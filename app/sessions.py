"""Sesiones de terminal (PTY) que la web muestra con xterm.js.

Cada sesión ejecuta un comando con un pseudo-terminal propio, guarda un histórico
limitado de la salida y lo reparte a todos los navegadores conectados. El proceso no
depende del WebSocket: cerrar la pestaña no interrumpe una actualización.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import signal
import struct
import subprocess
import termios
import time
import uuid
from dataclasses import dataclass, field

from . import host

BUFFER_LIMIT = 1024 * 1024  # bytes de histórico por sesión
CLIENT_QUEUE = 2000  # mensajes pendientes por navegador antes de desconectarlo
MAX_FINISHED = 20  # sesiones terminadas que se conservan


def _set_ctty() -> None:  # pragma: no cover - se ejecuta en el proceso hijo
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)


@dataclass
class Session:
    id: str
    title: str
    argv: list[str]
    container: str = ""  # contenedor auxiliar (si lo hay)
    reattached: bool = False
    created: float = field(default_factory=time.time)
    exit_code: int | None = None
    finished: float | None = None
    _fd: int = -1
    _proc: subprocess.Popen | None = None
    _buffer: bytearray = field(default_factory=bytearray)
    _clients: set[asyncio.Queue] = field(default_factory=set)

    @property
    def running(self) -> bool:
        return self.exit_code is None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "running": self.running,
            "exit_code": self.exit_code,
            "created": self.created,
            "finished": self.finished,
            "container": self.container,
            "reattached": self.reattached,
        }

    # ── ciclo de vida ──────────────────────────────────────────────
    def start(self, cols: int = 120, rows: int = 32) -> None:
        master, slave = pty.openpty()
        self._resize_fd(master, cols, rows)
        try:
            self._proc = subprocess.Popen(
                self.argv,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
                preexec_fn=_set_ctty,
                close_fds=True,
            )
        finally:
            os.close(slave)
        self._fd = master
        asyncio.get_running_loop().add_reader(master, self._on_readable)

    def _on_readable(self) -> None:
        try:
            data = os.read(self._fd, 65536)
        except OSError:
            data = b""
        if data:
            self._append(data)
            return
        loop = asyncio.get_running_loop()
        loop.remove_reader(self._fd)
        os.close(self._fd)
        self._fd = -1
        loop.create_task(self._finish())

    async def _finish(self) -> None:
        code = await asyncio.to_thread(self._proc.wait) if self._proc else 0
        self.exit_code = code
        self.finished = time.time()
        self._broadcast({"t": "exit", "code": code})

    def _append(self, data: bytes) -> None:
        self._buffer += data
        if len(self._buffer) > BUFFER_LIMIT:
            del self._buffer[: len(self._buffer) - BUFFER_LIMIT]
        self._broadcast(data)

    def _broadcast(self, item: bytes | dict) -> None:
        for q in list(self._clients):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                # Navegador demasiado lento: se vacía su cola y se le pide reconectar
                # (al volver recibe el histórico completo)
                self._clients.discard(q)
                while not q.empty():
                    q.get_nowait()
                q.put_nowait({"t": "drop"})

    # ── interacción ────────────────────────────────────────────────
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=CLIENT_QUEUE)
        if self._buffer:
            q.put_nowait(bytes(self._buffer))
        if not self.running:
            q.put_nowait({"t": "exit", "code": self.exit_code})
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    def is_subscribed(self, q: asyncio.Queue) -> bool:
        return q in self._clients

    def write(self, data: str) -> None:
        if self._fd >= 0 and data:
            os.write(self._fd, data.encode("utf-8", "replace"))

    def resize(self, cols: int, rows: int) -> None:
        if self._fd >= 0:
            self._resize_fd(self._fd, cols, rows)

    @staticmethod
    def _resize_fd(fd: int, cols: int, rows: int) -> None:
        cols = max(20, min(int(cols), 500))
        rows = max(5, min(int(rows), 200))
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    async def terminate(self) -> None:
        if not self.running:
            return
        if self.container:
            # Parar el auxiliar es lo que detiene update.sh; el cliente docker sale solo
            await asyncio.to_thread(host.stop_container, self.container)
        elif self._proc:
            try:
                os.killpg(self._proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass


class SessionManager:
    def __init__(self) -> None:
        self.sessions: dict[str, Session] = {}

    def list(self) -> list[dict]:
        return [s.to_dict() for s in sorted(self.sessions.values(), key=lambda s: s.created)]

    def get(self, sid: str) -> Session | None:
        return self.sessions.get(sid)

    def create(self, title: str, argv: list[str], container: str = "", sid: str | None = None,
               reattached: bool = False, cols: int = 120, rows: int = 32) -> Session:
        session = Session(
            id=sid or uuid.uuid4().hex[:10],
            title=title,
            argv=argv,
            container=container,
            reattached=reattached,
        )
        session.start(cols, rows)
        self.sessions[session.id] = session
        self._prune()
        return session

    def new_id(self) -> str:
        return uuid.uuid4().hex[:10]

    def remove(self, sid: str) -> bool:
        session = self.sessions.get(sid)
        if not session or session.running:
            return False
        del self.sessions[sid]
        return True

    def running_count(self) -> int:
        return sum(1 for s in self.sessions.values() if s.running)

    def _prune(self) -> None:
        finished = sorted(
            (s for s in self.sessions.values() if not s.running), key=lambda s: s.finished or 0
        )
        for s in finished[: max(0, len(finished) - MAX_FINISHED)]:
            del self.sessions[s.id]

"""Dockpanel: interfaz web para docker/update.sh (FastAPI + xterm.js)."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import host
from .actions import ActionError, build_plan, parse_check, parse_list
from .sessions import SessionManager

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
README_PATH = Path(__file__).resolve().parent.parent / "README.md"
VERSION = "1.0.0"

AUTH_USER = os.environ.get("AUTH_USER", "")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")
MAX_RUNNING = int(os.environ.get("MAX_RUNNING_JOBS", "6"))

manager = SessionManager()
state: dict = {
    "self_container": "",
    "self_image": "",
    "check": {},  # carpeta → update|ok|error
    "check_time": None,
    "check_running": False,
    "check_done": 0,  # progreso de la comprobación en curso
    "check_total": 0,
    "check_scope": "all",  # all | parcial (solo algunos servicios)
    "check_error": None,
    "check_queue": None,  # None | "all" | set de carpetas pendientes de recomprobar
    "jobs_scan": 0.0,
}
CHECK_TIMEOUT = 900
_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.get_running_loop().create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def run_check(services: list[str] | None = None) -> None:
    """Comprueba actualizaciones en segundo plano (todos o solo ``services``).

    update.sh --check-tsv escribe cada servicio en cuanto termina, así que el resultado y el
    progreso se actualizan sobre la marcha. Si ya hay una comprobación en curso, la petición
    se encola y se ejecuta al acabar.
    """
    if state["check_running"]:
        queued = state["check_queue"]
        if services is None or queued == "all":
            state["check_queue"] = "all"
        else:
            state["check_queue"] = set(queued or ()) | set(services)
        return
    state.update(check_running=True, check_done=0, check_error=None,
                 check_scope="all" if services is None else "partial")
    try:
        state["check_total"] = len(services) if services is not None else len(await _list_services())
        argv = host.on_host(host.update_sh_argv(["--check-tsv", *(services or [])]))
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        results: dict[str, str] = {}

        async def read_lines() -> None:
            async for raw in proc.stdout:
                for folder, status in parse_check(raw.decode("utf-8", "replace")).items():
                    if folder not in results:
                        state["check_done"] += 1
                    results[folder] = status
                    state["check"][folder] = status

        try:
            await asyncio.wait_for(read_lines(), CHECK_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            state["check_error"] = "La comprobación ha superado el tiempo máximo"
        stderr = (await proc.stderr.read()).decode("utf-8", "replace")
        rc = await proc.wait()
        if rc != 0 and not results and not state["check_error"]:
            state["check_error"] = stderr.strip()[-500:] or f"update.sh --check-tsv ha fallado ({rc})"
        if services is None and results:
            state["check"] = results  # olvida servicios que ya no existen
            state["check_time"] = time.time()
    except Exception as exc:  # noqa: BLE001 - se muestra en la web
        state["check_error"] = str(getattr(exc, "detail", exc))
    finally:
        state["check_running"] = False
        queued, state["check_queue"] = state["check_queue"], None
        if queued:
            _spawn(run_check(None if queued == "all" else sorted(queued)))


def _check_status() -> dict:
    return {
        "running": state["check_running"],
        "done": state["check_done"],
        "total": state["check_total"],
        "scope": state["check_scope"],
        "error": state["check_error"],
        "check": state["check"],
        "check_time": state["check_time"],
    }


def _on_session_finished(session) -> None:
    """Tras una tarea que ha podido cambiar imágenes (web, menú o SSH), se recomprueban los
    servicios que estaban marcados con actualización pendiente: así desaparece el aviso de los
    que ya se han actualizado y se mantiene el de los que han fallado."""
    if not session.container:
        return  # solo consultas
    pending = sorted(f for f, st in state["check"].items() if st == "update")
    if pending:
        _spawn(run_check(pending))


manager.on_finish = _on_session_finished
JOBS_SCAN_INTERVAL = 8  # s entre búsquedas de tareas lanzadas fuera de la web


async def _reattach_jobs() -> None:
    """Se engancha a los contenedores auxiliares vivos que el panel no tiene abiertos: los que
    quedaron de antes de reiniciarse y los lanzados por SSH con ``update.sh`` del contenedor."""
    state["jobs_scan"] = time.monotonic()
    try:
        jobs = await asyncio.to_thread(host.running_jobs)
    except Exception:  # noqa: BLE001 - el panel debe arrancar aunque Docker no responda
        return
    for name, title in jobs:
        sid = name[len(host.JOB_PREFIX):]
        if sid not in manager.sessions:
            label = title if title.startswith("SSH:") else f"{title} (reconectada)"
            manager.create(label, host.attach_argv(name), container=name,
                           sid=sid, reattached=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if host.DOCKER_DIR:
        name, image = await asyncio.to_thread(host.detect_self)
        state["self_container"] = name
        state["self_image"] = image or os.environ.get("HELPER_IMAGE", "")
        await _reattach_jobs()
    yield


app = FastAPI(title="Dockpanel", version=VERSION, lifespan=lifespan, docs_url=None, redoc_url=None)


# ── Seguridad ─────────────────────────────────────────────────────────
def _auth_ok(header: str | None) -> bool:
    if not (AUTH_USER and AUTH_PASSWORD):
        return True
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        user, _, pwd = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except Exception:  # noqa: BLE001
        return False
    return secrets.compare_digest(user, AUTH_USER) and secrets.compare_digest(pwd, AUTH_PASSWORD)


def _same_origin(origin: str | None, host_header: str | None) -> bool:
    if not origin:
        return True  # clientes no navegador (curl) no envían Origin
    return bool(host_header) and urlparse(origin).netloc == host_header


@app.middleware("http")
async def security(request: Request, call_next):
    if request.url.path != "/api/health" and not _auth_ok(request.headers.get("authorization")):
        return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="Dockpanel"'})
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        # Cabecera propia: obliga a una petición del propio panel (protección CSRF)
        if request.headers.get("x-dockpanel") != "1":
            return JSONResponse({"detail": "Petición no permitida"}, status_code=403)
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


# ── Utilidades ────────────────────────────────────────────────────────
def _require_docker_dir() -> None:
    if not host.DOCKER_DIR:
        raise HTTPException(500, "DOCKER_DIR no está definido en el contenedor")


async def _list_services() -> list[dict]:
    _require_docker_dir()
    res = await asyncio.to_thread(host.run_on_host, host.update_sh_argv(["--list"]), 60)
    if res.returncode != 0:
        detail = (res.stderr or res.stdout).strip()[-500:] or f"código {res.returncode}"
        raise HTTPException(502, f"update.sh --list ha fallado: {detail}")
    return parse_list(res.stdout)


# ── API ───────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health():
    return {"ok": True}


@app.get("/api/info")
async def info():
    return {
        "version": VERSION,
        "docker_dir": host.DOCKER_DIR,
        "self_container": state["self_container"],
        "helper_image": state["self_image"],
        "auth": bool(AUTH_USER and AUTH_PASSWORD),
    }


@app.get("/api/services")
async def services():
    items = await _list_services()
    for item in items:
        item["update"] = state["check"].get(item["folder"])
        item["is_self"] = bool(state["self_container"]) and item["container"] == state["self_container"]
    return {
        "services": items,
        "check_time": state["check_time"],
        "check_running": state["check_running"],
    }


@app.post("/api/check")
async def check():
    """Lanza la comprobación en segundo plano; el progreso se consulta con GET /api/check."""
    _require_docker_dir()
    if not state["check_running"]:
        _spawn(run_check())
        await asyncio.sleep(0)  # deja arrancar la tarea: la respuesta ya sale con running=True
    return _check_status()


@app.get("/api/check")
async def check_status():
    return _check_status()


@app.get("/api/sessions")
async def list_sessions():
    if host.DOCKER_DIR and time.monotonic() - state["jobs_scan"] > JOBS_SCAN_INTERVAL:
        await _reattach_jobs()
    return {"sessions": manager.list()}


@app.post("/api/sessions")
async def create_session(request: Request):
    _require_docker_dir()
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "JSON no válido") from exc
    if not isinstance(body, dict):
        raise HTTPException(400, "JSON no válido")

    needs_services = body.get("action") not in ("menu", "check", "stats", "help", "clean",
                                                 "restore", "new", "user_home", "update_all")
    names = {s["folder"]: s["name"] for s in await _list_services()} if needs_services else {}
    try:
        plan = build_plan(body, names)
    except ActionError as exc:
        raise HTTPException(400, str(exc)) from exc

    if manager.running_count() >= MAX_RUNNING:
        raise HTTPException(429, "Hay demasiadas tareas en marcha; cierra alguna antes")

    cols = body.get("cols") if isinstance(body.get("cols"), int) else 120
    rows = body.get("rows") if isinstance(body.get("rows"), int) else 32
    inner = host.script_argv(plan.script, plan.args) if plan.script else host.update_sh_argv(plan.args)

    sid = manager.new_id()
    container = ""
    if plan.modifies:
        if not state["self_image"]:
            raise HTTPException(500, "No se ha podido determinar la imagen del panel (define HELPER_IMAGE)")
        container = f"{host.JOB_PREFIX}{sid}"
        argv = host.job_container_argv(sid, plan.title, state["self_image"], inner)
    else:
        argv = host.on_host(inner)

    session = manager.create(plan.title, argv, container=container, sid=sid, cols=cols, rows=rows)
    return session.to_dict()


@app.delete("/api/sessions/{sid}")
async def delete_session(sid: str):
    session = manager.get(sid)
    if not session:
        raise HTTPException(404, "Sesión no encontrada")
    if session.running:
        await session.terminate()
        return {"terminating": True}
    manager.remove(sid)
    return {"removed": True}


@app.websocket("/api/sessions/{sid}/ws")
async def session_ws(ws: WebSocket, sid: str):
    if not _auth_ok(ws.headers.get("authorization")) or not _same_origin(
        ws.headers.get("origin"), ws.headers.get("host")
    ):
        await ws.close(code=4403)
        return
    session = manager.get(sid)
    if not session:
        await ws.close(code=4404)
        return
    await ws.accept()
    queue = session.subscribe()

    async def sender():
        while True:
            item = await queue.get()
            if isinstance(item, bytes):
                await ws.send_bytes(item)
            else:
                await ws.send_text(json.dumps(item))
                if item.get("t") == "drop":
                    await ws.close(code=4000)
                    return

    send_task = asyncio.create_task(sender())
    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("t") == "in" and isinstance(msg.get("d"), str):
                session.write(msg["d"])
            elif msg.get("t") == "resize":
                try:
                    session.resize(int(msg.get("c", 120)), int(msg.get("r", 32)))
                except (TypeError, ValueError):
                    pass
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        session.unsubscribe(queue)
        send_task.cancel()


@app.get("/api/readme")
async def readme():
    if not README_PATH.exists():
        raise HTTPException(404, "README no disponible")
    return {"content": README_PATH.read_text(encoding="utf-8")}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})

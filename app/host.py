"""Construcción de comandos que se ejecutan en el host.

El panel no lleva Docker dentro: monta la raíz del host en HOST_ROOT (/host) y ejecuta
todo con ``chroot /host``, de modo que update.sh usa el bash, el docker CLI y las rutas
reales del host (volúmenes, backups y stacks compose resuelven igual que por SSH).

Las tareas que modifican algo se lanzan dentro de un contenedor auxiliar efímero
(``docker run --rm -it``) creado con la misma imagen del panel. Así, si update.sh
recrea o actualiza el propio panel, la tarea sigue viva hasta terminar y se puede
volver a conectar a ella al recargar la página.
"""

from __future__ import annotations

import os
import re
import subprocess

HOST_ROOT = os.environ.get("HOST_ROOT", "/host")
DOCKER_DIR = os.environ.get("DOCKER_DIR", "").rstrip("/")
HOST_HOME = os.environ.get("HOST_HOME", "/root")
HOST_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

JOB_LABEL = "dockpanel.job"
JOB_TITLE_LABEL = "dockpanel.title"
JOB_PREFIX = "dockpanel-job-"

# Variables de update.sh que se pueden fijar en el contenedor del panel y se reenvían
PASSTHROUGH_VARS = (
    "USER_HOME",
    "BACKUP_DIR",
    "BACKUP_RETENTION_DAYS",
    "BACKUP_KEEP_MIN",
    "HEALTH_WAIT",
    "HEALTH_TIMEOUT",
    "HEALTH_POLL",
)

SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def update_sh_path() -> str:
    return f"{DOCKER_DIR}/update.sh"


def host_env() -> list[str]:
    """Prefijo ``env -i ...`` con un entorno limpio y predecible para el host."""
    env = [
        "env",
        "-i",
        f"HOME={HOST_HOME}",
        f"PATH={HOST_PATH}",
        "TERM=xterm-256color",
        "COLORTERM=truecolor",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
    ]
    for var in PASSTHROUGH_VARS:
        value = os.environ.get(var)
        if value:
            env.append(f"{var}={value}")
    return env


def on_host(argv: list[str]) -> list[str]:
    """Comando ejecutado en el host a través de chroot."""
    return ["chroot", HOST_ROOT, *host_env(), *argv]


def update_sh_argv(args: list[str]) -> list[str]:
    return ["/bin/bash", update_sh_path(), *args]


def script_argv(script: str, args: list[str]) -> list[str]:
    """Script bash con argumentos posicionales ($1 = update.sh, $2.. = args); sin interpolar."""
    return ["/bin/bash", "-c", script, "dockpanel", update_sh_path(), *args]


def run_on_host(argv: list[str], timeout: float = 60) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        on_host(argv),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def job_container_argv(job_id: str, title: str, image: str, inner: list[str],
                       tty: bool = True) -> list[str]:
    """``docker run -it`` del contenedor auxiliar que ejecuta ``inner`` en el host.

    Sin terminal (cron, ``docker exec`` sin ``-t``) se usa solo ``-i``.
    """
    return on_host(
        [
            "docker",
            "run",
            "--rm",
            "-it" if tty else "-i",
            "--name",
            f"{JOB_PREFIX}{job_id}",
            "--label",
            f"{JOB_LABEL}=1",
            "--label",
            f"{JOB_TITLE_LABEL}={title}",
            # Red del host: DNS (p. ej. systemd-resolved en 127.0.0.53) y registros igual que por SSH
            "--network",
            "host",
            "-v",
            "/:/host",
            "--entrypoint",
            "chroot",
            image,
            HOST_ROOT,
            *host_env(),
            *inner,
        ]
    )


def attach_argv(container: str) -> list[str]:
    """Reengancha un auxiliar que seguía en marcha: muestra su historial y se adjunta."""
    return on_host(
        [
            "sh",
            "-c",
            'docker logs --tail 400 "$1" 2>&1; exec docker attach "$1"',
            "dockpanel",
            container,
        ]
    )


def stop_container(container: str) -> None:
    run_on_host(["docker", "stop", "-t", "10", container], timeout=30)


def running_jobs() -> list[tuple[str, str]]:
    """(nombre, título) de los contenedores auxiliares en marcha."""
    res = run_on_host(
        [
            "docker",
            "ps",
            "--filter",
            f"label={JOB_LABEL}=1",
            "--format",
            '{{.Names}}\t{{.Label "' + JOB_TITLE_LABEL + '"}}',
        ],
        timeout=20,
    )
    jobs = []
    for line in res.stdout.splitlines():
        name, _, title = line.partition("\t")
        if name.startswith(JOB_PREFIX):
            jobs.append((name, title or name))
    return jobs


def detect_self() -> tuple[str, str]:
    """(nombre del contenedor del panel, ID de su imagen); vacíos si no se puede saber."""
    # HOSTNAME es el ID corto del contenedor salvo con --network host; entonces se usa el nombre
    candidates = [os.environ.get("HOSTNAME", ""), os.environ.get("SELF_CONTAINER", "dockpanel")]
    for cand in candidates:
        if not cand:
            continue
        res = run_on_host(
            ["docker", "inspect", "-f", "{{.Name}}\t{{.Image}}", cand], timeout=15
        )
        if res.returncode == 0 and "\t" in res.stdout:
            name, _, image = res.stdout.strip().partition("\t")
            return name.lstrip("/"), image
    return "", ""

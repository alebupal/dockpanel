"""Traducción de las acciones de la web a argumentos de update.sh (lista blanca)."""

from __future__ import annotations

from dataclasses import dataclass

from .host import SERVICE_RE

POLICIES = ("unless-stopped", "always", "on-failure", "no")
LIFECYCLE = ("start", "stop", "restart", "recreate")

# Actualiza solo los servicios indicados (los que tienen versión nueva) y limpia imágenes al final,
# igual que la subopción 'u' tras 'c' en el menú interactivo. $1 = update.sh, $2.. = servicios.
UPDATE_LIST_SCRIPT = """u="$1"; shift; rc=0
for s in "$@"; do /bin/bash "$u" "$s" -f || rc=1; done
echo "🧹 Limpiando imágenes antiguas..."; docker image prune -f
exit $rc"""


class ActionError(ValueError):
    """Petición no válida (se devuelve como 400)."""


@dataclass
class Plan:
    title: str
    args: list[str]
    script: str | None = None  # si se indica, se ejecuta con bash -c y args como posicionales
    modifies: bool = True  # False = solo consulta (no hace falta contenedor auxiliar)


def _service(value: object, known: set[str], allow_all: bool = False) -> str:
    if allow_all and value == "all":
        return "all"
    if not isinstance(value, str) or not SERVICE_RE.match(value):
        raise ActionError("Servicio no válido")
    if value not in known:
        raise ActionError(f"Servicio desconocido: {value}")
    return value


def _label(target: str, names: dict[str, str]) -> str:
    return "todos" if target == "all" else names.get(target, target)


def build_plan(req: dict, names: dict[str, str]) -> Plan:
    """``names``: carpeta → nombre descriptivo de los servicios actuales."""
    action = req.get("action")
    known = set(names)
    force = bool(req.get("force"))

    if action == "menu":
        return Plan("Menú interactivo", [])
    if action == "update":
        svc = _service(req.get("service"), known)
        return Plan(
            f"Actualizar {_label(svc, names)}" + (" (forzado)" if force else ""),
            [svc, "-f"] if force else [svc],
        )
    if action == "update_all":
        return Plan("Actualizar todos" + (" (forzado)" if force else ""), ["-a", "-f"] if force else ["-a"])
    if action == "update_list":
        services = req.get("services")
        if not isinstance(services, list) or not services:
            raise ActionError("No hay servicios que actualizar")
        svcs = [_service(s, known) for s in services]
        return Plan(f"Actualizar disponibles ({len(svcs)})", svcs, script=UPDATE_LIST_SCRIPT)
    if action in LIFECYCLE:
        svc = _service(req.get("service"), known, allow_all=True)
        verbs = {"start": "Iniciar", "stop": "Detener", "restart": "Reiniciar", "recreate": "Recrear"}
        return Plan(f"{verbs[action]} {_label(svc, names)}", [f"--{action}", svc])
    if action == "policy":
        svc = _service(req.get("service"), known, allow_all=True)
        policy = req.get("policy")
        if policy not in POLICIES:
            raise ActionError("Política de reinicio no válida")
        return Plan(f"Política {policy} → {_label(svc, names)}", ["--restart-policy", svc, policy])
    if action == "logs":
        svc = _service(req.get("service"), known)
        return Plan(f"Logs {_label(svc, names)}", ["--logs", svc], modifies=False)
    if action == "backup":
        svc = _service(req.get("service"), known, allow_all=True)
        return Plan(f"Backup {_label(svc, names)}", ["--backup", svc])
    if action == "new":
        run_cmd = req.get("run_cmd") or ""
        if not isinstance(run_cmd, str) or len(run_cmd) > 4000 or "\n" in run_cmd or "\0" in run_cmd:
            raise ActionError("Comando docker run no válido")
        run_cmd = run_cmd.strip()
        return Plan("Nuevo servicio", ["--new", run_cmd] if run_cmd else ["--new"])
    if action == "user_home":
        path = req.get("path")
        if not isinstance(path, str) or not path.startswith("/") or any(c in path for c in "\n\r\0"):
            raise ActionError("La ruta debe ser absoluta")
        return Plan("Ruta base de usuario", ["--set-user-home", path])

    simple = {
        "check": Plan("Comprobar actualizaciones", ["--check"], modifies=False),
        "stats": Plan("Monitor de recursos", ["--stats"], modifies=False),
        "show": Plan("Comandos de arranque", ["--show"], modifies=False),
        "help": Plan("Ayuda de update.sh", ["--help"], modifies=False),
        "clean": Plan("Limpieza y mantenimiento", ["--clean"]),
        "restore": Plan("Restaurar backup", ["--restore"]),
    }
    if action in simple:
        return simple[action]
    raise ActionError("Acción desconocida")


def parse_list(tsv: str) -> list[dict]:
    """Interpreta la salida de ``update.sh --list``."""
    keys = ("folder", "name", "mode", "port", "status", "image", "container", "backup")
    services = []
    for line in tsv.splitlines():
        parts = line.split("\t")
        if len(parts) < len(keys) or not SERVICE_RE.match(parts[0]):
            continue
        item = dict(zip(keys, parts[: len(keys)]))
        item["port"] = item["port"] if item["port"].isdigit() else ""
        item["backup"] = item["backup"] == "true"
        services.append(item)
    return services


def parse_check(tsv: str) -> dict[str, str]:
    """Interpreta la salida de ``update.sh --check-tsv``."""
    result = {}
    for line in tsv.splitlines():
        folder, _, status = line.partition("\t")
        if SERVICE_RE.match(folder) and status in ("update", "ok", "error"):
            result[folder] = status
    return result

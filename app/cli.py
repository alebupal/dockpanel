"""Comando ``update.sh`` dentro del contenedor: ejecuta el update.sh del host.

    docker exec -it dockpanel update.sh            # menú interactivo
    docker exec -it dockpanel update.sh -c         # cualquier opción de update.sh
    docker exec dockpanel update.sh -a             # desde cron (sin -t)

Las consultas se ejecutan directamente con ``chroot /host``; lo que modifica algo va en
un contenedor auxiliar efímero (igual que la web), para que actualizar o recrear el propio
panel no corte la ejecución. Esas tareas aparecen además en la web si se reinicia el panel.
"""

from __future__ import annotations

import os
import sys
import uuid

from . import host

# Opciones de update.sh que solo consultan (no paran ni recrean nada)
READ_ONLY = {
    "--help", "-?", "help", "--list", "--check-tsv", "--check", "-c",
    "--show", "-s", "--stats", "-m", "--logs", "-l",
}


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not host.DOCKER_DIR:
        print("❌ DOCKER_DIR no está definido en el contenedor.", file=sys.stderr)
        return 1

    inner = host.update_sh_argv(args)
    if args and args[0] in READ_ONLY:
        cmd = host.on_host(inner)
    else:
        _, image = host.detect_self()
        image = image or os.environ.get("HELPER_IMAGE", "")
        if not image:
            print("⚠️ No se pudo detectar la imagen del panel (HELPER_IMAGE); se ejecuta sin "
                  "contenedor auxiliar.", file=sys.stderr)
            cmd = host.on_host(inner)
        else:
            title = "SSH: update.sh" + (" " + " ".join(args) if args else "")
            tty = sys.stdin.isatty() and sys.stdout.isatty()
            cmd = host.job_container_argv(uuid.uuid4().hex[:10], title[:120], image, inner, tty=tty)

    os.execvp(cmd[0], cmd)
    return 127  # pragma: no cover - execvp no vuelve


if __name__ == "__main__":
    sys.exit(main())

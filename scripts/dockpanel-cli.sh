#!/bin/sh
# Ejecuta el update.sh del host desde dentro del contenedor (ver app/cli.py)
cd /app && exec python3 -m app.cli "$@"

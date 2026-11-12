# 🐳 Dockpanel (interfaz web de `update.sh`)

Panel web sencillo para gestionar los servicios de `docker/` con el **mismo `update.sh`** que se usa por SSH. No reimplementa nada: cada botón lanza `update.sh` con sus opciones de línea de comandos y muestra la salida en una **terminal real en el navegador** (xterm.js), así que las preguntas del script (s/N, elegir backup, asistente de nuevo servicio…) se responden ahí mismo.

> **Sin copias de `update.sh`:** este repositorio no incluye el script. El panel ejecuta siempre en vivo el `update.sh` del repositorio `docker` montado en el host (`DOCKER_DIR`), así que nunca hay dos versiones que sincronizar.
>
> **Regla de mantenimiento:** la lógica vive solo en `docker/update.sh`. Si se añade una opción nueva al script, en el panel basta con añadir su botón y su entrada en `app/actions.py`.

---

## Qué hace

| Zona | Acción | Equivale a |
|------|--------|-----------|
| Tabla de servicios | Estado (en marcha / parado / sin crear), modo, puerto con enlace y aviso de actualización | `./update.sh --list` |
| Fila de un servicio | Iniciar, detener, reiniciar, recrear | `--start/--stop/--restart/--recreate <svc>` |
| | Actualizar (y forzando recreación) | `./update.sh <svc> [-f]` |
| | Logs en vivo | `-l <svc>` |
| | Backup, política de reinicio, comando de arranque | `-k <svc>`, `--restart-policy <svc> <pol>`, `-s <svc>` |
| 🚀 Actualizaciones | Comprobar con barra de progreso (marca los servicios con imagen nueva). Al terminar cualquier tarea que modifica algo (web, menú o SSH) se recomprueban solos los que estaban pendientes | `--check-tsv [svc...]` (= `-c`) |
| | Actualizar disponibles | `u` tras `c` en el menú (cada uno con `-f` y `image prune` al final) |
| | Actualizar todos (opción forzar) | `-a [-f]` |
| ⏯️ Lote | Casillas por servicio (o clic en la fila), «Seleccionar todos» y casilla de cabecera (respeta el filtro). Iniciar, reiniciar, recrear, detener, actualizar, backup, política y comando de arranque **sobre los seleccionados**; sin selección, sobre todos | por servicio: `update.sh --start <svc>`…; sin selección: `--start all`, `-a [-f]`, `-k all`… |
| 🛠️ Herramientas | Recursos, nuevo servicio, restaurar, limpieza, ruta base, ayuda | `-m`, `-n [cmd]`, `-r`, `-b`, `--set-user-home`, `--help` |
| Cabecera | ⌨️ Menú interactivo | `./update.sh` sin argumentos (el menú completo) |

Cada acción abre una pestaña de terminal. Debajo hay una barra para responder (campo de texto + teclas rápidas ⏎, s, n, q, Ctrl+C, ↑, ↓ e ir al final).

**Móvil:** la terminal se abre a pantalla completa al lanzar una acción y se minimiza a un botón flotante «🖥️ Terminal»; el historial se desplaza deslizando el dedo (con inercia) y se responde con la barra, sin abrir el teclado sobre la salida. Los grupos de botones se desplazan en horizontal y el menú ⋯ de cada servicio aparece como hoja inferior.

 Las tareas **no dependen del navegador**: si cierras la página siguen en marcha y al volver aparecen otra vez con su salida. Cerrar con × una tarea en curso la interrumpe (se pide confirmación).

## Uso por SSH dentro del contenedor

El contenedor incluye el comando `update.sh` (alias `dockpanel`), que ejecuta el `update.sh` del host con las mismas opciones:

```bash
docker exec -it dockpanel update.sh              # menú interactivo completo
docker exec -it dockpanel update.sh -c           # comprobar actualizaciones
docker exec -it dockpanel update.sh portainer    # actualizar un servicio
docker exec dockpanel update.sh -a               # desde cron (sin -t)
```

Igual que en la web, las consultas (`-c`, `-l`, `-m`, `-s`, `--list`, `--help`…) se ejecutan directamente y lo que modifica algo va en un contenedor auxiliar, así que `docker exec -it dockpanel update.sh dockpanel` actualiza el propio panel sin cortarse. Esas tareas aparecen también en la web (pestaña «SSH: update.sh …»), donde se pueden seguir y responder.

## Cómo funciona

```text
Navegador ──WebSocket──► Dockpanel (FastAPI, PTY)
                              │ chroot /host
                              ▼
                 docker run --rm -it dockpanel-job-<id>   (contenedor auxiliar efímero)
                              │ chroot /host
                              ▼
                 bash <DOCKER_DIR>/update.sh <opciones>    (bash, docker y rutas del host)
```

- **Sin Docker dentro de la imagen.** El contenedor monta la raíz del host en `/host` y ejecuta todo con `chroot /host`: usa el `bash`, el `docker` CLI (con `compose` y `buildx`) y las rutas reales del host. Volúmenes, backups, `.env` y stacks compose se resuelven exactamente igual que por SSH. La imagen solo lleva Python.
- **Contenedor auxiliar para lo que modifica algo.** Las acciones que cambian servicios se ejecutan en un contenedor efímero (`dockpanel-job-<id>`, misma imagen que el panel, `--rm`). Así el panel se puede **actualizar o recrear a sí mismo**: aunque `update.sh` pare el contenedor del panel, la tarea sigue en el auxiliar hasta terminar (incluida la comprobación de salud y el rollback). Al arrancar, el panel se reengancha a los auxiliares que sigan vivos.
- **Consultas directas.** Logs, recursos, comandos, ayuda y la comprobación de actualizaciones se ejecutan directamente (no modifican nada).
- **Estado sin duplicar lógica.** La tabla sale de `update.sh --list` y la comprobación de `update.sh --check-tsv` (salidas en TSV añadidas al script para el panel).

## Puesta en marcha

La configuración está en `docker/dockpanel/config.sh` (`MODE="run"`), así que se gestiona como cualquier otro servicio:

```bash
cd /ruta/a/docker
./update.sh dockpanel
```

Comando equivalente:

```bash
docker run -d --name dockpanel --restart unless-stopped \
  -p 8092:8092 \
  -v /:/host \
  -e TZ=Europe/Madrid \
  -e DOCKER_DIR=/ruta/a/docker \
  -e SELF_CONTAINER=dockpanel \
  alebupal/dockpanel:latest
```

Abre `http://<servidor>:8092`.

### Variables de entorno

| Variable | Por defecto | Descripción |
|----------|-------------|-------------|
| `DOCKER_DIR` | — (obligatoria) | Carpeta del host donde está `update.sh`. En `config.sh` se usa `${BASE_DIR}`, que la rellena sola. |
| `PORT` | `8092` | Puerto de la web. |
| `SELF_CONTAINER` | `dockpanel` | Nombre del contenedor del panel (para detectar su imagen y marcarlo como «este panel»). |
| `HELPER_IMAGE` | imagen del propio contenedor | Imagen para los auxiliares si no se puede detectar. |
| `AUTH_USER` / `AUTH_PASSWORD` | vacías | Activan autenticación HTTP Basic en toda la web. |
| `HOST_HOME` | `/root` | `HOME` con el que se ejecuta `update.sh` en el host. |
| `MAX_RUNNING_JOBS` | `6` | Tareas simultáneas máximas. |
| `USER_HOME`, `BACKUP_DIR`, `BACKUP_RETENTION_DAYS`, `BACKUP_KEEP_MIN`, `HEALTH_WAIT`, `HEALTH_TIMEOUT`, `HEALTH_POLL` | — | Si se definen en el panel, se pasan tal cual a `update.sh`. |

`update.sh` toma la ruta base de usuario de `docker/.user_home` (opción **⚙️ Ruta base** o `--set-user-home`). Si no existe, usa `HOME`, que dentro del panel es `/root`: conviene fijarla una vez.

## Seguridad

El panel tiene **control total del host** (igual que quien tiene acceso al socket de Docker): puede ejecutar `update.sh`, crear contenedores y leer cualquier ruta. Por eso:

- **No lo publiques sin autenticación.** Ponlo detrás de Nginx Proxy Manager + Tinyauth, o define `AUTH_USER` y `AUTH_PASSWORD` en `docker/dockpanel/.env` (no se versiona) y añade `-e AUTH_USER -e AUTH_PASSWORD` a `EXTRA_ARGS`.
- Las peticiones que modifican algo exigen la cabecera `X-Dockpanel: 1` (protección CSRF) y los WebSocket deben venir del mismo origen.
- Las acciones van por lista blanca (`app/actions.py`): los servicios se validan contra `update.sh --list` y los argumentos se pasan sin shell.

## Estructura

```text
dockpanel/
├── Dockerfile            # python:3.12-slim + FastAPI/Uvicorn (sin Docker dentro)
├── app/
│   ├── main.py           # API REST, WebSocket de terminales, seguridad y estáticos
│   ├── actions.py        # Acción web → argumentos de update.sh (lista blanca) y parseo del TSV
│   ├── host.py           # chroot /host, contenedores auxiliares y reenganche
│   ├── cli.py            # Comando 'update.sh' dentro del contenedor (scripts/dockpanel-cli.sh)
│   └── sessions.py       # Sesiones PTY con histórico y varios espectadores
├── scripts/
│   └── dockpanel-cli.sh  # Envoltorio instalado como 'update.sh' y 'dockpanel' en el contenedor
├── static/               # index.html, style.css, app.js y xterm.js (vendor/)
├── tests/test_core.py    # Tests unitarios (unittest)
└── .github/workflows/    # CI (tests) y publicación en Docker Hub (amd64 + arm64)
```

## API

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/api/health` | Salud (sin autenticación). |
| GET | `/api/info` | Versión, `DOCKER_DIR`, contenedor e imagen del panel. |
| GET | `/api/services` | Servicios (`update.sh --list`) con el último resultado de la comprobación. |
| POST | `/api/check` | Lanza la comprobación en segundo plano (`update.sh --check-tsv`). |
| GET | `/api/check` | Progreso y resultados: `running`, `done`, `total`, `scope` (`all`/`partial`), `error`, `check`. |
| GET | `/api/sessions` | Tareas abiertas. |
| POST | `/api/sessions` | Lanza una acción: `{"action": "update", "service": "portainer", "force": false}`. |
| DELETE | `/api/sessions/{id}` | Interrumpe una tarea en curso o cierra una terminada. |
| WS | `/api/sessions/{id}/ws` | Terminal: binario = salida; texto JSON `{"t":"in","d":…}` / `{"t":"resize","c":…,"r":…}`. |

Acciones: `menu`, `update`, `update_all`, `update_list`, `batch` (`{"op": "restart", "services": [...]}`; `op`: start, stop, restart, recreate, update, backup, policy), `start`, `stop`, `restart`, `recreate`, `policy`, `logs`, `backup`, `check`, `stats`, `show` (`{"services": [...]}` opcional; sin servicios = todos), `clean`, `restore`, `new`, `user_home`, `help`.

## Desarrollo y tests

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
docker build -t alebupal/dockpanel:dev .
```

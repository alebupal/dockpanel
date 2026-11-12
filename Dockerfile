FROM python:3.12-slim

# El panel no incluye Docker: ejecuta update.sh y el docker CLI del host mediante
# 'chroot /host' (la raíz del host montada en /host). Solo necesita Python y coreutils.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOST_ROOT=/host \
    PORT=8092 \
    BIND_ADDRESS=0.0.0.0

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY static/ ./static/
COPY scripts/ ./scripts/
COPY README.md ./README.md
# 'update.sh' (y alias 'dockpanel') dentro del contenedor = update.sh del host
RUN chmod +x /app/scripts/dockpanel-cli.sh \
    && ln -sf /app/scripts/dockpanel-cli.sh /usr/local/bin/update.sh \
    && ln -sf /app/scripts/dockpanel-cli.sh /usr/local/bin/dockpanel

EXPOSE 8092

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python3 -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT','8092'), timeout=4)" || exit 1

CMD ["sh", "-c", "exec python3 -m uvicorn app.main:app --host ${BIND_ADDRESS:-0.0.0.0} --port ${PORT:-8092} --no-access-log"]

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_HOME=/app \
    RNS_CONFIG_DIR=/data/rns \
    LXMD_CONFIG_DIR=/data/lxmd \
    APP_DATA_DIR=/data/app \
    WEB_PORT=8080 \
    RNS_SERVER_PORT=4242

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY app /app/app
COPY config /app/config
COPY scripts /app/scripts

RUN chmod +x /app/scripts/*.sh /app/scripts/*.py

EXPOSE 8080 4242

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/scripts/container-entrypoint.sh"]

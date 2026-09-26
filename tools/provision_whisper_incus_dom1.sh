#!/usr/bin/env bash
set -Eeuo pipefail

CONTAINER="${CONTAINER:-whisper-speaches-incus}"
IMAGE="${IMAGE:-local:ubuntu-24.04-official}"
HOST_PORT="${HOST_PORT:-8094}"
APP_PORT="${APP_PORT:-8000}"
OLD_DOCKER="${OLD_DOCKER:-whisper-speaches}"
NGINX_CONF="${NGINX_CONF:-/etc/nginx/sites-available/006-whisper.conf}"
PUBLIC_URL="${PUBLIC_URL:-https://whisper.inovacaosistemas.com.br}"
COPY_ROOT="${COPY_ROOT:-/var/tmp/whisper-docker-to-incus}"

log() {
  printf '[%s] %s\n' "$(date -Is)" "$*"
}

need_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo/root on dom1" >&2
    exit 2
  fi
}

wait_container() {
  local tries=60
  while [ "$tries" -gt 0 ]; do
    if incus exec "$CONTAINER" -- true >/dev/null 2>&1; then
      return 0
    fi
    tries=$((tries - 1))
    sleep 1
  done
  echo "Container did not become reachable: $CONTAINER" >&2
  return 1
}

wait_http() {
  local url="$1"
  local tries="${2:-60}"
  while [ "$tries" -gt 0 ]; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    tries=$((tries - 1))
    sleep 2
  done
  echo "HTTP check failed: $url" >&2
  return 1
}

need_root

log "Preflight"
command -v incus >/dev/null
command -v docker >/dev/null
command -v curl >/dev/null
test -f "$NGINX_CONF"
docker inspect "$OLD_DOCKER" >/dev/null
curl -fsS http://127.0.0.1:8093/health >/dev/null

log "Create or reuse Incus container $CONTAINER"
if ! incus info "$CONTAINER" >/dev/null 2>&1; then
  incus launch "$IMAGE" "$CONTAINER" -c limits.cpu=8 -c limits.memory=24GiB </dev/null
fi
wait_container

log "Ensure IPv4/DNS inside container"
incus exec "$CONTAINER" -- bash -lc '
set -Eeuo pipefail
if ! ip -4 addr show dev eth0 | grep -q "inet "; then
  mkdir -p /etc/netplan
  cat >/etc/netplan/10-eth0.yaml <<EOF
network:
  version: 2
  ethernets:
    eth0:
      dhcp4: true
      dhcp6: false
EOF
  chmod 600 /etc/netplan/10-eth0.yaml
  netplan generate
  netplan apply
fi
'

log "Install system packages in Incus"
incus exec "$CONTAINER" -- bash -lc '
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl ffmpeg libgomp1 libsndfile1 libstdc++6 python3 tini
'

log "Copy working Speaches runtime and model cache from Docker"
rm -rf "$COPY_ROOT"
mkdir -p "$COPY_ROOT"
docker cp "$OLD_DOCKER:/home/ubuntu/speaches" "$COPY_ROOT/speaches"
if docker exec "$OLD_DOCKER" test -d /home/ubuntu/.local/share/uv/python; then
  mkdir -p "$COPY_ROOT/uv"
  docker cp "$OLD_DOCKER:/home/ubuntu/.local/share/uv/python" "$COPY_ROOT/uv/python"
fi
if docker exec "$OLD_DOCKER" test -d /home/ubuntu/.cache/huggingface; then
  mkdir -p "$COPY_ROOT/cache"
  docker cp "$OLD_DOCKER:/home/ubuntu/.cache/huggingface" "$COPY_ROOT/cache/huggingface"
fi

incus exec "$CONTAINER" -- bash -lc '
set -Eeuo pipefail
id ubuntu >/dev/null 2>&1 || useradd -m -s /bin/bash ubuntu
rm -rf /home/ubuntu/speaches /home/ubuntu/.cache/huggingface /home/ubuntu/.local/share/uv/python
mkdir -p /home/ubuntu/.cache /home/ubuntu/.local/share/uv
'
incus file push -r "$COPY_ROOT/speaches" "$CONTAINER/home/ubuntu/"
if [ -d "$COPY_ROOT/uv/python" ]; then
  incus file push -r "$COPY_ROOT/uv/python" "$CONTAINER/home/ubuntu/.local/share/uv/"
fi
if [ -d "$COPY_ROOT/cache/huggingface" ]; then
  incus file push -r "$COPY_ROOT/cache/huggingface" "$CONTAINER/home/ubuntu/.cache/"
fi
incus exec "$CONTAINER" -- chown -R ubuntu:ubuntu /home/ubuntu/speaches /home/ubuntu/.cache /home/ubuntu/.local

log "Install systemd unit in Incus"
incus exec "$CONTAINER" -- bash -lc "cat >/etc/systemd/system/whisper-speaches.service" <<'UNIT'
[Unit]
Description=Speaches Whisper API
After=network-online.target
Wants=network-online.target

[Service]
User=ubuntu
Group=ubuntu
WorkingDirectory=/home/ubuntu/speaches
Environment=PATH=/home/ubuntu/speaches/.venv/bin:/home/ubuntu/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
Environment=HOME=/home/ubuntu
Environment=UVICORN_HOST=0.0.0.0
Environment=UVICORN_PORT=8000
Environment=WHISPER__COMPUTE_TYPE=int8
Environment=WHISPER__CPU_THREADS=8
Environment=WHISPER__NUM_WORKERS=1
Environment=HF_HUB_ENABLE_HF_TRANSFER=0
Environment=DO_NOT_TRACK=1
Environment=GRADIO_ANALYTICS_ENABLED=False
Environment=DISABLE_TELEMETRY=1
Environment=HF_HUB_DISABLE_TELEMETRY=1
ExecStart=/home/ubuntu/speaches/.venv/bin/python -m uvicorn --factory speaches.main:create_app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT

incus exec "$CONTAINER" -- systemctl daemon-reload
incus exec "$CONTAINER" -- systemctl enable --now whisper-speaches.service

log "Configure Incus proxy device on 127.0.0.1:$HOST_PORT"
if incus config device show "$CONTAINER" | grep -q '^webproxy:'; then
  incus config device set "$CONTAINER" webproxy listen "tcp:127.0.0.1:$HOST_PORT"
  incus config device set "$CONTAINER" webproxy connect "tcp:127.0.0.1:$APP_PORT"
else
  incus config device add "$CONTAINER" webproxy proxy \
    listen="tcp:127.0.0.1:$HOST_PORT" \
    connect="tcp:127.0.0.1:$APP_PORT" \
    bind=host
fi

log "Validate Incus service"
wait_http "http://127.0.0.1:$HOST_PORT/health" 90
curl -fsS "http://127.0.0.1:$HOST_PORT/v1/models" >/dev/null

log "Switch nginx upstream from 8093 to $HOST_PORT"
cp -a "$NGINX_CONF" "$NGINX_CONF.bak-$(date +%Y%m%d%H%M%S)"
python3 - "$NGINX_CONF" "$HOST_PORT" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
port = sys.argv[2]
text = path.read_text()
old = "proxy_pass http://127.0.0.1:8093;"
new = f"proxy_pass http://127.0.0.1:{port};"
if new in text:
    raise SystemExit(0)
if old not in text:
    raise SystemExit(f"did not find expected nginx upstream: {old}")
path.write_text(text.replace(old, new, 1))
PY
nginx -t
service nginx reload

log "Validate public service"
wait_http "$PUBLIC_URL/health" 60
curl -fsS "$PUBLIC_URL/v1/models" >/dev/null

log "Stop old Docker service after successful public validation"
docker stop "$OLD_DOCKER" >/dev/null

log "Final state"
incus list "$CONTAINER"
docker ps -a --filter "name=$OLD_DOCKER" --format "docker {{.Names}} {{.Status}}"
curl -fsS "$PUBLIC_URL/health"
printf '\n'

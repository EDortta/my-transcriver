#!/usr/bin/env bash
set -Eeuo pipefail

SSH_TARGET="${SSH_TARGET:-whisper-dom1}"
CONTAINER="${CONTAINER:-whisper-speaches-incus}"
SERVICE="${SERVICE:-whisper-speaches.service}"
PUBLIC_URL="${PUBLIC_URL:-https://whisper.inovacaosistemas.com.br}"
RESTART_SERVICE="${RESTART_SERVICE:-0}"

APT_PACKAGES=(
  ca-certificates
  curl
  ffmpeg
  libgomp1
  libsndfile1
  libstdc++6
  python3
  tini
)

PYTHON_MODULES=(
  faster_whisper
  speaches
  uvicorn
)

log() {
  printf '[%s] %s\n' "$(date -Is)" "$*"
}

remote() {
  ssh -o BatchMode=yes -o ConnectTimeout=10 "$SSH_TARGET" "$@"
}

log "Checking host access: $SSH_TARGET"
remote 'hostname; whoami; command -v incus >/dev/null'

log "Checking Incus container: $CONTAINER"
remote "sudo incus info '$CONTAINER' >/dev/null"

log "Checking and installing system packages inside Incus"
packages_quoted=""
for pkg in "${APT_PACKAGES[@]}"; do
  packages_quoted+=" $(printf '%q' "$pkg")"
done

remote "sudo incus exec '$CONTAINER' -- bash -lc '
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
missing=()
for pkg in$packages_quoted; do
  if ! dpkg-query -s \"\$pkg\" 2>/dev/null | grep -q \"^Status: install ok installed\"; then
    missing+=(\"\$pkg\")
  fi
done
if [ \"\${#missing[@]}\" -gt 0 ]; then
  echo \"Installing missing apt packages: \${missing[*]}\"
  apt-get update
  apt-get install -y --no-install-recommends \"\${missing[@]}\"
else
  echo \"All apt packages present.\"
fi
'"

log "Checking Speaches runtime and Python modules"
modules_py="$(printf "%s\n" "${PYTHON_MODULES[@]}")"
remote "sudo incus exec '$CONTAINER' -- bash -lc '
set -Eeuo pipefail
test -x /home/ubuntu/speaches/.venv/bin/python
test -x /home/ubuntu/speaches/.venv/bin/python3 || true
while read -r module; do
  [ -n \"\$module\" ] || continue
  /home/ubuntu/speaches/.venv/bin/python - <<PY
import importlib.util
import sys
module = \"\$module\"
if importlib.util.find_spec(module) is None:
    print(f\"missing python module: {module}\", file=sys.stderr)
    raise SystemExit(1)
print(f\"python module ok: {module}\")
PY
done <<'MODULES'
$modules_py
MODULES
'"

log "Checking service unit and health"
remote "sudo incus exec '$CONTAINER' -- systemctl is-enabled '$SERVICE' >/dev/null"
remote "sudo incus exec '$CONTAINER' -- systemctl is-active '$SERVICE' >/dev/null"

if [ "$RESTART_SERVICE" = "1" ]; then
  log "Restarting $SERVICE because RESTART_SERVICE=1"
  remote "sudo incus exec '$CONTAINER' -- systemctl restart '$SERVICE'"
fi

remote "sudo incus exec '$CONTAINER' -- bash -lc '
set -Eeuo pipefail
curl -fsS http://127.0.0.1:8000/health
echo
curl -fsS http://127.0.0.1:8000/v1/models >/dev/null
'"

log "Checking public health"
curl -fsS "$PUBLIC_URL/health"
printf '\n'

log "Incus Whisper tools are ready."

#!/usr/bin/env sh
set -eu

IMAGE="${1:-}"
if [ -z "${IMAGE}" ]; then
  echo "Usage: $0 <image:tag>" >&2
  exit 64
fi

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_DIR="$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)"
BASE_COMPOSE="${PROJECT_DIR}/docker-compose.zero-downtime.yml"
GPU_COMPOSE="${PROJECT_DIR}/docker-compose.zero-downtime.gpu.yml"
PROXY_CONTAINER="videocut-proxy"
DRAIN_TIMEOUT_SECONDS="${DRAIN_TIMEOUT_SECONDS:-3600}"
STOP_TIMEOUT_SECONDS="${STOP_TIMEOUT_SECONDS:-600}"
ZERO_DOWNTIME_GPU="${ZERO_DOWNTIME_GPU:-1}"

cd "${PROJECT_DIR}"

dc() {
  if [ "${ZERO_DOWNTIME_GPU}" = "1" ]; then
    docker compose -f "${BASE_COMPOSE}" -f "${GPU_COMPOSE}" "$@"
  else
    docker compose -f "${BASE_COMPOSE}" "$@"
  fi
}

container_exists() {
  docker container inspect "$1" >/dev/null 2>&1
}

current_slot() {
  if ! container_exists "${PROXY_CONTAINER}"; then
    return 0
  fi
  docker exec "${PROXY_CONTAINER}" sh -c \
    "sed -n 's/.*server videocut-\\(blue\\|green\\):3000.*/\\1/p' /etc/nginx/runtime/upstream.conf" \
    2>/dev/null | head -n 1
}

wait_healthy() {
  container="$1"
  deadline=$(( $(date +%s) + 900 ))
  while :; do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container}" 2>/dev/null || true)"
    if [ "${status}" = "healthy" ]; then
      return 0
    fi
    if [ "${status}" = "unhealthy" ]; then
      echo "${container} became unhealthy; keeping the current slot online." >&2
      docker logs --tail 100 "${container}" >&2 || true
      return 1
    fi
    if [ "$(date +%s)" -ge "${deadline}" ]; then
      echo "Timed out waiting for ${container} to become healthy." >&2
      docker logs --tail 100 "${container}" >&2 || true
      return 1
    fi
    sleep 2
  done
}

switch_proxy() {
  slot="$1"
  docker exec "${PROXY_CONTAINER}" sh -c '
    set -eu
    slot="$1"
    cp /etc/nginx/runtime/upstream.conf /etc/nginx/runtime/upstream.conf.previous
    printf "server videocut-%s:3000;\n" "${slot}" > /etc/nginx/runtime/upstream.conf
    if nginx -t; then
      nginx -s reload
    else
      mv /etc/nginx/runtime/upstream.conf.previous /etc/nginx/runtime/upstream.conf
      exit 1
    fi
  ' sh "${slot}"
}

local_work_count() {
  container="$1"
  docker exec "${container}" python -c \
    "import json,urllib.request; d=json.load(urllib.request.urlopen('http://127.0.0.1:3000/health', timeout=3)); print(int(d.get('queueSize', 0)) + int(d.get('localActiveWorkers', 0)))" \
    2>/dev/null || printf '%s\n' 1
}

CURRENT="$(current_slot)"
if container_exists "${PROXY_CONTAINER}" && [ "${CURRENT}" != "blue" ] && [ "${CURRENT}" != "green" ]; then
  echo "Could not determine the active slot from ${PROXY_CONTAINER}." >&2
  echo "Check that the proxy is running and /etc/nginx/runtime/upstream.conf is valid." >&2
  exit 1
fi
if [ "${CURRENT}" = "blue" ]; then
  TARGET="green"
else
  TARGET="blue"
fi

TARGET_CONTAINER="videocut-${TARGET}"
mkdir -p "${PROJECT_DIR}/deploy/runtime/${TARGET}"
touch "${PROJECT_DIR}/deploy/runtime/${TARGET}/skip-replay-once"

echo "Starting ${TARGET} with ${IMAGE}..."
if [ "${TARGET}" = "blue" ]; then
  VIDEOCUT_BLUE_IMAGE="${IMAGE}" dc up -d --no-deps --force-recreate blue
else
  VIDEOCUT_GREEN_IMAGE="${IMAGE}" dc up -d --no-deps --force-recreate green
fi
wait_healthy "${TARGET_CONTAINER}"

if ! container_exists "${PROXY_CONTAINER}"; then
  if docker ps --format '{{.Names}} {{.Ports}}' | grep -Eq '(^| )0\.0\.0\.0:3000->|(^| )\[::\]:3000->'; then
    echo "Port 3000 is still owned by the legacy direct container." >&2
    echo "Stop that container once, then rerun this command to complete the proxy migration." >&2
    exit 1
  fi
  if [ "${TARGET}" != "blue" ]; then
    echo "Initial proxy bootstrap requires the blue slot." >&2
    exit 1
  fi
  dc up -d --no-deps proxy
  wait_healthy "${TARGET_CONTAINER}"
else
  switch_proxy "${TARGET}"
fi

if ! docker exec "${PROXY_CONTAINER}" wget -qO- http://127.0.0.1:3000/health >/dev/null; then
  echo "Proxy verification failed; rolling traffic back to ${CURRENT}." >&2
  if [ -n "${CURRENT}" ]; then
    switch_proxy "${CURRENT}"
  fi
  exit 1
fi

echo "Traffic is now on ${TARGET}."

if [ -n "${CURRENT}" ]; then
  OLD_CONTAINER="videocut-${CURRENT}"
  deadline=$(( $(date +%s) + DRAIN_TIMEOUT_SECONDS ))
  while container_exists "${OLD_CONTAINER}"; do
    work="$(local_work_count "${OLD_CONTAINER}")"
    if [ "${work}" = "0" ]; then
      echo "Old ${CURRENT} slot drained; stopping it gracefully."
      docker stop --time "${STOP_TIMEOUT_SECONDS}" "${OLD_CONTAINER}" >/dev/null
      docker rm "${OLD_CONTAINER}" >/dev/null
      break
    fi
    if [ "$(date +%s)" -ge "${deadline}" ]; then
      echo "Traffic switched, but ${OLD_CONTAINER} still has ${work} local task(s)." >&2
      echo "It was left running so active renders are not killed." >&2
      exit 2
    fi
    echo "Waiting for ${OLD_CONTAINER} to drain (${work} local task(s))..."
    sleep 5
  done
fi

echo "Deployment complete: ${IMAGE} is serving through ${PROXY_CONTAINER}:3000."

#!/usr/bin/env sh
set -eu

sync_bgm_from_oss() {
  if [ "${SYNC_BGM_ON_STARTUP:-1}" != "1" ]; then
    echo "[entrypoint] skip BGM sync because SYNC_BGM_ON_STARTUP=${SYNC_BGM_ON_STARTUP:-0}"
    return
  fi

  BGM_DIR="${BGM_DIR:-/app/input/bgm}"
  BGM_OSS_URI="${BGM_OSS_URI:-oss://goumee-coze/GouMei-Video-Cut/bgm/}"

  if ! command -v ossutil >/dev/null 2>&1; then
    echo "[entrypoint] ossutil not found, cannot sync BGM." >&2
    exit 1
  fi

  if [ -z "${OSS_ENDPOINT:-}" ] || [ -z "${OSS_ACCESS_KEY_ID:-}" ] || [ -z "${OSS_ACCESS_KEY_SECRET:-}" ]; then
    echo "[entrypoint] OSS_ENDPOINT / OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET are required for BGM sync." >&2
    exit 1
  fi

  mkdir -p "${BGM_DIR}"
  echo "[entrypoint] syncing BGM from ${BGM_OSS_URI} to ${BGM_DIR}"

  set -- ossutil sync "${BGM_OSS_URI}" "${BGM_DIR}/" \
    -e "${OSS_ENDPOINT}" \
    -i "${OSS_ACCESS_KEY_ID}" \
    -k "${OSS_ACCESS_KEY_SECRET}" \
    -u \
    -f

  if [ -n "${OSS_STS_TOKEN:-}" ]; then
    set -- "$@" -t "${OSS_STS_TOKEN}"
  fi

  "$@"
}

sync_bgm_from_oss
exec "$@"
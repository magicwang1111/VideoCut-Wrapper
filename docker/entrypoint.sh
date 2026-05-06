#!/usr/bin/env sh
set -eu

load_env_file() {
  ENV_FILE="${VIDEOCUT_ENV_FILE:-/app/.env}"
  if [ ! -f "${ENV_FILE}" ]; then
    return
  fi

  echo "[entrypoint] loading ${ENV_FILE}"
  while IFS= read -r line || [ -n "${line}" ]; do
    line="$(printf '%s' "${line}" | sed 's/\r$//;s/^[[:space:]]*//;s/[[:space:]]*$//')"
    case "${line}" in
      ""|\#*)
        continue
        ;;
      export\ *)
        line="${line#export }"
        ;;
    esac

    key="${line%%=*}"
    value="${line#*=}"
    if [ "${key}" = "${line}" ]; then
      continue
    fi
    key="$(printf '%s' "${key}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    case "${key}" in
      ""|[0-9]*|*[!abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_]*)
        continue
        ;;
    esac

    eval "is_set=\${${key}+x}"
    if [ -n "${is_set}" ]; then
      continue
    fi
    value="$(printf '%s' "${value}" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    export "${key}=${value}"
  done < "${ENV_FILE}"
}

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

load_env_file
sync_bgm_from_oss
exec "$@"

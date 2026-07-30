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

sync_bgm_prefix() {
  SYNC_URI="$1"
  SYNC_DIR="$2"
  SYNC_LABEL="$3"

  if [ -z "${SYNC_URI}" ]; then
    echo "[entrypoint] skip ${SYNC_LABEL} sync because URI is empty"
    return
  fi

  mkdir -p "${SYNC_DIR}"
  echo "[entrypoint] syncing ${SYNC_LABEL} from ${SYNC_URI} to ${SYNC_DIR}"

  set -- ossutil sync "${SYNC_URI}" "${SYNC_DIR}/" \
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

sync_bgm_from_oss() {
  SYNC_ALL_BGM="${SYNC_BGM_ON_STARTUP:-1}"

  if [ "${SYNC_ALL_BGM}" != "1" ]; then
    echo "[entrypoint] skip all BGM sync because SYNC_BGM_ON_STARTUP=${SYNC_ALL_BGM}"
    return
  fi

  if ! command -v ossutil >/dev/null 2>&1; then
    echo "[entrypoint] ossutil not found, cannot sync BGM." >&2
    exit 1
  fi

  if [ -z "${OSS_ENDPOINT:-}" ] || [ -z "${OSS_ACCESS_KEY_ID:-}" ] || [ -z "${OSS_ACCESS_KEY_SECRET:-}" ]; then
    echo "[entrypoint] OSS_ENDPOINT / OSS_ACCESS_KEY_ID / OSS_ACCESS_KEY_SECRET are required for BGM sync." >&2
    exit 1
  fi

  BGM_DIR="${BGM_DIR:-/app/input/bgm}"
  BGM_OSS_URI="${BGM_OSS_URI:-oss://goumee-coze/GouMei-Video-Cut/bgm/}"
  BGM_BACKUP_DIR="${BGM_BACKUP_DIR:-/app/input/bgm-backup}"
  if [ -z "${BGM_BACKUP_OSS_URI+x}" ]; then
    BGM_BACKUP_OSS_URI="oss://goumee-coze/GouMei-Video-Cut/bgm-backup/"
  fi
  BGM_TEMPLATE_DIR="${BGM_TEMPLATE_DIR:-/app/input/bgm-templete}"
  BGM_TEMPLATE_OSS_URI="${BGM_TEMPLATE_OSS_URI:-oss://goumee-coze/GouMei-Video-Cut/bgm-templete/}"
  BGM_AVATAR_DIR="${BGM_AVATAR_DIR:-/app/input/bgm-avatar}"
  BGM_AVATAR_OSS_URI="${BGM_AVATAR_OSS_URI:-oss://goumee-coze/GouMei-Video-Cut/bgm-avatar/}"

  sync_bgm_prefix "${BGM_OSS_URI}" "${BGM_DIR}" "BGM"
  sync_bgm_prefix "${BGM_BACKUP_OSS_URI}" "${BGM_BACKUP_DIR}" "BGM backup"
  sync_bgm_prefix "${BGM_TEMPLATE_OSS_URI}" "${BGM_TEMPLATE_DIR}" "BGM template"
  sync_bgm_prefix "${BGM_AVATAR_OSS_URI}" "${BGM_AVATAR_DIR}" "BGM avatar"
}

load_env_file
sync_bgm_from_oss
exec "$@"

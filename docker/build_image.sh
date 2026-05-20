#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT_DIR}"

IMAGE_TAG="${1:-${IMAGE_TAG:-videocut-wrapper:v2}}"
if [[ "${IMAGE_TAG}" != *:* ]]; then
  IMAGE_TAG="videocut-wrapper:${IMAGE_TAG}"
fi

BASE_IMAGE="${BASE_IMAGE:-magicwang/pytorch-base:torch210-cu128-runtime-v1}"
IMAGE_VERSION="${IMAGE_VERSION:-${IMAGE_TAG##*:}}"
IMAGE_DESCRIPTION="${IMAGE_DESCRIPTION:-VideoCut Docker image ${IMAGE_VERSION}}"
BGM_MANIFEST_SOURCE="${BGM_MANIFEST_SOURCE:-${ROOT_DIR}/input/bgm}"

PYTHON_BIN="${PYTHON:-}"
if [ -z "${PYTHON_BIN}" ]; then
  if command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  else
    echo "python or python3 is required to generate docs/BGM_MANIFEST.json." >&2
    exit 1
  fi
fi

"${PYTHON_BIN}" -m videocut bgm-manifest \
  --bgm-dir "${BGM_MANIFEST_SOURCE}" \
  --output "${ROOT_DIR}/docs/BGM_MANIFEST.json"

docker build \
  --build-arg BASE_IMAGE="${BASE_IMAGE}" \
  --build-arg IMAGE_VERSION="${IMAGE_VERSION}" \
  --build-arg IMAGE_DESCRIPTION="${IMAGE_DESCRIPTION}" \
  -t "${IMAGE_TAG}" \
  .

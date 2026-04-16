ARG BASE_IMAGE=magicwang/pytorch-base:torch210-cu128-runtime-v1
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC \
    PORT=3000 \
    BGM_DIR=/app/input/bgm \
    BGM_OSS_URI=oss://goumee-coze/GouMei-Video-Cut/bgm/ \
    SYNC_BGM_ON_STARTUP=1

WORKDIR /app

COPY README.md pyproject.toml requirements.txt ./
COPY videocut ./videocut
COPY templates ./templates
COPY pipelines ./pipelines
COPY fonts ./fonts
COPY docker/entrypoint.sh /usr/local/bin/videocut-entrypoint

RUN chmod +x /usr/local/bin/videocut-entrypoint \
    && python -m pip install --upgrade pip \
    && python -m pip install . \
    && mkdir -p /app/input/bgm /app/output /srv/videocut/data /srv/videocut/temp /srv/videocut/oss-local

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, sys, urllib.request; port = os.getenv('PORT', '3000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3).getcode() == 200 else 1)"

ENTRYPOINT ["tini", "--", "/usr/local/bin/videocut-entrypoint"]
CMD ["sh", "-c", "python -m videocut serve --host 0.0.0.0 --port ${PORT:-3000}"]

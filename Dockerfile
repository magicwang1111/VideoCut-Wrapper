FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC \
    PORT=3000

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        fontconfig \
        tini \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY README.md pyproject.toml requirements.txt ./
COPY videocut ./videocut
COPY templates ./templates
COPY fonts ./fonts

RUN python -m pip install --upgrade pip \
    && python -m pip install .

RUN mkdir -p /app/output /srv/videocut/data /srv/videocut/temp /srv/videocut/oss-local

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os, sys, urllib.request; port = os.getenv('PORT', '3000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3).getcode() == 200 else 1)"

ENTRYPOINT ["tini", "--"]
CMD ["sh", "-c", "python -m videocut serve --host 0.0.0.0 --port ${PORT:-3000}"]

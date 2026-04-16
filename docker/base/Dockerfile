# docker/base/Dockerfile
FROM pytorch/pytorch:2.10.0-cuda12.8-cudnn9-runtime

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC

RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get autoremove -y \
    && apt-get install -y --no-install-recommends \
        curl \
        wget \
        git \
        unzip \
        zip \
        tar \
        build-essential \
        cmake \
        vim \
        software-properties-common \
        ca-certificates \
        python3.11-dev \
        fontconfig \
        tini \
        tzdata \
    && python3 -c "from pathlib import Path; dist = Path('/usr/lib/python3/dist-packages'); target = dist / 'apt_pkg.so'; matches = sorted(dist.glob('apt_pkg*.so')); target.exists() or (matches and target.symlink_to(matches[0].name))" \
    && add-apt-repository -y ppa:ubuntuhandbook1/ffmpeg7 \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && curl -fsSL https://gosspublic.alicdn.com/ossutil/install.sh | bash \
    && OSSUTIL_BIN="$(command -v ossutil || true)" \
    && if [ -z "$OSSUTIL_BIN" ]; then OSSUTIL_BIN="$(find /root -type f -name ossutil -perm -111 | head -n 1)"; fi \
    && test -n "$OSSUTIL_BIN" \
    && ln -sf "$OSSUTIL_BIN" /usr/local/bin/ossutil \
    && ossutil version >/dev/null \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

ENTRYPOINT ["tini", "--"]
CMD ["python", "--version"]

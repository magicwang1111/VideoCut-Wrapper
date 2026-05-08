FROM pytorch/pytorch:2.10.0-cuda12.8-cudnn9-runtime

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ARG FFMPEG_VERSION=n7.1.1
ARG NV_CODEC_HEADERS_VERSION=n13.0.19.0

ENV DEBIAN_FRONTEND=noninteractive \
    VIRTUAL_ENV=/opt/videocut/venv \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC

ENV PATH="${VIRTUAL_ENV}/bin:${PATH}"

RUN apt-get update \
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
        ca-certificates \
        python3-dev \
        python3-venv \
        fontconfig \
        pkg-config \
        nasm \
        yasm \
        libx264-dev \
        libfreetype-dev \
        libfontconfig1-dev \
        tini \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv "${VIRTUAL_ENV}" \
    && python -m pip install --upgrade pip setuptools wheel

RUN git clone --depth 1 --branch "${NV_CODEC_HEADERS_VERSION}" https://github.com/FFmpeg/nv-codec-headers.git /tmp/nv-codec-headers \
    && make -C /tmp/nv-codec-headers -j"$(nproc)" \
    && make -C /tmp/nv-codec-headers install PREFIX=/usr/local \
    && git clone --depth 1 --branch "${FFMPEG_VERSION}" https://github.com/FFmpeg/FFmpeg.git /tmp/ffmpeg \
    && cd /tmp/ffmpeg \
    && PKG_CONFIG_PATH="/usr/local/lib/pkgconfig:${PKG_CONFIG_PATH:-}" ./configure \
        --prefix=/usr/local \
        --enable-gpl \
        --enable-nonfree \
        --enable-libx264 \
        --enable-libfreetype \
        --enable-libfontconfig \
    && make -j"$(nproc)" \
    && make install \
    && hash -r \
    && ffmpeg -hide_banner -encoders > /tmp/ffmpeg-encoders.txt \
    && grep -q h264_nvenc /tmp/ffmpeg-encoders.txt \
    && rm -rf /tmp/ffmpeg /tmp/nv-codec-headers /tmp/ffmpeg-encoders.txt

RUN curl -fL -o /tmp/ossutil.zip https://gosspublic.alicdn.com/ossutil/1.7.19/ossutil-v1.7.19-linux-amd64.zip \
    && unzip /tmp/ossutil.zip -d /tmp \
    && install -m 0755 /tmp/ossutil-v1.7.19-linux-amd64/ossutil /usr/local/bin/ossutil \
    && /usr/local/bin/ossutil help >/dev/null \
    && rm -rf /tmp/ossutil.zip /tmp/ossutil-v1.7.19-linux-amd64

ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility,video

WORKDIR /workspace

ENTRYPOINT ["tini", "--"]
CMD ["python", "--version"]

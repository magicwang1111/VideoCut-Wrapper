FROM pytorch/pytorch:2.10.0-cuda12.8-cudnn9-runtime

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC

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
        software-properties-common \
        ca-certificates \
        python3-dev \
        fontconfig \
        tini \
        tzdata \
    && python3 -c "from pathlib import Path; dist = Path('/usr/lib/python3/dist-packages'); target = dist / 'apt_pkg.so'; matches = sorted(dist.glob('apt_pkg*.so')); target.exists() or (matches and target.symlink_to(matches[0].name))" \
    && add-apt-repository -y ppa:ubuntuhandbook1/ffmpeg7 \
    && apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && curl -fL -o /tmp/ossutil.zip https://gosspublic.alicdn.com/ossutil/1.7.19/ossutil-v1.7.19-linux-amd64.zip \
    && unzip /tmp/ossutil.zip -d /tmp \
    && install -m 0755 /tmp/ossutil-v1.7.19-linux-amd64/ossutil /usr/local/bin/ossutil \
    && /usr/local/bin/ossutil help >/dev/null \
    && rm -rf /tmp/ossutil.zip /tmp/ossutil-v1.7.19-linux-amd64 /var/lib/apt/lists/*

WORKDIR /workspace

ENTRYPOINT ["tini", "--"]
CMD ["python", "--version"]
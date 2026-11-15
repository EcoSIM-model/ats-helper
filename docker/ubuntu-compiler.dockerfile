FROM ubuntu:24.04

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    autoconf \
    automake \
    bison \
    build-essential \
    ca-certificates \
    cmake \
    curl \
    flex \
    gfortran \
    git \
    libblas-dev \
    libcurl4-openssl-dev \
    liblapack-dev \
    liblapacke-dev \
    libopenmpi-dev \
    libssl-dev \
    libtool \
    libxml2-dev \
    m4 \
    openmpi-bin \
    patch \
    pkg-config \
    python-is-python3 \
    python3 \
    python3-h5py \
    python3-matplotlib \
    python3-numpy \
    rsync \
    unzip \
    wget \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY docker/ats-env.sh /etc/profile.d/ats.sh

WORKDIR /ats-helper
CMD ["bash", "--login"]

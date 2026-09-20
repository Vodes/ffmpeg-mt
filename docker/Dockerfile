FROM almalinux:9 AS packages

RUN dnf -y install dnf-plugins-core epel-release && \
    dnf config-manager --set-enabled crb && \
    dnf -y install \
      autoconf automake bison bzip2 cargo cargo-c ccache clang cmake diffutils file findutils \
      flex gcc gcc-c++ git glibc-static gperf gzip libstdc++-static libtool make meson nasm ninja-build \
      mesa-vulkan-drivers patch perl python3.12 python3.12-pip python3.12-devel rsync \
      pkgconf-pkg-config rust tar texinfo unzip vim-common which xz yasm zip zstd && \
    dnf clean all

FROM packages AS shader_tools

ARG SHADERC_VERSION=2026.2
ARG SHADERC_SHA256=f924178e75e3293082481b25ed64d5e48a795b479dac3bd3c83d23070855df42

WORKDIR /tmp/shaderc
RUN curl -fL --retry 5 \
      -o source.tar.gz \
      "https://github.com/google/shaderc/archive/refs/tags/v${SHADERC_VERSION}.tar.gz" && \
    echo "${SHADERC_SHA256}  source.tar.gz" | sha256sum -c - && \
    tar -xzf source.tar.gz --strip-components=1 && \
    python3.12 utils/git-sync-deps && \
    cmake -S . -B build -GNinja -DCMAKE_BUILD_TYPE=Release \
      -DSHADERC_SKIP_TESTS=ON -DSHADERC_SKIP_EXAMPLES=ON \
      -DSHADERC_SKIP_COPYRIGHT_CHECK=ON -DBUILD_SHARED_LIBS=OFF && \
    cmake --build build --target glslc --parallel && \
    install -m 0755 build/glslc/glslc /usr/local/bin/glslc && \
    rm -rf /tmp/shaderc

FROM shader_tools AS builder

ARG LLVM_MINGW_VERSION=20260908
ARG LLVM_MINGW_SHA256=6409ef0f7278489698477dd008418b91b62245995c5e2f3c6b22500ef95d42e2

WORKDIR /tmp/llvm-mingw
RUN curl -fL --retry 5 \
      -o source.tar.gz \
      "https://github.com/mstorsjo/llvm-mingw/archive/refs/tags/${LLVM_MINGW_VERSION}.tar.gz" && \
    echo "${LLVM_MINGW_SHA256}  source.tar.gz" | sha256sum -c - && \
    tar -xzf source.tar.gz --strip-components=1 && \
    ./build-all.sh /opt/llvm-mingw && \
    rm -rf /tmp/llvm-mingw

RUN python3.12 -m pip install --no-cache-dir uv==0.8.22

ENV LLVM_MINGW_ROOT=/opt/llvm-mingw
ENV PATH=/opt/llvm-mingw/bin:/root/.local/bin:${PATH}
WORKDIR /src

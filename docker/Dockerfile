ARG MANYLINUX_IMAGE=quay.io/pypa/manylinux_2_34_x86_64@sha256:0b4acc5b3c4c797bb9a6286c4d15039962f61ee0c5426753afd5cbe45baebfc4
FROM ubuntu:22.04@sha256:b8b6ee6aa931ecd9d0d952abc34dc0e5f7c6a30c6bb71b079fe399fde0329c02 AS llvm_mingw_runtime

FROM ${MANYLINUX_IMAGE} AS packages

RUN dnf -y install dnf-plugins-core epel-release && \
    dnf config-manager --set-enabled crb && \
    dnf -y install \
      autoconf automake bison bzip2 cargo cargo-c ccache clang cmake diffutils file findutils \
      flex gcc gcc-c++ git glibc-static gperf gzip libstdc++-static libtool make nasm ninja-build \
      patch perl rsync alsa-lib-devel \
      pkgconf-pkg-config rust tar texinfo unzip vim-common which xz yasm zip zstd && \
    if [ "$(uname -m)" = x86_64 ]; then dnf -y install mesa-vulkan-drivers; fi && \
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
    cmake --build build --target glslc_exe --parallel && \
    install -m 0755 build/glslc/glslc /usr/local/bin/glslc && \
    rm -rf /tmp/shaderc

FROM shader_tools AS base_builder

RUN python3.12 -m pip install --no-cache-dir uv==0.8.22

WORKDIR /src

FROM base_builder AS linux_builder

FROM base_builder AS builder

ARG LLVM_MINGW_VERSION=20260616
ARG LLVM_MINGW_X86_64_SHA256=534b92e067b22a6b4441f48ae9240a3341b17825d04d577eab0cf85c44b4deda
ARG LLVM_MINGW_AARCH64_SHA256=e7e5d135d93d3f2a3beaaea633a5b0e66ac75391a53feae654391913dd76102b
ARG TARGETARCH

WORKDIR /tmp/llvm-mingw
RUN case "${TARGETARCH}" in \
      amd64) host_arch=x86_64; checksum="${LLVM_MINGW_X86_64_SHA256}" ;; \
      arm64) host_arch=aarch64; checksum="${LLVM_MINGW_AARCH64_SHA256}" ;; \
      *) echo "Unsupported LLVM-MinGW host architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac && \
    archive="llvm-mingw-${LLVM_MINGW_VERSION}-ucrt-ubuntu-22.04-${host_arch}.tar.xz" && \
    curl -fL --retry 5 \
      -o "${archive}" \
      "https://github.com/mstorsjo/llvm-mingw/releases/download/${LLVM_MINGW_VERSION}/${archive}" && \
    echo "${checksum}  ${archive}" | sha256sum -c - && \
    mkdir -p /opt/llvm-mingw && \
    tar -xJf "${archive}" --strip-components=1 -C /opt/llvm-mingw && \
    rm -rf /tmp/llvm-mingw

COPY --from=llvm_mingw_runtime \
    /usr/lib/*-linux-gnu/libstdc++.so.6* /opt/llvm-mingw/host-libs/

ENV LLVM_MINGW_ROOT=/opt/llvm-mingw
ENV PATH=/opt/llvm-mingw/bin:/root/.local/bin:${PATH}
ENV LD_LIBRARY_PATH=/opt/llvm-mingw/host-libs:${LD_LIBRARY_PATH}

WORKDIR /src

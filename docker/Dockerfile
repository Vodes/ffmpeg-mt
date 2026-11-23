ARG MANYLINUX_IMAGE=quay.io/pypa/manylinux_2_34_x86_64@sha256:994ad4b59d4eb3be4b84f177b001ef8d5fea57b81e83b4f9171dfcc4a6d6bdee
FROM ubuntu:22.04@sha256:f64b2ffc2f10ab8f8505aa56b2664bd846cbb9df87c7e2532e30f7667021c0e6 AS llvm_mingw_runtime

FROM ${MANYLINUX_IMAGE} AS packages

RUN dnf -y install dnf-plugins-core epel-release && \
    dnf config-manager --set-enabled crb && \
    dnf -y install \
      autoconf automake bison bzip2 cargo-c ccache clang cmake diffutils file findutils \
      flex gcc gcc-c++ git glibc-static gperf gzip libstdc++-static libtool make nasm ninja-build \
      patch perl rsync alsa-lib-devel \
      pkgconf-pkg-config tar texinfo unzip vim-common which xz yasm zip zstd && \
    if [ "$(uname -m)" = x86_64 ]; then dnf -y install mesa-vulkan-drivers; fi && \
    dnf clean all

ARG RUST_VERSION=1.100.0
ARG RUSTUP_VERSION=1.29.3
ARG RUSTUP_X86_64_SHA256=b1c8cb14e30889e7e2999e6b69e62d9e9b9ffc0f4df8180564d306d9680e6c55
ARG RUSTUP_AARCH64_SHA256=9f608c142e0e38a78957d0661ba1dfaf0b6fb37184cfaa4b9a0aa57af6184282
ARG TARGETARCH
ENV RUSTUP_HOME=/opt/rustup
ENV CARGO_HOME=/opt/cargo
ENV PATH=/opt/cargo/bin:${PATH}
ENV RUSTUP_TOOLCHAIN=${RUST_VERSION}

RUN case "${TARGETARCH}" in \
      amd64) rust_arch=x86_64-unknown-linux-gnu; checksum="${RUSTUP_X86_64_SHA256}" ;; \
      arm64) rust_arch=aarch64-unknown-linux-gnu; checksum="${RUSTUP_AARCH64_SHA256}" ;; \
      *) echo "Unsupported Rust host architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac && \
    curl -fL --retry 5 \
      -o /tmp/rustup-init \
      "https://static.rust-lang.org/rustup/archive/${RUSTUP_VERSION}/${rust_arch}/rustup-init" && \
    echo "${checksum}  /tmp/rustup-init" | sha256sum -c - && \
    chmod 0755 /tmp/rustup-init && \
    /tmp/rustup-init -y --default-toolchain none --profile minimal && \
    rustup toolchain install "${RUST_VERSION}" --profile minimal --component rust-docs && \
    rustup target add --toolchain "${RUST_VERSION}" \
      x86_64-pc-windows-gnu aarch64-pc-windows-gnullvm && \
    rustup default "${RUST_VERSION}" && \
    chmod -R a+rX "${RUSTUP_HOME}" "${CARGO_HOME}" && \
    rm -f /tmp/rustup-init

FROM packages AS shader_tools

ARG SHADERC_VERSION=2026.4
ARG SHADERC_SHA256=f06ce5bcca94e5df7f34e115743597d0ad2e13c5fe9213c67dc8c76031241947

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

RUN python3.12 -m pip install --no-cache-dir uv==0.13.9

WORKDIR /src

FROM base_builder AS linux_builder

FROM base_builder AS builder

ARG LLVM_MINGW_VERSION=20261117
ARG LLVM_MINGW_X86_64_SHA256=e7a15a49a2ea95e9f3d8a9d0cfabe5052a4b10fb4777cc54de5a8c27b64bb701
ARG LLVM_MINGW_AARCH64_SHA256=6d9442ead9ace3b3e4c3389bfef69f93f873305e0705e6cc60978ac895320872
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

RUN ln -s windows.h /opt/llvm-mingw/generic-w64-mingw32/include/Windows.h

# Match the reference Windows images: remove shared runtime and import libraries
# so LLVM-MinGW consistently selects its static runtimes.
RUN find /opt/llvm-mingw \( -name '*.dll' -o -name '*.dll.a' \) -delete

COPY --from=llvm_mingw_runtime \
    /usr/lib/*-linux-gnu/libstdc++.so.6* /opt/llvm-mingw/host-libs/

ENV LLVM_MINGW_ROOT=/opt/llvm-mingw
ENV PATH=/opt/llvm-mingw/bin:/root/.local/bin:${PATH}
ENV LD_LIBRARY_PATH=/opt/llvm-mingw/host-libs:${LD_LIBRARY_PATH}

WORKDIR /src

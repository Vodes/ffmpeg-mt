# ffmpeg-mt

Reproducible FFmpeg and FFprobe builds for Linux, Windows, and Apple Silicon.
The default profile is GPLv3. FFmpeg and every linked third-party input are
checksum-pinned in `sources.lock.toml`; the build prefix is isolated from host
package metadata.

## Targets

| Target | Build environment | Platform acceleration |
| --- | --- | --- |
| `linux-x86_64` | manylinux_2_34, native GCC | VAAPI/DRM, Vulkan, OpenCL, NVIDIA, AMF, oneVPL/QSV |
| `linux-arm64` | manylinux_2_34, native GCC | VAAPI/DRM, Vulkan, OpenCL, NVIDIA headers, AMF |
| `windows-x86_64` | manylinux_2_34, LLVM-MinGW/UCRT | MediaFoundation, D3D11/12, DXVA2, Vulkan, OpenCL, NVIDIA, AMF, oneVPL/QSV |
| `windows-arm64` | manylinux_2_34, LLVM-MinGW/UCRT | MediaFoundation, D3D11/12, DXVA2, Vulkan, OpenCL, NVIDIA, AMF |
| `macos-arm64` | native macOS 15, macOS 12 target | VideoToolbox, AudioToolbox, AVFoundation, CoreImage/Metal, OpenCL, static MoltenVK |

The curated codec/filter surface includes AV1, H.264/H.265, VP8/VP9, Opus,
MP3, LC3, VMAF, zimg, libplacebo, subtitles/font shaping, SVG/LV2, DVD and
Blu-ray, ARIB captions, JPEG 2000, WebP, OpenAL, SRT, RIST, SSH, QR
encoding/decoding, and libmysofa. Whisper is omitted because of its build and
binary-size cost.

## Build

The single build interface launches the manylinux_2_34 builder automatically for
Linux and Windows, and builds directly on macOS:

```console
uv run python build.py <target> [--nonfree] [--jobs N] [--clean]
```

Outputs are written to `dist/` as
`ffmpeg-<version>-<target>.tar.zst`. Each archive has only `ffmpeg`,
`ffprobe`, third-party notices, and machine-readable build/source metadata.
The corresponding-source archive is produced with:

```console
uv run python scripts/source_archive.py
```

The build validates target architecture, expected features, runtime-library
allowlists, the Linux glibc 2.34 ceiling, macOS 12 deployment metadata, and
leaked host/build paths. CI executes every packaged binary on a matching native
runner and performs a generated A/V encode-and-probe smoke test.
Linux VAAPI uses pinned Implib.so lazy import shims on both architectures: no
libva or libva-drm shared object is packaged or added to `DT_NEEDED`, while a
host with a compatible VAAPI runtime and driver can load them when VAAPI is
actually used.

## Nonfree profile

`--nonfree` selects the generic nonfree profile. It currently adds libfdk-aac,
passes both `--enable-libfdk-aac` and `--enable-nonfree`, marks metadata as
non-redistributable, and adds `-nonfree` to the filename and release tag.
Additional nonfree dependencies can be added to this profile without changing
the build interface. The workflow input controls whether this profile is built
and published; the repository owner is responsible for deciding where to
publish the resulting archive.

## Updating

The weekly updater considers only numbered stable `.tar.xz` archives. It
checks the downloaded FFmpeg release key against its published fingerprint,
verifies the archive signature, computes the checksum, resets the build
revision, and opens a PR. Dependency pins are reviewed and updated manually.

Pushes, pull requests, and manual workflow runs build, test, and retain the
target archives as GitHub Actions artifacts. Publishing a versioned GitHub
release requires a manual workflow run with the `release` checkbox selected;
the checkbox defaults to off.

## Disclaimer

This repo was built with heavy ai-assistance because I can't be arsed to come up with a clean builder structure myself.

Mostly using 5.6 Sol because Astra sucks.

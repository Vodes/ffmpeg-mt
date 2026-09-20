from __future__ import annotations

from pathlib import Path

from .helpers import extract, run
from .model import BuildContext

COMMON_FLAGS = (
    "--disable-autodetect",
    "--disable-debug",
    "--disable-doc",
    "--disable-ffplay",
    "--disable-shared",
    "--enable-static",
    "--enable-gpl",
    "--enable-version3",
    "--enable-ffmpeg",
    "--enable-ffprobe",
    "--enable-pic",
    "--pkg-config-flags=--static",
    "--enable-zlib",
    "--enable-lzma",
    "--enable-libaom",
    "--enable-libaribcaption",
    "--enable-libass",
    "--enable-libbluray",
    "--enable-libdav1d",
    "--enable-libfontconfig",
    "--enable-libfreetype",
    "--enable-libfribidi",
    "--enable-libharfbuzz",
    "--enable-libdvdnav",
    "--enable-libdvdread",
    "--enable-liblc3",
    "--enable-libmp3lame",
    "--enable-libmysofa",
    "--enable-libopenjpeg",
    "--enable-libopus",
    "--enable-libqrencode",
    "--enable-libquirc",
    "--enable-librist",
    "--enable-librubberband",
    "--enable-libsoxr",
    "--enable-libsrt",
    "--enable-libsvtav1",
    "--enable-libvmaf",
    "--enable-libvpx",
    "--enable-libwebp",
    "--enable-libx264",
    "--enable-libx265",
    "--enable-libzimg",
    "--enable-libxml2",
    "--enable-iconv",
    "--enable-lv2",
    "--enable-openal",
    "--enable-sdl2",
)


def configure_flags(ctx: BuildContext, revision: int) -> list[str]:
    flags = [
        *COMMON_FLAGS,
        "--prefix=/",
        f"--extra-version=ffmt.{revision}",
    ]
    if ctx.target.linux:
        flags += [
            "--extra-libs=-lm",
            "--enable-gnutls",
            "--enable-librsvg",
            "--enable-libssh",
            "--enable-libplacebo",
            "--enable-opencl",
            "--enable-vulkan",
            "--enable-vaapi",
            "--enable-libdrm",
        ]
        if ctx.target.name != "linux-arm64":
            flags += ["--enable-amf", "--enable-libvpl"]
        flags += ["--enable-ffnvcodec", "--enable-nvdec", "--enable-nvenc"]
    elif ctx.target.windows:
        assert ctx.target.host is not None
        env = ctx.env()
        flags += [
            "--enable-cross-compile",
            "--target-os=mingw32",
            f"--arch={ctx.target.arch}",
            f"--cross-prefix={ctx.target.host}-",
            f"--cc={env['CC']}",
            f"--cxx={env['CXX']}",
            f"--ar={env['AR']}",
            f"--ranlib={env['RANLIB']}",
            f"--strip={env['STRIP']}",
            f"--windres={env['WINDRES']}",
            "--enable-schannel",
            "--enable-opencl",
            "--enable-vulkan",
            "--enable-libplacebo",
            "--enable-mediafoundation",
            "--enable-d3d11va",
            "--enable-d3d12va",
            "--enable-dxva2",
        ]
        if ctx.target.arch == "x86_64":
            flags += [
                "--enable-libssh",
                "--enable-amf",
                "--enable-libvpl",
                "--enable-ffnvcodec",
                "--enable-nvdec",
                "--enable-nvenc",
            ]
    else:
        frameworks = (
            "-lMoltenVK -lc++ -framework Metal -framework Foundation -framework QuartzCore "
            "-framework CoreGraphics -framework IOSurface -framework IOKit -framework AppKit"
        )
        flags += [
            "--arch=arm64",
            "--enable-audiotoolbox",
            "--enable-avfoundation",
            "--enable-coreimage",
            "--enable-metal",
            "--enable-opencl",
            "--enable-libssh",
            "--enable-librsvg",
            "--enable-securetransport",
            "--enable-videotoolbox",
            "--enable-vulkan",
            "--enable-vulkan-static",
            "--enable-libplacebo",
            f"--extra-libs={frameworks}",
        ]
    if ctx.with_fdk_aac:
        flags += ["--enable-libfdk-aac", "--enable-nonfree"]
    return flags


def build_ffmpeg(ctx: BuildContext, revision: int) -> tuple[Path, list[str]]:
    source = extract(ctx, "ffmpeg")
    build_dir = ctx.work_root / "ffmpeg"
    build_dir.mkdir(parents=True, exist_ok=True)
    flags = configure_flags(ctx, revision)
    run(source / "configure", *flags, cwd=build_dir, env=ctx.env())
    run("make", f"-j{ctx.jobs}", cwd=build_dir, env=ctx.env())
    run("make", f"DESTDIR={ctx.prefix}", "install", cwd=build_dir, env=ctx.env())
    return source, flags

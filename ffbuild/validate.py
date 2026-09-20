from __future__ import annotations

import json
import platform
import re
import tomllib
from pathlib import Path
from typing import Any

from .helpers import run
from .model import BuildContext

LINUX_ALLOWED = {
    "libc.so.6",
    "libdl.so.2",
    "libm.so.6",
    "libpthread.so.0",
    "librt.so.1",
    "ld-linux-aarch64.so.1",
    "ld-linux-x86-64.so.2",
}
WINDOWS_ALLOWED = {
    "ADVAPI32.dll",
    "AVICAP32.dll",
    "BCRYPT.dll",
    "CFGMGR32.dll",
    "COMDLG32.dll",
    "CRYPT32.dll",
    "D3D11.dll",
    "D3D12.dll",
    "DXGI.dll",
    "GDI32.dll",
    "IMM32.dll",
    "KERNEL32.dll",
    "MF.dll",
    "MFPlat.dll",
    "MFReadWrite.dll",
    "OLE32.dll",
    "OLEAUT32.dll",
    "OLEACC.dll",
    "NCRYPT.dll",
    "NTDLL.dll",
    "Secur32.dll",
    "SETUPAPI.dll",
    "SHELL32.dll",
    "SHLWAPI.dll",
    "USER32.dll",
    "USERENV.dll",
    "VERSION.dll",
    "WINMM.dll",
    "WS2_32.dll",
    "api-ms-win-crt-convert-l1-1-0.dll",
    "api-ms-win-crt-environment-l1-1-0.dll",
    "api-ms-win-crt-filesystem-l1-1-0.dll",
    "api-ms-win-crt-heap-l1-1-0.dll",
    "api-ms-win-crt-locale-l1-1-0.dll",
    "api-ms-win-crt-math-l1-1-0.dll",
    "api-ms-win-crt-private-l1-1-0.dll",
    "api-ms-win-crt-runtime-l1-1-0.dll",
    "api-ms-win-crt-stdio-l1-1-0.dll",
    "api-ms-win-crt-string-l1-1-0.dll",
    "api-ms-win-crt-time-l1-1-0.dll",
}


def _words(output: str) -> set[str]:
    return {line.split()[0] for line in output.splitlines() if line and not line.startswith(" ")}


def _expected(root: Path, target: str) -> tuple[dict[str, Any], dict[str, Any]]:
    with (root / "features.toml").open("rb") as stream:
        data: dict[str, Any] = tomllib.load(stream)
    return data["common"], data["targets"][target]


def assert_configure_flags(root: Path, target: str, flags: list[str], with_fdk_aac: bool) -> None:
    common, selected = _expected(root, target)
    missing = (set(common["configure"]) | set(selected["configure"])) - set(flags)
    if missing:
        raise RuntimeError(f"missing required configure flags: {sorted(missing)}")
    if with_fdk_aac:
        if not {"--enable-libfdk-aac", "--enable-nonfree"} <= set(flags):
            raise RuntimeError("FDK-AAC builds must enable both libfdk-aac and nonfree")
    elif {"--enable-libfdk-aac", "--enable-nonfree"} & set(flags):
        raise RuntimeError("public build contains nonfree configuration")


def _assert_architecture(ctx: BuildContext, binary: Path) -> None:
    output = run("file", binary, capture=True).lower()
    aliases = {
        "x86_64": ("x86-64", "x86_64", "x86-64"),
        "aarch64": ("aarch64", "arm64"),
        "arm64": ("arm64", "aarch64"),
    }[ctx.target.arch]
    if not any(alias in output for alias in aliases):
        raise RuntimeError(f"wrong binary architecture: {output.strip()}")


def _assert_runtime_dependencies(ctx: BuildContext, binary: Path) -> None:
    if ctx.target.linux:
        dynamic = run("readelf", "-d", binary, capture=True)
        needed = set(re.findall(r"Shared library: \[(.+?)\]", dynamic))
        unexpected = needed - LINUX_ALLOWED
        if unexpected:
            raise RuntimeError(f"unexpected Linux runtime dependencies: {sorted(unexpected)}")
        versions = run("readelf", "--version-info", binary, capture=True)
        for major, minor in re.findall(r"GLIBC_(\d+)\.(\d+)", versions):
            if (int(major), int(minor)) > (2, 34):
                raise RuntimeError(f"binary requires GLIBC_{major}.{minor}; maximum is GLIBC_2.34")
    elif ctx.target.windows:
        output = run(f"{ctx.target.host}-objdump", "-p", binary, capture=True)
        needed = set(re.findall(r"DLL Name: ([^\r\n]+)", output, re.IGNORECASE))
        allowed = {item.lower() for item in WINDOWS_ALLOWED}
        unexpected = {item for item in needed if item.lower() not in allowed}
        if unexpected:
            raise RuntimeError(f"unexpected Windows runtime dependencies: {sorted(unexpected)}")
    else:
        output = run("otool", "-L", binary, capture=True)
        dependencies = re.findall(r"^\s+([^ ]+)", output, re.MULTILINE)
        unexpected_macos = [
            item for item in dependencies if not item.startswith(("/usr/lib/", "/System/Library/"))
        ]
        if unexpected_macos:
            raise RuntimeError(f"unexpected macOS runtime dependencies: {unexpected_macos}")
        if any("MoltenVK" in item or "vulkan" in item.lower() for item in dependencies):
            raise RuntimeError("MoltenVK/Vulkan must be linked statically on macOS")
        load_commands = run("otool", "-l", binary, capture=True)
        match = re.search(r"minos\s+(\d+)\.(\d+)", load_commands)
        if not match or tuple(map(int, match.groups())) > (12, 0):
            raise RuntimeError("macOS minimum deployment target is newer than 12.0")


def _assert_no_staged_shared_libraries(ctx: BuildContext) -> None:
    shared_libraries = []
    for path in ctx.prefix.rglob("*"):
        name = path.name.lower()
        if name.endswith((".dll", ".dylib", ".so")) or ".so." in name:
            shared_libraries.append(path.relative_to(ctx.prefix).as_posix())
    if shared_libraries:
        raise RuntimeError(
            f"build prefix contains shared libraries that must not be packaged: {shared_libraries}"
        )


def _assert_lazy_vaapi(ctx: BuildContext, ffmpeg: Path) -> None:
    if not ctx.target.linux:
        return
    content = ffmpeg.read_bytes()
    missing = [name for name in (b"libva.so.2", b"libva-drm.so.2") if name not in content]
    if missing:
        decoded = [name.decode() for name in missing]
        raise RuntimeError(f"FFmpeg is missing lazy VAAPI import shims: {decoded}")


def _native(ctx: BuildContext) -> bool:
    machine = platform.machine().lower()
    aliases = {
        "x86_64": {"x86_64", "amd64"},
        "aarch64": {"aarch64", "arm64"},
        "arm64": {"aarch64", "arm64"},
    }
    system = {"linux": "linux", "macos": "darwin", "windows": "windows"}[ctx.target.os]
    return platform.system().lower() == system and machine in aliases[ctx.target.arch]


def _assert_features(ctx: BuildContext, ffmpeg: Path) -> None:
    common, selected = _expected(ctx.root, ctx.target.name)
    commands = {
        "encoders": "-encoders",
        "decoders": "-decoders",
        "filters": "-filters",
        "protocols": "-protocols",
        "hwaccels": "-hwaccels",
    }
    for group, switch in commands.items():
        output = run(ffmpeg, "-hide_banner", switch, capture=True)
        for name in [*common.get(group, []), *selected.get(group, [])]:
            if not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", output):
                raise RuntimeError(f"expected {group[:-1]} {name!r} is missing")


def _assert_compiled_features(ctx: BuildContext) -> None:
    common, selected = _expected(ctx.root, ctx.target.name)
    config_files = (
        ctx.work_root / "ffmpeg" / "config.h",
        ctx.work_root / "ffmpeg" / "config_components.h",
    )
    configuration = "\n".join(path.read_text(encoding="utf-8") for path in config_files)
    suffixes = {
        "encoders": "ENCODER",
        "decoders": "DECODER",
        "filters": "FILTER",
        "protocols": "PROTOCOL",
    }
    for group, suffix in suffixes.items():
        for name in [*common.get(group, []), *selected.get(group, [])]:
            macro_name = name.upper().replace("-", "_")
            macro = f"#define CONFIG_{macro_name}_{suffix} 1"
            if macro not in configuration:
                raise RuntimeError(f"expected compiled {group[:-1]} {name!r} is missing")
    for name in [*common.get("hwaccels", []), *selected.get("hwaccels", [])]:
        macro = f"#define CONFIG_{name.upper().replace('-', '_')} 1"
        if macro not in configuration:
            raise RuntimeError(f"expected hardware integration {name!r} is missing")


def _smoke_test(ctx: BuildContext, ffmpeg: Path, ffprobe: Path) -> None:
    sample = ctx.build_root / "smoke.mkv"
    run(
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=duration=1:size=128x72:rate=10",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=1000:duration=1",
        "-c:v",
        "mpeg4",
        "-c:a",
        "aac",
        "-y",
        sample,
    )
    data = run(ffprobe, "-v", "error", "-show_streams", "-of", "json", sample, capture=True)
    streams = json.loads(data)["streams"]
    if {stream["codec_type"] for stream in streams} != {"audio", "video"}:
        raise RuntimeError("smoke test did not produce one audio and one video stream")


def _vulkan_smoke(ctx: BuildContext, ffmpeg: Path) -> None:
    if not (ctx.target.linux or ctx.target.macos):
        return
    run(
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-init_hw_device",
        "vulkan=vk:0",
        "-filter_hw_device",
        "vk",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=duration=0.2:size=128x72:rate=5",
        "-vf",
        "format=nv12,hwupload,scale_vulkan=64:36,hwdownload,format=nv12",
        "-f",
        "null",
        "-",
    )


def _scan_paths(ctx: BuildContext, binaries: tuple[Path, Path]) -> None:
    needles = {
        str(ctx.root),
        str(ctx.build_root),
        str(ctx.prefix),
        "/opt/homebrew",
        "/opt/llvm-mingw",
    }
    for binary in binaries:
        content = binary.read_bytes()
        found = sorted(needle for needle in needles if needle.encode() in content)
        if found:
            raise RuntimeError(f"{binary.name} leaks build paths: {found}")


def validate(ctx: BuildContext, flags: list[str]) -> None:
    suffix = ctx.target.executable_suffix
    ffmpeg = ctx.prefix / "bin" / f"ffmpeg{suffix}"
    ffprobe = ctx.prefix / "bin" / f"ffprobe{suffix}"
    assert_configure_flags(ctx.root, ctx.target.name, flags, ctx.with_fdk_aac)
    _assert_compiled_features(ctx)
    _assert_no_staged_shared_libraries(ctx)
    for binary in (ffmpeg, ffprobe):
        if not binary.is_file():
            raise FileNotFoundError(binary)
        _assert_architecture(ctx, binary)
        _assert_runtime_dependencies(ctx, binary)
    _assert_lazy_vaapi(ctx, ffmpeg)
    _scan_paths(ctx, (ffmpeg, ffprobe))
    if _native(ctx):
        version = run(ffmpeg, "-version", capture=True)
        run(ffprobe, "-version")
        if "--disable-autodetect" not in version:
            raise RuntimeError("ffmpeg -version does not report the expected build configuration")
        _assert_features(ctx, ffmpeg)
        _smoke_test(ctx, ffmpeg, ffprobe)
        _vulkan_smoke(ctx, ffmpeg)

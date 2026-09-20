from __future__ import annotations

from .model import Target

TARGETS: dict[str, Target] = {
    "linux-x86_64": Target("linux-x86_64", "linux", "x86_64", None),
    "linux-arm64": Target("linux-arm64", "linux", "aarch64", None),
    "windows-x86_64": Target("windows-x86_64", "windows", "x86_64", "x86_64-w64-mingw32", ".exe"),
    "windows-arm64": Target("windows-arm64", "windows", "aarch64", "aarch64-w64-mingw32", ".exe"),
    "macos-arm64": Target("macos-arm64", "macos", "arm64", None, deployment_target="12.0"),
}

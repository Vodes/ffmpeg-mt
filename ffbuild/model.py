from __future__ import annotations

import os
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    version: str
    url: str
    sha256: str
    license: str
    license_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Target:
    name: str
    os: str
    arch: str
    host: str | None
    executable_suffix: str = ""
    deployment_target: str | None = None

    @property
    def windows(self) -> bool:
        return self.os == "windows"

    @property
    def linux(self) -> bool:
        return self.os == "linux"

    @property
    def macos(self) -> bool:
        return self.os == "macos"


@dataclass(slots=True)
class BuildContext:
    root: Path
    target: Target
    sources: dict[str, Source]
    jobs: int
    with_fdk_aac: bool
    source_dirs: dict[str, Path] = field(default_factory=dict)

    @property
    def build_root(self) -> Path:
        return self.root / "build" / self.target.name

    @property
    def source_root(self) -> Path:
        return self.build_root / "src"

    @property
    def work_root(self) -> Path:
        return self.build_root / "work"

    @property
    def prefix(self) -> Path:
        return self.build_root / "prefix"

    @property
    def downloads(self) -> Path:
        return self.root / ".cache" / "downloads"

    @property
    def dist(self) -> Path:
        return self.root / "dist"

    def prepare(self, clean: bool) -> None:
        if clean and self.build_root.exists():
            shutil.rmtree(self.build_root)
        # Build trees and prefixes are deliberately never reused across invocations.
        for path in (self.source_root, self.work_root, self.prefix):
            if path.exists():
                shutil.rmtree(path)
        for path in (self.source_root, self.work_root, self.prefix, self.downloads, self.dist):
            path.mkdir(parents=True, exist_ok=True)

    def env(self) -> dict[str, str]:
        prefix = str(self.prefix)
        maps = f"-ffile-prefix-map={self.root}=. -fdebug-prefix-map={self.root}=."
        env = dict(os.environ)
        env.update(
            {
                "PKG_CONFIG_LIBDIR": os.pathsep.join(
                    [f"{prefix}/lib/pkgconfig", f"{prefix}/share/pkgconfig"]
                ),
                "PKG_CONFIG_PATH": "",
                "CMAKE_PREFIX_PATH": prefix,
                "PATH": os.pathsep.join([f"{prefix}/bin", env.get("PATH", "")]),
                "SOURCE_DATE_EPOCH": env.get("SOURCE_DATE_EPOCH", "0"),
                "ZERO_AR_DATE": "1",
                "FFMPEG_MT_TARGET": self.target.name,
                "FFMPEG_MT_PREFIX": prefix,
                "CCACHE_DIR": str(self.root / ".cache" / "ccache"),
                "CARGO_HOME": str(self.root / ".cache" / "cargo"),
                "RUSTFLAGS": f"--remap-path-prefix={self.root}=.",
                "CPPFLAGS": f"-I{prefix}/include",
                "CFLAGS": f"-O2 {maps}",
                "CXXFLAGS": f"-O2 {maps}",
                "LDFLAGS": f"-L{prefix}/lib",
            }
        )
        if self.target.macos:
            env["MACOSX_DEPLOYMENT_TARGET"] = self.target.deployment_target or "12.0"
        if self.target.windows:
            triple = self.target.host
            assert triple is not None
            toolchain = Path(env.get("LLVM_MINGW_ROOT", "/opt/llvm-mingw"))
            bindir = toolchain / "bin"
            env.update(
                {
                    "PATH": os.pathsep.join([str(bindir), env["PATH"]]),
                    "CC": f"ccache {triple}-clang",
                    "CXX": f"ccache {triple}-clang++",
                    "AR": f"{triple}-ar",
                    "RANLIB": f"{triple}-ranlib",
                    "STRIP": f"{triple}-strip",
                    "WINDRES": f"{triple}-windres",
                    "PKG_CONFIG": "pkg-config",
                }
            )
        else:
            env.setdefault("CC", "ccache clang" if self.target.macos else "ccache gcc")
            env.setdefault("CXX", "ccache clang++" if self.target.macos else "ccache g++")
            env.setdefault("AR", "ar")
            env.setdefault("RANLIB", "ranlib")
        if not self.target.macos:
            env["LDFLAGS"] += " -static-libgcc -static-libstdc++"
        return env


def load_sources(path: Path) -> tuple[dict[str, Source], int]:
    with path.open("rb") as stream:
        raw: dict[str, Any] = tomllib.load(stream)
    revision = int(raw["build"]["revision"])
    sources: dict[str, Source] = {}
    for name, item in raw["sources"].items():
        source = Source(
            name=name,
            version=str(item["version"]),
            url=str(item["url"]),
            sha256=str(item["sha256"]),
            license=str(item["license"]),
            license_files=tuple(item.get("license_files", ["COPYING*"])),
        )
        if len(source.sha256) != 64:
            raise ValueError(f"{name}: sha256 must contain 64 hexadecimal characters")
        int(source.sha256, 16)
        sources[name] = source
    return sources, revision

#!/usr/bin/env python3
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

MANYLINUX_IMAGES = {
    "x86_64": (
        "quay.io/pypa/manylinux_2_34_x86_64"
        "@sha256:0b4acc5b3c4c797bb9a6286c4d15039962f61ee0c5426753afd5cbe45baebfc4"
    ),
    "aarch64": (
        "quay.io/pypa/manylinux_2_34_aarch64"
        "@sha256:db1a485b015c1d9a6d7e2929367a69a6f772964be06cca8fe585f959a47ec0b7"
    ),
}


def run_in_container(root: Path, target: str, jobs: int, with_fdk_aac: bool, clean: bool) -> None:
    machine = platform.machine().lower()
    expected = {"x86_64", "amd64"} if target.endswith("x86_64") else {"arm64", "aarch64"}
    if machine not in expected:
        raise RuntimeError(
            f"{target} requires a native {sorted(expected)[0]} Docker host, got {machine}"
        )
    host_arch = "x86_64" if machine in {"x86_64", "amd64"} else "aarch64"
    image = "ffmpeg-mt-builder:manylinux_2_34"
    if os.environ.get("FFMPEG_MT_SKIP_IMAGE_BUILD") != "1":
        image_stage = "builder" if target.startswith("windows-") else "linux_builder"
        subprocess.run(
            [
                "docker",
                "build",
                "--build-arg",
                f"MANYLINUX_IMAGE={MANYLINUX_IMAGES[host_arch]}",
                "--target",
                image_stage,
                "--tag",
                image,
                "--file",
                str(root / "docker" / "Dockerfile"),
                str(root),
            ],
            check=True,
        )
    command = ["uv", "run", "python", "build.py", target, "--jobs", str(jobs)]
    if with_fdk_aac:
        command.append("--with-fdk-aac")
    if clean:
        command.append("--clean")
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            f"{__import__('os').getuid()}:{__import__('os').getgid()}",
            "--volume",
            f"{root}:/src",
            "--env",
            "UV_CACHE_DIR=/tmp/uv-cache",
            "--env",
            "UV_PROJECT_ENVIRONMENT=/tmp/ffmpeg-mt-venv",
            "--env",
            "FFMPEG_MT_IN_CONTAINER=1",
            image,
            *command,
        ],
        check=True,
    )

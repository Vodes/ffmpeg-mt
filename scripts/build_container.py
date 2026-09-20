#!/usr/bin/env python3
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path


def run_in_container(root: Path, target: str, jobs: int, with_fdk_aac: bool, clean: bool) -> None:
    machine = platform.machine().lower()
    expected = {"x86_64", "amd64"} if target.endswith("x86_64") else {"arm64", "aarch64"}
    if machine not in expected:
        raise RuntimeError(
            f"{target} requires a native {sorted(expected)[0]} Docker host, got {machine}"
        )
    image = "ffmpeg-mt-builder:almalinux9"
    if os.environ.get("FFMPEG_MT_SKIP_IMAGE_BUILD") != "1":
        subprocess.run(
            [
                "docker",
                "build",
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

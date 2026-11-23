#!/usr/bin/env python3
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

MANYLINUX_IMAGES = {
    "x86_64": (
        "quay.io/pypa/manylinux_2_34_x86_64"
        "@sha256:994ad4b59d4eb3be4b84f177b001ef8d5fea57b81e83b4f9171dfcc4a6d6bdee"
    ),
    "aarch64": (
        "quay.io/pypa/manylinux_2_34_aarch64"
        "@sha256:2027d9b991e2e9f87541b78d8dd28968984f82796b08449890ac04aaf2ae044f"
    ),
}


def run_in_container(root: Path, target: str, jobs: int, nonfree: bool, clean: bool) -> None:
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
    if nonfree:
        command.append("--nonfree")
    if clean:
        command.append("--clean")
    container_environment = [
        "--env",
        "UV_CACHE_DIR=/tmp/uv-cache",
        "--env",
        "UV_PROJECT_ENVIRONMENT=/tmp/ffmpeg-mt-venv",
        "--env",
        "FFMPEG_MT_IN_CONTAINER=1",
    ]
    source_date_epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if source_date_epoch is not None:
        container_environment += ["--env", f"SOURCE_DATE_EPOCH={source_date_epoch}"]
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--user",
            f"{__import__('os').getuid()}:{__import__('os').getgid()}",
            "--volume",
            f"{root}:/src",
            *container_environment,
            image,
            *command,
        ],
        check=True,
    )

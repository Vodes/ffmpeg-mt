#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import platform
from pathlib import Path

from ffbuild.build import build
from ffbuild.targets import TARGETS
from scripts.build_container import run_in_container


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Build a pinned static FFmpeg tool archive")
    result.add_argument("target", choices=TARGETS)
    result.add_argument(
        "--nonfree",
        action="store_true",
        help="enable the optional nonfree dependency profile",
    )
    result.add_argument("--jobs", type=int, default=os.cpu_count() or 1)
    result.add_argument("--clean", action="store_true")
    return result


def main() -> None:
    args = parser().parse_args()
    if args.jobs < 1:
        parser().error("--jobs must be at least 1")
    root = Path(__file__).resolve().parent
    if not args.target.startswith("macos-") and os.environ.get("FFMPEG_MT_IN_CONTAINER") != "1":
        run_in_container(root, args.target, args.jobs, args.nonfree, args.clean)
        return
    machine = platform.machine().lower()
    expected = {"x86_64"} if args.target.endswith("x86_64") else {"arm64", "aarch64"}
    if machine not in expected:
        parser().error(
            f"{args.target} requires a native {sorted(expected)[0]} runner, got {machine}"
        )
    build(
        root=root,
        target_name=args.target,
        jobs=args.jobs,
        nonfree=args.nonfree,
        clean=args.clean,
    )


if __name__ == "__main__":
    main()

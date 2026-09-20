from __future__ import annotations

from pathlib import Path

from .ffmpeg import build_ffmpeg
from .model import BuildContext, load_sources
from .package import package
from .recipes import build_dependencies
from .targets import TARGETS
from .validate import validate


def build(
    root: Path,
    target_name: str,
    jobs: int,
    with_fdk_aac: bool,
    clean: bool,
) -> Path:
    sources, revision = load_sources(root / "sources.lock.toml")
    ctx = BuildContext(root, TARGETS[target_name], sources, jobs, with_fdk_aac)
    ctx.prepare(clean)
    build_dependencies(ctx)
    _, flags = build_ffmpeg(ctx, revision)
    validate(ctx, flags)
    output = package(ctx, revision, flags)
    print(f"Created {output}")
    return output

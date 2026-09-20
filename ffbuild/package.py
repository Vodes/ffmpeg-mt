from __future__ import annotations

import json
import shutil
import tarfile
import tempfile
from pathlib import Path

from .helpers import copy_license_files, run, sha256
from .model import BuildContext


def artifact_name(ctx: BuildContext) -> str:
    version = ctx.sources["ffmpeg"].version
    suffix = "-nonfree" if ctx.with_fdk_aac else ""
    return f"ffmpeg-{version}-{ctx.target.name}{suffix}.tar.zst"


def public_release_allowed(with_fdk_aac: bool, repository_private: bool) -> bool:
    return not with_fdk_aac


def private_upload_allowed(with_fdk_aac: bool, repository_private: bool) -> bool:
    return not with_fdk_aac or repository_private


def _normalise(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    return info


def _write_tar(source: Path, destination: Path) -> None:
    with tarfile.open(destination, "w") as archive:
        for path in sorted(source.rglob("*"), key=lambda item: item.as_posix()):
            archive.add(
                path,
                arcname=path.relative_to(source.parent),
                filter=_normalise,
                recursive=False,
            )


def package(ctx: BuildContext, revision: int, flags: list[str]) -> Path:
    name = artifact_name(ctx)
    root_name = name.removesuffix(".tar.zst")
    with tempfile.TemporaryDirectory(dir=ctx.build_root) as temporary:
        staging = Path(temporary) / root_name
        bin_dir = staging / "bin"
        notices = staging / "licenses"
        bin_dir.mkdir(parents=True)
        suffix = ctx.target.executable_suffix
        for program in ("ffmpeg", "ffprobe"):
            program_path = ctx.prefix / "bin" / f"{program}{suffix}"
            if not program_path.is_file():
                raise FileNotFoundError(f"expected built program is missing: {program_path}")
            shutil.copy2(program_path, bin_dir / program_path.name)
        included = set(ctx.source_dirs)
        for source_name in sorted(included):
            source_info = ctx.sources[source_name]
            bundled_notice = ctx.root / "notices" / source_name
            if bundled_notice.is_dir():
                shutil.copytree(bundled_notice, notices / source_name)
            else:
                copy_license_files(
                    ctx.source_dirs[source_name], source_info.license_files, notices / source_name
                )
        if "rsvg" in included:
            lock_dir = staging / "source-locks"
            lock_dir.mkdir()
            shutil.copy2(ctx.source_dirs["rsvg"] / "Cargo.lock", lock_dir / "librsvg-Cargo.lock")
            crate_notices = notices / "rsvg-rust-crates"
            copied_crate_notices = 0
            for crate in sorted((ctx.source_dirs["rsvg"] / "vendor").iterdir()):
                if not crate.is_dir():
                    continue
                for pattern in ("LICENSE*", "COPYING*", "NOTICE*", "UNLICENSE"):
                    for license_file in sorted(crate.glob(pattern)):
                        if not license_file.is_file():
                            continue
                        destination = crate_notices / crate.name / license_file.name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(license_file, destination)
                        copied_crate_notices += 1
            if copied_crate_notices == 0:
                raise FileNotFoundError("no license notices found in vendored librsvg crates")
        source_records = []
        for item in sorted(included):
            source = ctx.sources[item]
            record = {
                "name": source.name,
                "version": source.version,
                "url": source.url,
                "sha256": source.sha256,
                "license": source.license,
            }
            if item == "rsvg":
                record["nested_lock"] = "source-locks/librsvg-Cargo.lock"
            source_records.append(record)
        metadata = {
            "artifact": name,
            "build_revision": revision,
            "ffmpeg_version": ctx.sources["ffmpeg"].version,
            "target": ctx.target.name,
            "profile": "nonfree" if ctx.with_fdk_aac else "gplv3",
            "redistributable": not ctx.with_fdk_aac,
            "configure": flags,
            "sources": source_records,
        }
        encoded_metadata = json.dumps(metadata, indent=2, sort_keys=True) + "\n"
        forbidden = (str(ctx.root), str(ctx.build_root), "/opt/homebrew", "/opt/llvm-mingw")
        leaked = [item for item in forbidden if item in encoded_metadata]
        if leaked:
            raise RuntimeError(f"build metadata leaks private build paths: {leaked}")
        (staging / "build-info.json").write_text(encoded_metadata, encoding="utf-8")
        raw_tar = ctx.build_root / f"{root_name}.tar"
        _write_tar(staging, raw_tar)
        output = ctx.dist / name
        output.unlink(missing_ok=True)
        run("zstd", "-T0", "-19", "--no-progress", "-o", output, raw_tar)
        raw_tar.unlink()
    (output.with_suffix(output.suffix + ".sha256")).write_text(
        f"{sha256(output)}  {output.name}\n", encoding="utf-8"
    )
    return output

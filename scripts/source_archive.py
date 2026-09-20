#!/usr/bin/env python3
from __future__ import annotations

import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from ffbuild.helpers import download, extract_tar
from ffbuild.model import load_sources


def normalise(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = info.mtime = 0
    info.uname = info.gname = ""
    return info


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    sources, _ = load_sources(root / "sources.lock.toml")
    version = sources["ffmpeg"].version
    output = root / "dist" / f"ffmpeg-{version}-corresponding-source.tar.zst"
    output.parent.mkdir(exist_ok=True)
    (root / "build").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root / "build") as temporary:
        stage = Path(temporary) / f"ffmpeg-{version}-corresponding-source"
        archives = stage / "archives"
        scripts = stage / "build-scripts"
        archives.mkdir(parents=True)
        scripts.mkdir()
        for source in sources.values():
            archive = download(source, root / ".cache" / "downloads")
            shutil.copy2(archive, archives)
            if source.name == "rsvg":
                cargo_source = Path(temporary) / "rsvg-source"
                cargo_source.mkdir()
                extract_tar(archive, cargo_source, "rsvg")
                source_root = next(cargo_source.iterdir())
                subprocess.run(
                    [
                        "cargo",
                        "vendor",
                        "--locked",
                        "--versioned-dirs",
                        str(archives / "rsvg-cargo-vendor"),
                    ],
                    cwd=source_root,
                    check=True,
                )
        tracked = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, check=True, stdout=subprocess.PIPE
        ).stdout.split(b"\0")
        for encoded in tracked:
            if not encoded:
                continue
            relative = Path(encoded.decode())
            if relative.parts[0] == "context":
                continue
            destination = scripts / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / relative, destination)
        raw = output.with_suffix("")
        with tarfile.open(raw, "w") as bundle:
            bundle.add(stage, arcname=stage.name, filter=normalise)
        subprocess.run(["zstd", "-T0", "-19", "-f", "-o", output, raw], check=True)
        raw.unlink()
    print(output)


if __name__ == "__main__":
    main()

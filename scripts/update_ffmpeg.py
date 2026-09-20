#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import tempfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import tomlkit
from packaging.version import InvalidVersion, Version
from tomlkit.items import Table

from ffbuild.helpers import download_url, fetch_text, sha256

INDEX = "https://ffmpeg.org/releases/"
KEY = "https://ffmpeg.org/ffmpeg-devel.asc"
FINGERPRINT = "FCF986EA15E6E293A5644F10B4322F04D67658D8"


class ReleaseIndexParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.versions: dict[Version, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = next((value for name, value in attrs if name == "href"), None)
        if href is None:
            return
        filename = Path(urlsplit(href).path).name
        prefix, suffix = "ffmpeg-", ".tar.xz"
        if not filename.startswith(prefix) or not filename.endswith(suffix):
            return
        candidate = filename.removeprefix(prefix).removesuffix(suffix)
        try:
            version = Version(candidate)
        except InvalidVersion:
            return
        if version.is_prerelease or version.is_devrelease:
            return
        self.versions[version] = candidate


def stable_versions(index: str) -> list[str]:
    parser = ReleaseIndexParser()
    parser.feed(index)
    return [parser.versions[version] for version in sorted(parser.versions)]


def verify_release(version: str, directory: Path) -> tuple[Path, str]:
    archive = directory / f"ffmpeg-{version}.tar.xz"
    signature = archive.with_suffix(archive.suffix + ".asc")
    key = directory / "ffmpeg-devel.asc"
    download_url(f"{INDEX}{archive.name}", archive)
    download_url(f"{INDEX}{signature.name}", signature)
    download_url(KEY, key)
    home = directory / "gnupg"
    home.mkdir(mode=0o700)
    subprocess.run(["gpg", "--homedir", str(home), "--import", str(key)], check=True)
    listing = subprocess.run(
        ["gpg", "--homedir", str(home), "--with-colons", "--fingerprint"],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout
    fingerprints = {line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:")}
    if FINGERPRINT not in fingerprints:
        raise RuntimeError("downloaded FFmpeg signing key has the wrong fingerprint")
    verification = subprocess.run(
        [
            "gpg",
            "--homedir",
            str(home),
            "--batch",
            "--status-fd=1",
            "--verify",
            str(signature),
            str(archive),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout
    if f"[GNUPG:] VALIDSIG {FINGERPRINT} " not in verification:
        raise RuntimeError("release signature was not made by the pinned FFmpeg key")
    return archive, sha256(archive)


def update_lock(path: Path, version: str, sha256: str) -> bool:
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    sources = document.get("sources")
    if not isinstance(sources, Table):
        raise RuntimeError("sources section is missing")
    ffmpeg = sources.get("ffmpeg")
    if not isinstance(ffmpeg, Table):
        raise RuntimeError("sources.ffmpeg section is missing")
    current = ffmpeg.get("version")
    if not isinstance(current, str):
        raise RuntimeError("sources.ffmpeg.version is missing or is not a string")
    try:
        current_version = Version(current)
        next_version = Version(version)
    except InvalidVersion as error:
        raise RuntimeError(f"invalid FFmpeg version: {error}") from error
    if current_version >= next_version:
        return False
    build = document.get("build")
    if not isinstance(build, Table):
        raise RuntimeError("build section is missing")
    ffmpeg["version"] = version
    ffmpeg["url"] = f"{INDEX}ffmpeg-{version}.tar.xz"
    ffmpeg["sha256"] = sha256
    build["revision"] = 1
    path.write_text(tomlkit.dumps(document), encoding="utf-8")
    return True


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    index = fetch_text(INDEX)
    versions = stable_versions(index)
    if not versions:
        raise RuntimeError("no stable FFmpeg archives found")
    with tempfile.TemporaryDirectory() as directory:
        _, checksum = verify_release(versions[-1], Path(directory))
    changed = update_lock(root / "sources.lock.toml", versions[-1], checksum)
    print(f"{'Updated to' if changed else 'Already at'} FFmpeg {versions[-1]}")


if __name__ == "__main__":
    main()

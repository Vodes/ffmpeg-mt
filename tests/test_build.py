from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

import ffbuild.helpers as helpers
from ffbuild.ffmpeg import configure_flags
from ffbuild.helpers import download, download_url, extract, fetch_text
from ffbuild.model import BuildContext, Source, load_sources
from ffbuild.package import artifact_name, private_upload_allowed, public_release_allowed
from ffbuild.recipes import selected_recipes
from ffbuild.targets import TARGETS
from ffbuild.validate import _assert_no_staged_shared_libraries, assert_configure_flags
from scripts.update_ffmpeg import INDEX, stable_versions, update_lock

ROOT = Path(__file__).parents[1]


def context(tmp_path: Path, target: str, nonfree: bool = False) -> BuildContext:
    sources, _ = load_sources(ROOT / "sources.lock.toml")
    return BuildContext(tmp_path, TARGETS[target], sources, 2, nonfree)


def test_target_matrix_is_exact() -> None:
    assert tuple(TARGETS) == (
        "linux-x86_64",
        "linux-arm64",
        "windows-x86_64",
        "windows-arm64",
        "macos-arm64",
    )


def test_third_party_workflow_actions_are_commit_pinned() -> None:
    for workflow in (ROOT / ".github" / "workflows").glob("*.yml"):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.startswith(("uses:", "- uses:")):
                continue
            action = stripped.split("uses:", 1)[1].split("#", 1)[0].strip()
            if action.startswith("./"):
                continue
            name, reference = action.rsplit("@", 1)
            if name.startswith("actions/"):
                parts = reference.removeprefix("v").split(".")
                assert len(parts) == 3 and all(part.isdigit() for part in parts), action
            else:
                assert len(reference) == 40, action
                int(reference, 16)


@pytest.mark.parametrize("target", TARGETS)
def test_dependency_order_and_predicates(tmp_path: Path, target: str) -> None:
    names = [name for name, _, _ in selected_recipes(context(tmp_path, target))]
    assert names.index("zlib") < names.index("libpng")
    assert names.index("iconv") < names.index("libxml2")
    assert names.index("libudfread") < names.index("libbluray")
    assert names.index("libdvdcss") < names.index("libdvdread") < names.index("libdvdnav")
    assert names.index("lv2") < names.index("zix") < names.index("serd") < names.index("lilv")
    if target.startswith("linux-") or target == "macos-arm64":
        assert names.index("libffi") < names.index("glib") < names.index("cairo")
        assert names.index("pango") < names.index("librsvg")
    else:
        assert "librsvg" not in names
    if target == "macos-arm64":
        assert "MoltenVK" in names
    else:
        assert names.index("vulkan-headers") < names.index("libplacebo")
    assert ("libva" in names) is target.startswith("linux-")
    assert ("Implib.so" in names) is target.startswith("linux-")
    if target.startswith("linux-"):
        assert names.index("libdrm") < names.index("Implib.so") < names.index("libva")
    assert ("MoltenVK" in names) is (target == "macos-arm64")
    assert "fdk-aac" not in names
    assert ("openssl" in names) is (target != "windows-arm64")
    assert ("ssh" in names) is (target != "windows-arm64")


@pytest.mark.parametrize("target", TARGETS)
def test_configure_manifest_and_nonfree_separation(tmp_path: Path, target: str) -> None:
    public = context(tmp_path, target)
    public_flags = configure_flags(public, 1)
    assert_configure_flags(ROOT, target, public_flags, False)
    assert len(public_flags) == len(set(public_flags))
    assert "--enable-nonfree" not in public_flags
    assert "--enable-libfdk-aac" not in public_flags
    if target == "macos-arm64":
        extra_libraries = next(flag for flag in public_flags if flag.startswith("--extra-libs="))
        assert "-lc++" in extra_libraries
        assert "-framework AppKit" in extra_libraries
        assert "-framework CoreGraphics" in extra_libraries

    nonfree = context(tmp_path, target, True)
    nonfree_flags = configure_flags(nonfree, 1)
    assert_configure_flags(ROOT, target, nonfree_flags, True)
    assert {"--enable-nonfree", "--enable-libfdk-aac"} <= set(nonfree_flags)


def test_artifact_names(tmp_path: Path) -> None:
    assert artifact_name(context(tmp_path, "linux-x86_64")) == ("ffmpeg-9.0.2-linux-x86_64.tar.zst")
    assert artifact_name(context(tmp_path, "macos-arm64", True)) == (
        "ffmpeg-9.0.2-macos-arm64-nonfree.tar.zst"
    )


def test_release_gate() -> None:
    assert public_release_allowed(False, False)
    assert public_release_allowed(False, True)
    assert not public_release_allowed(True, False)
    assert not public_release_allowed(True, True)
    assert private_upload_allowed(False, False)
    assert not private_upload_allowed(True, False)
    assert private_upload_allowed(True, True)


def test_download_rejects_bad_checksum(tmp_path: Path) -> None:
    payload = tmp_path / "input.tar"
    payload.write_bytes(b"not the expected source")
    source = Source("bad", "1", payload.as_uri(), "0" * 64, "MIT", ("LICENSE",))
    destination = tmp_path / "downloads"
    destination.mkdir()
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        download(source, destination)
    assert not any(destination.glob("*.part"))


def test_download_accepts_verified_source(tmp_path: Path) -> None:
    payload = tmp_path / "upstream-1.2.3.tar.xz"
    payload.write_bytes(b"verified")
    checksum = hashlib.sha256(payload.read_bytes()).hexdigest()
    source = Source("ok", "1.2.3", payload.as_uri(), checksum, "MIT", ("LICENSE",))
    destination = tmp_path / "downloads"
    destination.mkdir()
    downloaded = download(source, destination)
    assert downloaded.name == "ok-1.2.3.tar.xz"
    assert downloaded.read_bytes() == b"verified"


class FakeResponse:
    def __init__(self, chunks: tuple[bytes, ...] = (), text: str = "") -> None:
        self.chunks = chunks
        self.text = text
        self.status_checked = False

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def raise_for_status(self) -> None:
        self.status_checked = True

    def iter_content(self, chunk_size: int) -> tuple[bytes, ...]:
        assert chunk_size == 1024 * 1024
        return self.chunks


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, **kwargs: object) -> FakeResponse:
        self.calls.append((url, kwargs))
        return self.response


def test_http_download_streams_and_checks_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    response = FakeResponse((b"first", b"", b"second"))
    session = FakeSession(response)
    monkeypatch.setattr(helpers, "HTTP_SESSION", session)
    destination = tmp_path / "nested" / "download"

    download_url("https://example.invalid/archive", destination)

    assert destination.read_bytes() == b"firstsecond"
    assert response.status_checked
    assert session.calls == [
        ("https://example.invalid/archive", {"stream": True, "allow_redirects": True})
    ]


def test_http_text_fetch_checks_status(monkeypatch: pytest.MonkeyPatch) -> None:
    response = FakeResponse(text="release index")
    session = FakeSession(response)
    monkeypatch.setattr(helpers, "HTTP_SESSION", session)

    assert fetch_text("https://example.invalid/") == "release index"
    assert response.status_checked
    assert session.calls == [("https://example.invalid/", {"allow_redirects": True})]


def _archive_with_symlink(tmp_path: Path, link_target: str) -> Source:
    archive = tmp_path / "fixture.tar"
    content = b"safe\n"
    with tarfile.open(archive, "w") as bundle:
        root = tarfile.TarInfo("root")
        root.type = tarfile.DIRTYPE
        bundle.addfile(root)
        subdirectory = tarfile.TarInfo("root/sub")
        subdirectory.type = tarfile.DIRTYPE
        bundle.addfile(subdirectory)
        target = tarfile.TarInfo("root/target")
        target.size = len(content)
        bundle.addfile(target, io.BytesIO(content))
        link = tarfile.TarInfo("root/sub/link")
        link.type = tarfile.SYMTYPE
        link.linkname = link_target
        bundle.addfile(link)
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    return Source("fixture", "1", archive.as_uri(), checksum, "MIT", ("LICENSE",))


def test_extract_accepts_relative_symlink_within_archive(tmp_path: Path) -> None:
    ctx = context(tmp_path / "workspace", "linux-x86_64")
    ctx.sources["fixture"] = _archive_with_symlink(tmp_path, "../target")
    ctx.prepare(clean=False)
    extracted = extract(ctx, "fixture")
    assert (extracted / "sub" / "link").read_text(encoding="utf-8") == "safe\n"


def test_extract_rejects_symlink_escaping_archive(tmp_path: Path) -> None:
    ctx = context(tmp_path / "workspace", "linux-x86_64")
    ctx.sources["fixture"] = _archive_with_symlink(tmp_path, "../../../outside")
    ctx.prepare(clean=False)
    with pytest.raises(ValueError, match="unsafe archive member"):
        extract(ctx, "fixture")


@pytest.mark.parametrize(
    "library", ("libexample.so", "libexample.so.1", "example.dll", "lib.dylib")
)
def test_staged_shared_libraries_are_rejected(tmp_path: Path, library: str) -> None:
    ctx = context(tmp_path, "linux-x86_64")
    ctx.prepare(clean=False)
    staged = ctx.prefix / "lib" / library
    staged.parent.mkdir()
    staged.write_bytes(b"shared")
    with pytest.raises(RuntimeError, match="build prefix contains shared libraries"):
        _assert_no_staged_shared_libraries(ctx)


def test_only_numbered_stable_archives_are_selected() -> None:
    index = """
      <a href="ffmpeg-9.0.2.tar.xz">stable</a>
      <a href="ffmpeg-10.0-rc1.tar.xz">prerelease</a>
      <a href="ffmpeg-snapshot.tar.xz">snapshot</a>
      <a href="ffmpeg-8.1.12.tar.xz">old stable</a>
    """
    assert stable_versions(index) == ["8.1.12", "9.0.2"]


def test_lock_update_uses_toml_structure_and_preserves_comments(tmp_path: Path) -> None:
    lock = tmp_path / "sources.lock.toml"
    lock.write_text(
        """# retained header
[build]
revision = 7 # reset after an upstream update

[sources.ffmpeg]
version = "9.0.1" # stable release
url = "https://example.invalid/old.tar.xz"
sha256 = "old"

[sources.other]
version = "1"
""",
        encoding="utf-8",
    )
    assert update_lock(lock, "9.0.2", "a" * 64)
    updated = lock.read_text(encoding="utf-8")
    assert "# retained header" in updated
    assert "revision = 1 # reset after an upstream update" in updated
    assert 'version = "9.0.2" # stable release' in updated
    assert f'url = "{INDEX}ffmpeg-9.0.2.tar.xz"' in updated
    assert f'sha256 = "{"a" * 64}"' in updated
    assert '[sources.other]\nversion = "1"' in updated

    unchanged = updated
    assert not update_lock(lock, "9.0.2", "b" * 64)
    assert lock.read_text(encoding="utf-8") == unchanged


def test_environment_excludes_host_pkg_config(tmp_path: Path) -> None:
    ctx = context(tmp_path, "linux-x86_64")
    env = ctx.env()
    assert env["PKG_CONFIG_PATH"] == ""
    assert env["PKG_CONFIG_LIBDIR"].split(":") == [
        str(ctx.prefix / "lib" / "pkgconfig"),
        str(ctx.prefix / "share" / "pkgconfig"),
    ]


def test_lock_has_valid_checksums_and_license_metadata() -> None:
    sources, revision = load_sources(ROOT / "sources.lock.toml")
    assert revision >= 1
    assert sources["ffmpeg"].sha256 == (
        "8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e"
    )
    assert {
        "implib",
        "lc3",
        "mysofa",
        "qrencode",
        "quirc",
        "moltenvk",
        "llvm_mingw",
    } <= sources.keys()
    assert all(source.license and source.license_files for source in sources.values())

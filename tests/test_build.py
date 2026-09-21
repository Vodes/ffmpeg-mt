from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

import ffbuild.helpers as helpers
import ffbuild.package as package_module
import ffbuild.recipes as recipes
import ffbuild.validate as validation
from ffbuild.ffmpeg import configure_flags
from ffbuild.helpers import download, download_url, extract, fetch_text
from ffbuild.model import BuildContext, Source, load_sources
from ffbuild.package import (
    _copy_rust_toolchain_notices,
    _write_tar,
    artifact_name,
)
from ffbuild.recipes import selected_recipes
from ffbuild.targets import TARGETS
from ffbuild.validate import (
    _assert_lazy_vaapi,
    _assert_no_staged_shared_libraries,
    _assert_runtime_dependencies,
    _scan_paths,
    _vulkan_smoke,
    assert_configure_flags,
)
from scripts.build_container import run_in_container
from scripts.update_ffmpeg import INDEX, stable_versions, update_lock

ROOT = Path(__file__).parents[1]


def context(tmp_path: Path, target: str, nonfree: bool = False) -> BuildContext:
    sources, _ = load_sources(ROOT / "sources.lock.toml")
    return BuildContext(tmp_path, TARGETS[target], sources, 2, nonfree)


def test_archive_members_are_unique(tmp_path: Path) -> None:
    source = tmp_path / "artifact"
    nested = source / "licenses" / "dependency"
    nested.mkdir(parents=True)
    (source / "bin").mkdir()
    (source / "bin" / "ffmpeg").write_bytes(b"binary")
    (nested / "LICENSE").write_text("license\n", encoding="utf-8")

    archive = tmp_path / "artifact.tar"
    _write_tar(source, archive, 123456789)

    with tarfile.open(archive) as bundle:
        members = bundle.getmembers()
        names = [member.name for member in members]
    assert len(names) == len(set(names))
    assert {member.mtime for member in members} == {123456789}
    assert names == [
        "artifact/bin",
        "artifact/bin/ffmpeg",
        "artifact/licenses",
        "artifact/licenses/dependency",
        "artifact/licenses/dependency/LICENSE",
    ]


def test_windows_rust_toolchain_is_pinned_with_precompiled_targets() -> None:
    dockerfile = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")

    assert "ARG RUST_VERSION=1.92.0" in dockerfile
    assert "ARG RUSTUP_VERSION=1.28.2" in dockerfile
    assert "--component rust-docs" in dockerfile
    assert "x86_64-pc-windows-gnu aarch64-pc-windows-gnullvm" in dockerfile
    assert "rust-src" not in dockerfile
    assert "ENV RUSTUP_HOME=/opt/rustup" in dockerfile
    assert "ENV CARGO_HOME=/opt/cargo" in dockerfile
    assert "ENV PATH=/opt/cargo/bin:${PATH}" in dockerfile
    assert "ENV PATH=/root/.cargo/bin:${PATH}" not in dockerfile


def test_macos_uses_the_same_pinned_rustup_toolchain() -> None:
    workflow = (ROOT / ".github" / "workflows" / "build.yml").read_text(encoding="utf-8")

    assert 'RUST_VERSION: "1.92.0"' in workflow
    assert 'RUSTUP_VERSION: "1.28.2"' in workflow
    assert (
        'RUSTUP_SHA256: "20ef5516c31b1ac2290084199ba77dbbcaa1406c45c1d978ca68558ef5964ef5"'
        in workflow
    )
    assert "aarch64-apple-darwin/rustup-init" in workflow
    assert "--profile minimal --component rust-docs" in workflow
    assert "pkg-config rust shaderc" not in workflow


def test_rust_toolchain_notices_include_std_and_registry_licenses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sysroot = tmp_path / "sysroot"
    rust_source = sysroot / "lib" / "rustlib" / "src" / "rust"
    rust_source.mkdir(parents=True)
    (rust_source / "COPYRIGHT").write_text("copyright\n", encoding="utf-8")
    (rust_source / "LICENSE-APACHE").write_text("apache\n", encoding="utf-8")
    (sysroot / "share" / "doc" / "rust" / "licenses").mkdir(parents=True)
    (sysroot / "share" / "doc" / "rust" / "licenses" / "MIT.txt").write_text(
        "mit\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        package_module,
        "run",
        lambda *_args, **_kwargs: f"{sysroot}\n",
    )

    destination = tmp_path / "licenses"
    _copy_rust_toolchain_notices(destination)

    assert (destination / "rust-std" / "rust-src" / "COPYRIGHT").read_text() == "copyright\n"
    assert (destination / "rust-std" / "rust-src" / "LICENSE-APACHE").read_text() == "apache\n"
    assert (destination / "rust-std" / "rust-toolchain-licenses" / "MIT.txt").read_text() == "mit\n"


def test_windows_runtime_dependencies_ignore_export_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "windows-arm64")
    output = """
    DLL Name: AVRT.dll
    DLL Name: DWrite.dll
    DLL Name: IPHLPAPI.DLL
    DLL Name: api-ms-win-crt-conio-l1-1-0.dll
    DLL Name: api-ms-win-crt-multibyte-l1-1-0.dll
    DLL Name: api-ms-win-crt-utility-l1-1-0.dll
 DLL name: ffmpeg_g.exe
"""
    monkeypatch.setattr(validation, "run", lambda *_args, **_kwargs: output)

    _assert_runtime_dependencies(ctx, tmp_path / "ffmpeg.exe")


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


def test_moltenvk_installs_vulkan_loader_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "macos-arm64")
    source = tmp_path / "moltenvk"
    library = source / "MoltenVK.xcframework" / "macos-arm64" / "libMoltenVK.a"
    library.parent.mkdir(parents=True)
    library.write_bytes(b"moltenvk")
    (source / "MoltenVK" / "include").mkdir(parents=True)
    ctx.prefix.mkdir(parents=True)
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)

    recipes.install_moltenvk(ctx)

    alias = ctx.prefix / "lib" / "libvulkan.a"
    assert alias.is_symlink()
    assert alias.readlink() == Path("libMoltenVK.a")
    assert alias.resolve().read_bytes() == b"moltenvk"
    pkg_config = (ctx.prefix / "lib" / "pkgconfig" / "vulkan.pc").read_text(encoding="utf-8")
    assert "Libs: -L${libdir} -lvulkan\n" in pkg_config
    assert "Libs.private: -lc++ -framework Metal" in pkg_config


def test_target_shaderc_installs_static_metadata_and_spirv_headers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "linux-x86_64")
    sources: dict[str, Path] = {}
    for name in (
        "shaderc",
        "shaderc_abseil",
        "shaderc_effcee",
        "shaderc_glslang",
        "shaderc_googletest",
        "shaderc_re2",
        "shaderc_spirv_headers",
        "shaderc_spirv_tools",
    ):
        source = tmp_path / f"{name}-source"
        source.mkdir()
        sources[name] = source
    spirv_header = sources["shaderc_spirv_headers"] / "include" / "spirv" / "unified1"
    spirv_header.mkdir(parents=True)
    (spirv_header / "spirv.h").write_text("header\n", encoding="utf-8")
    monkeypatch.setattr(recipes, "extract", lambda _ctx, name: sources[name])

    def fake_cmake(_ctx: BuildContext, source: Path, *_args: object) -> None:
        build_dir = ctx.work_root / source.name
        (build_dir / "libshaderc_util").mkdir(parents=True)
        (build_dir / "libshaderc_util" / "libshaderc_util.a").write_bytes(b"archive")
        pkg_config = ctx.prefix / "lib" / "pkgconfig"
        pkg_config.mkdir(parents=True)
        (pkg_config / "shaderc_combined.pc").write_text(
            "Libs: -lshaderc_combined\n", encoding="utf-8"
        )
        (ctx.prefix / "lib" / "libshaderc_shared.so").touch()
        (ctx.prefix / "lib" / "libSPIRV-Tools-shared.so").touch()
        (ctx.prefix / "bin").mkdir(parents=True)
        (ctx.prefix / "bin" / "glslc").touch()

    monkeypatch.setattr(recipes, "cmake", fake_cmake)
    recipes.build_shaderc(ctx)

    assert (ctx.prefix / "include" / "spirv-headers" / "spirv.h").is_file()
    assert (ctx.prefix / "lib" / "libshaderc_util.a").is_file()
    assert not (ctx.prefix / "lib" / "libshaderc_shared.so").exists()
    assert not (ctx.prefix / "lib" / "libSPIRV-Tools-shared.so").exists()
    assert not (ctx.prefix / "bin" / "glslc").exists()
    assert "-lstdc++" in (ctx.prefix / "lib" / "pkgconfig" / "shaderc.pc").read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize(
    ("target", "machine", "stage", "base_arch", "nonfree"),
    (
        ("linux-x86_64", "x86_64", "linux_builder", "x86_64", False),
        ("windows-arm64", "aarch64", "builder", "aarch64", True),
    ),
)
def test_container_selects_only_the_required_image_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    machine: str,
    stage: str,
    base_arch: str,
    nonfree: bool,
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr("scripts.build_container.platform.machine", lambda: machine)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")
    monkeypatch.setattr(
        "scripts.build_container.subprocess.run",
        lambda command, check: commands.append(command),
    )

    run_in_container(tmp_path, target, 2, nonfree, False)

    assert commands[0][commands[0].index("--target") + 1] == stage
    base_image = commands[0][commands[0].index("--build-arg") + 1]
    assert f"manylinux_2_34_{base_arch}@sha256:" in base_image
    assert ("--nonfree" in commands[1]) is nonfree
    assert "SOURCE_DATE_EPOCH=0" in commands[1]


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
    assert names.index("shaderc") < names.index("libplacebo")
    assert names.index("libdovi") < names.index("libplacebo")
    if target.startswith("windows-"):
        assert names.index("SPIRV-Cross") < names.index("libplacebo")
    else:
        assert "SPIRV-Cross" not in names
    assert ("libva" in names) is target.startswith("linux-")
    assert ("Implib.so" in names) is target.startswith("linux-")
    if target.startswith("linux-"):
        assert names.index("Implib.so") < names.index("libdrm") < names.index("libva")
    assert ("MoltenVK" in names) is (target == "macos-arm64")
    assert "fdk-aac" not in names
    assert "openssl" in names
    assert "ssh" in names
    assert ("nv-codec-headers" in names) is (target != "macos-arm64")
    assert ("amf-headers" in names) is (target != "macos-arm64")


@pytest.mark.parametrize("target", TARGETS)
def test_configure_manifest_and_nonfree_separation(tmp_path: Path, target: str) -> None:
    default = context(tmp_path, target)
    default_flags = configure_flags(default, 1)
    assert_configure_flags(ROOT, target, default_flags, False)
    assert len(default_flags) == len(set(default_flags))
    assert "--enable-nonfree" not in default_flags
    assert "--enable-libfdk-aac" not in default_flags
    if target.startswith("windows-"):
        assert "--pkg-config=pkg-config" in default_flags
    if target == "macos-arm64":
        extra_libraries = next(flag for flag in default_flags if flag.startswith("--extra-libs="))
        assert "-lc++" in extra_libraries
        assert "-framework AppKit" in extra_libraries
        assert "-framework CoreGraphics" in extra_libraries

    nonfree = context(tmp_path, target, True)
    nonfree_flags = configure_flags(nonfree, 1)
    assert_configure_flags(ROOT, target, nonfree_flags, True)
    assert {"--enable-nonfree", "--enable-libfdk-aac"} <= set(nonfree_flags)


def test_extra_version_includes_source_date_and_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "0")

    flags = configure_flags(context(tmp_path, "linux-x86_64"), 7)

    assert "--extra-version=ffmt.19700101.r7" in flags


def test_build_timestamp_defaults_to_current_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    monkeypatch.setattr("ffbuild.model.time.time", lambda: 1234567890)

    ctx = context(tmp_path, "linux-x86_64")

    assert ctx.build_epoch == 1234567890
    assert ctx.env()["SOURCE_DATE_EPOCH"] == "1234567890"


def test_artifact_names(tmp_path: Path) -> None:
    assert artifact_name(context(tmp_path, "linux-x86_64")) == ("ffmpeg-9.0.2-linux-x86_64.tar.zst")
    assert artifact_name(context(tmp_path, "macos-arm64", True)) == (
        "ffmpeg-9.0.2-macos-arm64-nonfree.tar.zst"
    )


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


def test_shared_library_debug_helpers_are_not_treated_as_libraries(tmp_path: Path) -> None:
    ctx = context(tmp_path, "linux-x86_64")
    ctx.prepare(clean=False)
    helper = ctx.prefix / "share" / "gdb" / "libglib-2.0.so.0.8400.3-gdb.py"
    helper.parent.mkdir(parents=True)
    helper.write_text("# GDB helper\n", encoding="utf-8")

    _assert_no_staged_shared_libraries(ctx)


def test_lazy_vaapi_requires_drm_and_va_shims(tmp_path: Path) -> None:
    ctx = context(tmp_path, "linux-x86_64")
    ffmpeg = tmp_path / "ffmpeg"
    ffmpeg.write_bytes(b"libva.so.2\0libva-drm.so.2\0")

    with pytest.raises(RuntimeError, match="libdrm.so.2"):
        _assert_lazy_vaapi(ctx, ffmpeg)

    ffmpeg.write_bytes(b"libdrm.so.2\0libva.so.2\0libva-drm.so.2\0")
    _assert_lazy_vaapi(ctx, ffmpeg)


def test_path_scan_allows_relative_source_paths_but_rejects_workspace_paths(
    tmp_path: Path,
) -> None:
    ctx = context(tmp_path / "workspace", "linux-x86_64")
    binary = tmp_path / "ffmpeg"
    binary.write_bytes(b"../../src/library.c\0")
    _scan_paths(ctx, (binary, binary))

    binary.write_bytes(f"error in {ctx.root}/library.c\0".encode())
    with pytest.raises(RuntimeError, match="leaks build paths"):
        _scan_paths(ctx, (binary, binary))


def test_only_numbered_stable_archives_are_selected() -> None:
    index = """
      <a href="ffmpeg-9.0.2.tar.xz">stable</a>
      <a href="ffmpeg-10.0-rc1.tar.xz">prerelease</a>
      <a href="ffmpeg-snapshot.tar.xz">snapshot</a>
      <a href="ffmpeg-8.1.12.tar.xz">old stable</a>
    """
    assert stable_versions(index) == ["8.1.12", "9.0.2"]


def test_vulkan_smoke_skips_linux_arm64(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_run(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Linux ARM64 has no software Vulkan runtime in AlmaLinux 9")

    monkeypatch.setattr("ffbuild.validate.run", unexpected_run)
    _vulkan_smoke(context(tmp_path, "linux-arm64"), tmp_path / "ffmpeg")


def test_vulkan_smoke_exercises_libplacebo_runtime_compilation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[tuple[object, ...]] = []
    monkeypatch.setattr("ffbuild.validate.run", lambda *args: commands.append(args))

    _vulkan_smoke(context(tmp_path, "linux-x86_64"), tmp_path / "ffmpeg")

    assert len(commands) == 2
    assert any("libplacebo=" in str(argument) for argument in commands[1])


def test_glib_stages_declared_libintl_fallback_on_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    glib = tmp_path / "glib"
    (glib / "subprojects").mkdir(parents=True)
    proxy_libintl = tmp_path / "proxy-libintl"
    proxy_libintl.mkdir()
    (proxy_libintl / "meson.build").write_text("project('proxy-libintl')\n", encoding="utf-8")

    sources = {"glib": glib, "proxy_libintl": proxy_libintl}
    monkeypatch.setattr(recipes, "extract", lambda _ctx, name: sources[name])
    monkeypatch.setattr(recipes, "meson", lambda *_args, **_kwargs: None)

    recipes.build_glib(context(tmp_path, "macos-arm64"))

    staged = glib / "subprojects" / "proxy-libintl" / "meson.build"
    assert staged.read_text(encoding="utf-8") == "project('proxy-libintl')\n"


def test_rsvg_build_omits_unneeded_converter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "macos-arm64")
    source = tmp_path / "librsvg"
    source.mkdir()
    meson_build = source / "meson.build"
    meson_build.write_text("subdir('rsvg')\nsubdir('rsvg_convert')\n", encoding="utf-8")
    meson_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)
    monkeypatch.setattr(
        recipes,
        "run",
        lambda *_args, **_kwargs: 'source = "vendor"\n',
    )
    monkeypatch.setattr(recipes, "meson", lambda *args: meson_calls.append(args))

    recipes.build_rsvg(ctx)

    assert meson_build.read_text(encoding="utf-8") == "subdir('rsvg')\n"
    assert meson_calls[0][1] == source


def test_libssh_static_pkg_config_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = context(tmp_path, "linux-x86_64")
    source = tmp_path / "libssh"
    source.mkdir()
    pkg_config = ctx.prefix / "lib" / "pkgconfig" / "libssh.pc"
    cmake_calls: list[tuple[object, ...]] = []

    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)

    def fake_cmake(*args: object) -> None:
        cmake_calls.append(args)
        pkg_config.parent.mkdir(parents=True)
        pkg_config.write_text("Name: libssh\nLibs: -lssh\n", encoding="utf-8")

    monkeypatch.setattr(recipes, "cmake", fake_cmake)
    recipes.build_ssh(ctx)

    assert "-DWITH_GSSAPI=OFF" in cmake_calls[0]
    metadata = pkg_config.read_text(encoding="utf-8")
    assert "Requires.private: libssl libcrypto zlib" in metadata
    assert "Cflags.private: -DLIBSSH_STATIC" in metadata
    assert "Libs.private: -lpthread" in metadata


def test_windows_libssh_declares_strndup_after_configure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "windows-x86_64")
    source = tmp_path / "libssh"
    source.mkdir()
    captured: dict[str, object] = {}
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)

    def fake_cmake(*args: object, **kwargs: object) -> None:
        captured["args"] = args
        build_dir = tmp_path / "configured"
        build_dir.mkdir()
        (build_dir / "config.h").write_text("#define HAVE_STRNDUP 1\n", encoding="utf-8")
        kwargs["after_configure"](build_dir)  # type: ignore[operator]
        pkg_config = ctx.prefix / "lib" / "pkgconfig" / "libssh.pc"
        pkg_config.parent.mkdir(parents=True)
        pkg_config.write_text("Name: libssh\nLibs: -lssh\n", encoding="utf-8")
        captured["config"] = (build_dir / "config.h").read_text(encoding="utf-8")

    monkeypatch.setattr(recipes, "cmake", fake_cmake)
    recipes.build_ssh(ctx)

    assert "-DHAVE_STRNDUP=YES" in captured["args"]  # type: ignore[operator]
    assert "char *strndup(const char *, size_t);" in captured["config"]  # type: ignore[operator]


def test_windows_arm64_openssl_uses_arm_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "windows-arm64")
    source = tmp_path / "openssl"
    source.mkdir()
    commands: list[tuple[object, ...]] = []
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)
    monkeypatch.setattr(recipes, "run", lambda *args, **_kwargs: commands.append(args))
    monkeypatch.setattr(recipes, "_set_pkg_config_variables", lambda *_args, **_kwargs: None)

    recipes.build_openssl(ctx)

    assert commands[0][1] == "mingwarm64"


def test_static_opencl_pkg_config_has_platform_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for target, expected in (
        ("linux-x86_64", {"-ldl", "-pthread"}),
        ("windows-x86_64", {"-lole32", "-lshlwapi", "-lcfgmgr32"}),
    ):
        ctx = context(tmp_path / target, target)
        source = tmp_path / target / "opencl"
        source.mkdir(parents=True)
        monkeypatch.setattr(recipes, "extract", lambda _ctx, _name, source=source: source)

        def fake_cmake(*_args: object, ctx: BuildContext = ctx) -> None:
            pkg_config = ctx.prefix / "lib" / "pkgconfig" / "OpenCL.pc"
            pkg_config.parent.mkdir(parents=True)
            pkg_config.write_text("Libs: -lOpenCL\n", encoding="utf-8")

        monkeypatch.setattr(recipes, "cmake", fake_cmake)
        recipes.build_opencl_loader(ctx)
        metadata = (ctx.prefix / "lib" / "pkgconfig" / "OpenCL.pc").read_text(encoding="utf-8")
        assert expected <= set(metadata.split())
        if ctx.target.windows:
            assert "-l:OpenCL.a" in metadata
            assert "-lOpenCL" not in metadata


def test_windows_openal_pkg_config_has_com_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "windows-x86_64")
    source = tmp_path / "openal"
    fmt_header = source / "fmt-11.1.1" / "include" / "fmt" / "format.h"
    fmt_header.parent.mkdir(parents=True)
    fmt_header.write_text("#  include <cstdint>  // uint32_t\n", encoding="utf-8")
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)

    def fake_cmake(*_args: object) -> None:
        pkg_config = ctx.prefix / "lib" / "pkgconfig" / "openal.pc"
        pkg_config.parent.mkdir(parents=True)
        pkg_config.write_text("Libs: -lopenal\n", encoding="utf-8")

    monkeypatch.setattr(recipes, "cmake", fake_cmake)
    recipes.build_openal(ctx)

    metadata = (ctx.prefix / "lib" / "pkgconfig" / "openal.pc").read_text(encoding="utf-8")
    assert {"-lole32", "-luuid", "-lc++"} <= set(metadata.split())


@pytest.mark.parametrize(
    ("target", "encryption"),
    (("linux-x86_64", "ON"), ("windows-x86_64", "ON"), ("windows-arm64", "ON")),
)
def test_srt_encryption_tracks_openssl_availability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str, encryption: str
) -> None:
    ctx = context(tmp_path, target)
    source = tmp_path / "srt"
    source.mkdir()
    pkg_config = ctx.prefix / "lib" / "pkgconfig" / "srt.pc"
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)

    def fake_cmake(*args: object) -> None:
        calls.append(args)
        pkg_config.parent.mkdir(parents=True)
        pkg_config.write_text("Libs: -lsrt\n", encoding="utf-8")

    monkeypatch.setattr(recipes, "cmake", fake_cmake)
    recipes.build_srt(ctx)

    assert f"-DENABLE_ENCRYPTION={encryption}" in calls[0]


def test_windows_arm64_rist_declares_gettimeofday(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "windows-arm64")
    source = tmp_path / "rist"
    timing_source = source / "contrib" / "mbedtls" / "library" / "timing.c"
    timing_source.parent.mkdir(parents=True)
    timing_source.write_text("#include <windows.h>\n#include <process.h>\n", encoding="utf-8")
    meson_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)
    monkeypatch.setattr(recipes, "meson", lambda *args: meson_calls.append(args))

    recipes.build_rist(ctx)

    assert "#include <process.h>\n#include <sys/time.h>\n" in timing_source.read_text(
        encoding="utf-8"
    )
    assert meson_calls[0][1] == source


def test_aribcaption_uses_a_linker_flag_for_the_static_cpp_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "linux-x86_64")
    source = tmp_path / "aribcaption"
    source.mkdir()
    pkg_config = ctx.prefix / "lib" / "pkgconfig" / "libaribcaption.pc"
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)

    def fake_cmake(*_args: object) -> None:
        pkg_config.parent.mkdir(parents=True)
        pkg_config.write_text(
            "Libs: -L${libdir} -laribcaption /toolchain/libstdc++.a -lm\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(recipes, "cmake", fake_cmake)

    recipes.build_aribcaption(ctx)

    assert pkg_config.read_text(encoding="utf-8") == (
        "Libs: -L${libdir} -laribcaption -lstdc++ -lm\n"
    )


def test_onevpl_declares_its_static_cpp_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "linux-x86_64")
    source = tmp_path / "vpl"
    source.mkdir()
    pkg_config = ctx.prefix / "lib" / "pkgconfig" / "vpl.pc"
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)

    cmake_kwargs: dict[str, object] = {}

    def fake_cmake(*_args: object, **kwargs: object) -> None:
        cmake_kwargs.update(kwargs)
        pkg_config.parent.mkdir(parents=True)
        pkg_config.write_text(
            "Libs: -L${libdir} -lvpl -ldl\nLibs.private:\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(recipes, "cmake", fake_cmake)

    recipes.build_vpl(ctx)

    assert cmake_kwargs == {"install_prefix": "/", "install_destdir": ctx.prefix}
    assert "Libs.private: -lstdc++\n" in pkg_config.read_text(encoding="utf-8")


def test_libdovi_builds_a_static_c_api_for_libplacebo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "windows-x86_64")
    source = tmp_path / "dovi"
    (source / "dolby_vision").mkdir(parents=True)
    commands: list[tuple[tuple[object, ...], dict[str, object]]] = []

    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)

    def fake_run(*args: object, **kwargs: object) -> str:
        commands.append((args, kwargs))
        return '[source.vendored-sources]\ndirectory = "vendor"\n' if kwargs.get("capture") else ""

    monkeypatch.setattr(recipes, "run", fake_run)

    recipes.build_libdovi(ctx)

    vendor_args = commands[0][0]
    assert vendor_args[:3] == ("cargo", "vendor", "--locked")
    assert "--sync" not in vendor_args
    vendor_env = commands[0][1].get("env")
    assert isinstance(vendor_env, dict)
    assert "RUSTC_BOOTSTRAP" not in vendor_env
    install_args = commands[-1][0]
    assert install_args[:2] == ("cargo", "cinstall")
    assert "--features=capi" in install_args
    assert "--library-type=staticlib" in install_args
    assert "--target=x86_64-pc-windows-gnu" in install_args
    assert "-Z" not in install_args


def test_libplacebo_enables_dolby_vision_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "linux-x86_64")
    source = tmp_path / "placebo"
    source.mkdir()
    submodules = {}
    for name in (
        "placebo_vulkan",
        "placebo_fast_float",
        "placebo_glad",
        "placebo_jinja",
        "placebo_markupsafe",
    ):
        submodule = tmp_path / name
        submodule.mkdir()
        submodules[name] = submodule
    meson_calls: list[tuple[object, ...]] = []

    def fake_extract(_ctx: BuildContext, name: str) -> Path:
        return source if name == "placebo" else submodules[name]

    monkeypatch.setattr(recipes, "extract", fake_extract)
    monkeypatch.setattr(recipes, "meson", lambda *args: meson_calls.append(args))
    monkeypatch.setattr(recipes, "_ensure_static_cpp_runtime", lambda *_args: None)

    recipes.build_placebo(ctx)

    assert "-Ddovi=enabled" in meson_calls[0]
    assert "-Dlibdovi=enabled" in meson_calls[0]


@pytest.mark.parametrize(
    ("target", "runtime"),
    [("linux-x86_64", "-lstdc++"), ("windows-x86_64", "-lc++"), ("macos-arm64", "-lc++")],
)
def test_static_cpp_runtime_metadata_uses_the_target_toolchain(
    tmp_path: Path, target: str, runtime: str
) -> None:
    ctx = context(tmp_path, target)
    pkg_config = tmp_path / f"{target}.pc"
    pkg_config.write_text("Libs: -L${libdir} -lexample\nLibs.private: -lm\n", encoding="utf-8")

    recipes._ensure_static_cpp_runtime(ctx, pkg_config)

    assert f"Libs.private: -lm {runtime}\n" in pkg_config.read_text(encoding="utf-8")


def test_rubberband_declares_its_private_math_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "linux-x86_64")
    source = tmp_path / "rubberband"
    source.mkdir()
    pkg_config = ctx.prefix / "lib" / "pkgconfig" / "rubberband.pc"
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)

    def fake_meson(*_args: object) -> None:
        pkg_config.parent.mkdir(parents=True)
        pkg_config.write_text("Libs: -L${libdir} -lrubberband\n", encoding="utf-8")

    monkeypatch.setattr(recipes, "meson", fake_meson)

    recipes.build_rubberband(ctx)

    assert "Libs.private: -lm -lstdc++\n" in pkg_config.read_text(encoding="utf-8")


def test_rubberband_macos_includes_cstddef_for_libcxx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "macos-arm64")
    source = tmp_path / "rubberband"
    header = source / "src" / "common" / "mathmisc.h"
    header.parent.mkdir(parents=True)
    header.write_text('#include "sysutils.h"\n', encoding="utf-8")
    pkg_config = ctx.prefix / "lib" / "pkgconfig" / "rubberband.pc"
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)

    def fake_meson(*_args: object) -> None:
        pkg_config.parent.mkdir(parents=True)
        pkg_config.write_text("Libs: -lrubberband\n", encoding="utf-8")

    monkeypatch.setattr(recipes, "meson", fake_meson)

    recipes.build_rubberband(ctx)

    assert header.read_text(encoding="utf-8") == '#include "sysutils.h"\n#include <cstddef>\n'


def test_libdrm_shared_library_is_kept_until_libva_shims_are_generated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "linux-x86_64")
    source = tmp_path / "source"
    source.mkdir()
    library_dir = ctx.prefix / "lib"
    pkg_config_dir = library_dir / "pkgconfig"
    commands: list[tuple[object, ...]] = []
    generated: list[str] = []
    ctx.source_dirs["implib"] = source
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)

    def fake_run(*args: object, **_kwargs: object) -> None:
        commands.append(args)
        if args[:2] == ("meson", "install"):
            pkg_config_dir.mkdir(parents=True, exist_ok=True)
            if "libdrm" in str(args):
                (library_dir / "libdrm.so.2").write_bytes(b"shared")
                (pkg_config_dir / "libdrm.pc").write_text("Libs: -ldrm\n", encoding="utf-8")

    def fake_generate(_ctx: BuildContext, _implib: Path, shared: Path, output: Path) -> None:
        generated.append(shared.name)
        output.write_bytes(b"import")

    monkeypatch.setattr(recipes, "run", fake_run)
    monkeypatch.setattr(recipes, "_generate_import_library", fake_generate)

    recipes.build_libdrm(ctx)

    assert "--default-library=shared" in commands[0]
    assert (library_dir / "libdrm.so.2").exists()

    (library_dir / "libva.so.2").write_bytes(b"shared")
    (library_dir / "libva-drm.so.2").write_bytes(b"shared")
    (pkg_config_dir / "libva.pc").write_text("Libs: -lva\n", encoding="utf-8")
    (pkg_config_dir / "libva-drm.pc").write_text("Libs: -lva-drm\n", encoding="utf-8")
    recipes.build_libva(ctx)

    assert generated == ["libdrm.so.2", "libva.so.2", "libva-drm.so.2"]
    assert not list(library_dir.glob("libdrm*.so*"))
    assert not list(library_dir.glob("libva*.so*"))


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
    assert "-fPIC" in env["CFLAGS"]
    assert "-fPIC" in env["CXXFLAGS"]
    assert "-static-libgcc" in env["LDFLAGS"]
    assert "-static-libstdc++" in env["LDFLAGS"]
    assert "--remap-path-prefix=/opt/homebrew=." not in env["RUSTFLAGS"]

    windows_env = context(tmp_path, "windows-x86_64").env()
    assert "-fPIC" not in windows_env["CFLAGS"]
    assert "-fPIC" not in windows_env["CXXFLAGS"]
    assert "-static-libgcc" not in windows_env["LDFLAGS"]
    assert "-static-libstdc++" not in windows_env["LDFLAGS"]

    macos_env = context(tmp_path, "macos-arm64").env()
    assert "--remap-path-prefix=/opt/homebrew=." in macos_env["RUSTFLAGS"]


def test_sources_declare_license_metadata() -> None:
    sources, _ = load_sources(ROOT / "sources.lock.toml")
    assert all(source.license and source.license_files for source in sources.values())

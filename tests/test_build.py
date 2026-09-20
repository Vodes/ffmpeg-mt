from __future__ import annotations

import hashlib
import inspect
import io
import tarfile
from pathlib import Path

import pytest

import ffbuild.helpers as helpers
import ffbuild.recipes as recipes
from ffbuild.ffmpeg import configure_flags
from ffbuild.helpers import download, download_url, extract, fetch_text
from ffbuild.model import BuildContext, Source, load_sources
from ffbuild.package import (
    _write_tar,
    artifact_name,
    private_upload_allowed,
    public_release_allowed,
)
from ffbuild.recipes import selected_recipes
from ffbuild.targets import TARGETS
from ffbuild.validate import (
    COMPILED_FEATURE_NAMES,
    _assert_lazy_vaapi,
    _assert_no_staged_shared_libraries,
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
    _write_tar(source, archive)

    with tarfile.open(archive) as bundle:
        names = bundle.getnames()
    assert len(names) == len(set(names))
    assert names == [
        "artifact/bin",
        "artifact/bin/ffmpeg",
        "artifact/licenses",
        "artifact/licenses/dependency",
        "artifact/licenses/dependency/LICENSE",
    ]


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


def test_shaderc_builder_builds_the_glslc_executable_target() -> None:
    dockerfile = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert "cmake --build build --target glslc_exe --parallel" in dockerfile
    assert "install -m 0755 build/glslc/glslc /usr/local/bin/glslc" in dockerfile


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


def test_container_pins_compatible_meson() -> None:
    dockerfile = (ROOT / "docker" / "Dockerfile").read_text(encoding="utf-8")
    assert "pip install --no-cache-dir meson==1.9.1 uv==0.8.22" in dockerfile


def test_vulkan_loader_uses_btbns_static_shim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []
    shim = tmp_path / "shim"
    headers = tmp_path / "headers"
    (shim / "Vulkan-Headers").mkdir(parents=True)
    (headers / "include").mkdir(parents=True)
    (headers / "include" / "vulkan.h").touch()
    ctx = context(tmp_path, "linux-x86_64")
    ctx.source_dirs["vulkan_headers"] = headers
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: shim)
    monkeypatch.setattr(recipes, "cmake", lambda *args: calls.append(args))

    recipes.build_vulkan_loader(ctx)

    assert (shim / "Vulkan-Headers" / "include" / "vulkan.h").exists()
    assert calls == [(ctx, shim, "-DVULKAN_SHIM_IMPERSONATE=ON")]


def test_mysofa_uses_a_relocatable_logical_install_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "linux-x86_64")
    source = tmp_path / "mysofa"
    source.mkdir()
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)

    def fake_cmake(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))
        pkg_config = ctx.prefix / "lib" / "pkgconfig" / "libmysofa.pc"
        pkg_config.parent.mkdir(parents=True)
        pkg_config.write_text("prefix=/\nlibdir=/lib\nincludedir=/include\n", encoding="utf-8")

    monkeypatch.setattr(recipes, "cmake", fake_cmake)

    recipes.build_mysofa(ctx)

    assert "-DCMAKE_INSTALL_LIBDIR=/lib" in calls[0][0]
    assert "-DCMAKE_INSTALL_INCLUDEDIR=/include" in calls[0][0]
    assert "-DCMAKE_INSTALL_DATADIR=/share" in calls[0][0]
    assert calls[0][1] == {"install_prefix": "/", "install_destdir": ctx.prefix}
    metadata = (ctx.prefix / "lib" / "pkgconfig" / "libmysofa.pc").read_text(encoding="utf-8")
    assert f"prefix={ctx.prefix}" in metadata


def test_openssl_relocates_pkg_config_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "linux-x86_64")
    source = tmp_path / "openssl"
    source.mkdir()
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)

    def fake_run(*args: object, **_kwargs: object) -> None:
        if "install_sw" not in args:
            return
        pkg_config = ctx.prefix / "lib" / "pkgconfig"
        pkg_config.mkdir(parents=True)
        for name in ("libcrypto.pc", "libssl.pc", "openssl.pc"):
            (pkg_config / name).write_text(
                "prefix=/\nlibdir=${prefix}/lib\nincludedir=${prefix}/include\n",
                encoding="utf-8",
            )

    monkeypatch.setattr(recipes, "run", fake_run)

    recipes.build_openssl(ctx)

    for name in ("libcrypto.pc", "libssl.pc", "openssl.pc"):
        metadata = (ctx.prefix / "lib" / "pkgconfig" / name).read_text(encoding="utf-8")
        assert f"prefix={ctx.prefix}" in metadata


@pytest.mark.parametrize(
    ("target", "stage"),
    (("linux-x86_64", "linux_builder"), ("windows-x86_64", "builder")),
)
def test_container_selects_only_the_required_image_stage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    stage: str,
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr("scripts.build_container.platform.machine", lambda: "x86_64")
    monkeypatch.setattr(
        "scripts.build_container.subprocess.run",
        lambda command, check: commands.append(command),
    )

    run_in_container(tmp_path, target, 2, False, False)

    assert commands[0][commands[0].index("--target") + 1] == stage


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
    assert ("libva" in names) is target.startswith("linux-")
    assert ("Implib.so" in names) is target.startswith("linux-")
    if target.startswith("linux-"):
        assert names.index("Implib.so") < names.index("libdrm") < names.index("libva")
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


def test_external_protocol_runtime_names_map_to_ffmpeg_config_macros() -> None:
    assert COMPILED_FEATURE_NAMES[("protocols", "rist")] == "librist"
    assert COMPILED_FEATURE_NAMES[("protocols", "sftp")] == "libssh"
    assert COMPILED_FEATURE_NAMES[("protocols", "srt")] == "libsrt"


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


def test_fontconfig_uses_a_relocatable_logical_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "linux-x86_64")
    source = tmp_path / "fontconfig"
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr("ffbuild.recipes.extract", lambda *_args: source)
    monkeypatch.setattr(
        "ffbuild.recipes.meson", lambda *args, **kwargs: calls.append((args, kwargs))
    )

    recipes.build_fontconfig(ctx)

    assert calls == [
        (
            (
                ctx,
                source,
                "-Ddoc=disabled",
                "-Dtests=disabled",
                "-Dtools=disabled",
                "-Dpkgconfig.relocatable=true",
            ),
            {"install_prefix": "/", "install_destdir": ctx.prefix},
        )
    ]


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


def test_openssl_disables_shared_provider_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    commands: list[tuple[object, ...]] = []
    source = tmp_path / "openssl"
    source.mkdir()
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)
    monkeypatch.setattr(recipes, "run", lambda *args, **_kwargs: commands.append(args))
    monkeypatch.setattr(recipes, "_set_pkg_config_variables", lambda *_args: None)

    recipes.build_openssl(context(tmp_path, "linux-x86_64"))

    assert "--prefix=/" in commands[0]
    assert "--openssldir=/ssl" in commands[0]
    assert "no-shared" in commands[0]
    assert "no-module" in commands[0]
    assert any(str(command).startswith("DESTDIR=") for command in commands[2])


def test_libxml2_uses_cmake_without_runtime_modules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    source = tmp_path / "libxml2"
    source.mkdir()
    ctx = context(tmp_path, "linux-x86_64")
    pkg_config = ctx.prefix / "lib" / "pkgconfig" / "libxml-2.0.pc"
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)

    def fake_cmake(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))
        pkg_config.parent.mkdir(parents=True)
        pkg_config.write_text("prefix=/\n", encoding="utf-8")

    monkeypatch.setattr(recipes, "cmake", fake_cmake)
    recipes.build_xml2(ctx)

    assert calls == [
        (
            (
                ctx,
                source,
                "-DCMAKE_INSTALL_BINDIR=/bin",
                "-DCMAKE_INSTALL_LIBDIR=/lib",
                "-DCMAKE_INSTALL_INCLUDEDIR=/include",
                "-DCMAKE_INSTALL_DATAROOTDIR=/share",
                "-DCMAKE_INSTALL_DATADIR=/share",
                "-DCMAKE_INSTALL_SYSCONFDIR=/etc",
                "-DLIBXML2_WITH_MODULES=OFF",
                "-DLIBXML2_WITH_PROGRAMS=OFF",
                "-DLIBXML2_WITH_PYTHON=OFF",
                "-DLIBXML2_WITH_TESTS=OFF",
            ),
            {"install_prefix": "/", "install_destdir": ctx.prefix},
        )
    ]
    assert pkg_config.read_text(encoding="utf-8") == f"prefix={ctx.prefix}\n"


def test_dvdcss_uses_its_current_meson_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []
    source = tmp_path / "libdvdcss"
    source.mkdir()
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)
    monkeypatch.setattr(recipes, "meson", lambda *args: calls.append(args))

    recipes.build_dvdcss(context(tmp_path, "linux-x86_64"))

    assert calls[0][1:] == (source, "-Denable_docs=false", "-Denable_examples=false")


def test_dvdread_and_dvdnav_use_their_current_meson_builds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[object, ...]] = []
    source = tmp_path / "dvd"
    source.mkdir()
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)
    monkeypatch.setattr(recipes, "meson", lambda *args: calls.append(args))
    ctx = context(tmp_path, "linux-x86_64")

    recipes.build_dvdread(ctx)
    recipes.build_dvdnav(ctx)

    assert calls[0][1:] == (source, "-Denable_docs=false", "-Dlibdvdcss=enabled")
    assert calls[1][1:] == (source, "-Denable_docs=false", "-Denable_examples=false")


def test_current_rsvg_and_gnutls_options_are_supported() -> None:
    rsvg_source = inspect.getsource(recipes.build_rsvg)
    assert "-Drsvg-convert=disabled" not in rsvg_source

    gnutls_source = inspect.getsource(recipes.build_gnutls)
    assert "--disable-guile" not in gnutls_source


def test_audited_recipe_options_are_current() -> None:
    svt_source = inspect.getsource(recipes.build_svtav1)
    assert "BUILD_DEC" not in svt_source
    assert "BUILD_ENC" not in svt_source

    vpl_source = inspect.getsource(recipes.build_vpl)
    assert "BUILD_TOOLS" not in vpl_source

    ssh_source = inspect.getsource(recipes.build_ssh)
    assert "WITH_STATIC_LIB" not in ssh_source

    sdl_source = inspect.getsource(recipes.build_sdl2)
    assert "SDL_TEST_LIBRARY" not in sdl_source
    assert "-DSDL_TESTS=OFF" in sdl_source
    assert "-DSDL2_DISABLE_SDL2MAIN=ON" in sdl_source


def test_libssh_static_pkg_config_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = context(tmp_path, "linux-x86_64")
    source = tmp_path / "libssh"
    source.mkdir()
    pkg_config = ctx.prefix / "lib" / "pkgconfig" / "libssh.pc"

    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)

    def fake_cmake(*_args: object) -> None:
        pkg_config.parent.mkdir(parents=True)
        pkg_config.write_text("Name: libssh\nLibs: -lssh\n", encoding="utf-8")

    monkeypatch.setattr(recipes, "cmake", fake_cmake)
    recipes.build_ssh(ctx)

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


def test_static_opencl_pkg_config_has_platform_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for target, expected in (
        ("linux-x86_64", {"-ldl", "-pthread"}),
        ("windows-x86_64", {"-lcfgmgr32", "-lruntimeobject"}),
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


def test_windows_openal_pkg_config_has_com_dependencies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "windows-x86_64")
    source = tmp_path / "openal"
    source.mkdir()
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
    (("linux-x86_64", "ON"), ("windows-x86_64", "ON"), ("windows-arm64", "OFF")),
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


def test_amf_installs_the_headers_archive_layout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ctx = context(tmp_path, "linux-x86_64")
    source = tmp_path / "amf"
    header = source / "AMF" / "core" / "Version.h"
    header.parent.mkdir(parents=True)
    header.write_text("#define AMF_VERSION 1\n", encoding="utf-8")
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)

    recipes.build_amf(ctx)

    installed = ctx.prefix / "include" / "AMF" / "core" / "Version.h"
    assert installed.read_text(encoding="utf-8") == "#define AMF_VERSION 1\n"


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


def test_linux_ffmpeg_links_the_math_library_for_static_dependency_probes(
    tmp_path: Path,
) -> None:
    flags = configure_flags(context(tmp_path, "linux-x86_64"), revision=1)

    assert "--extra-libs=-lm" in flags


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


def test_lamer_builds_only_the_library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = context(tmp_path, "windows-x86_64")
    source = tmp_path / "lamer"
    source.mkdir()
    commands: list[tuple[object, ...]] = []
    monkeypatch.setattr(recipes, "extract", lambda _ctx, _name: source)
    monkeypatch.setattr(recipes, "run", lambda *args, **_kwargs: commands.append(args))

    recipes.build_lamer(ctx)

    assert commands[0] == ("make", f"-j{ctx.jobs}", "lib")


def test_quirc_library_build_does_not_probe_host_sdl() -> None:
    source = inspect.getsource(recipes.build_quirc)
    assert "make" not in source
    assert "SDL" not in source
    assert 'env["AR"]' in source


def test_librist_uses_its_actual_tools_option() -> None:
    source = inspect.getsource(recipes.build_rist)
    assert "-Dbuilt_tools=false" in source
    assert '"-Dtools=false"' not in source


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

    windows_env = context(tmp_path, "windows-x86_64").env()
    assert "-fPIC" not in windows_env["CFLAGS"]
    assert "-fPIC" not in windows_env["CXXFLAGS"]


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
        "proxy_libintl",
        "shaderc",
        "shaderc_glslang",
        "shaderc_spirv_headers",
        "shaderc_spirv_tools",
    } <= sources.keys()
    assert all(source.license and source.license_files for source in sources.values())

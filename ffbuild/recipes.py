from __future__ import annotations

import shlex
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

from .helpers import autotools, cmake, extract, make, meson, run
from .model import BuildContext

Recipe = tuple[str, Callable[[BuildContext], bool], Callable[[BuildContext], None]]


def always(_: BuildContext) -> bool:
    return True


def linux(ctx: BuildContext) -> bool:
    return ctx.target.linux


def unix(ctx: BuildContext) -> bool:
    return ctx.target.linux or ctx.target.macos


def not_macos(ctx: BuildContext) -> bool:
    return not ctx.target.macos


def x86(ctx: BuildContext) -> bool:
    return ctx.target.arch == "x86_64"


def fdk(ctx: BuildContext) -> bool:
    return ctx.with_fdk_aac


def macos(ctx: BuildContext) -> bool:
    return ctx.target.macos


def windows(ctx: BuildContext) -> bool:
    return ctx.target.windows


def record_llvm_mingw(ctx: BuildContext) -> None:
    extract(ctx, "llvm_mingw")


def build_zlib(ctx: BuildContext) -> None:
    source = extract(ctx, "zlib")
    run(
        source / "configure",
        f"--prefix={ctx.prefix}",
        f"--libdir={ctx.prefix / 'lib'}",
        "--static",
        cwd=source,
        env=ctx.env(),
    )
    make(ctx, source)


def build_xz(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "xz"),
        "-DXZ_NLS=OFF",
        "-DXZ_TOOL_XZ=OFF",
        "-DXZ_TOOL_XZDEC=OFF",
        "-DXZ_TOOL_LZMADEC=OFF",
        "-DXZ_TOOL_LZMAINFO=OFF",
    )


def build_openssl(ctx: BuildContext) -> None:
    source = extract(ctx, "openssl")
    env = ctx.env()
    for name in ("CPPFLAGS", "CFLAGS", "CXXFLAGS", "LDFLAGS"):
        env[name] = " ".join(
            token for token in shlex.split(env[name]) if str(ctx.root) not in token
        )
    platform = {
        "linux-x86_64": "linux-x86_64",
        "linux-arm64": "linux-aarch64",
        "windows-x86_64": "mingw64",
        "windows-arm64": "mingwarm64",
        "macos-arm64": "darwin64-arm64-cc",
    }[ctx.target.name]
    run(
        source / "Configure",
        platform,
        "--prefix=/",
        "--openssldir=/ssl",
        "--libdir=lib",
        "no-shared",
        "no-module",
        "no-tests",
        "no-apps",
        "no-docs",
        cwd=source,
        env=env,
    )
    run("make", f"-j{ctx.jobs}", cwd=source, env=env)
    run("make", f"DESTDIR={ctx.prefix}", "install_sw", cwd=source, env=env)
    for name in ("libcrypto.pc", "libssl.pc", "openssl.pc"):
        _set_pkg_config_variables(
            ctx.prefix / "lib" / "pkgconfig" / name, {"prefix": str(ctx.prefix)}
        )


def build_expat(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "expat"),
        "-DEXPAT_BUILD_DOCS=OFF",
        "-DEXPAT_BUILD_EXAMPLES=OFF",
        "-DEXPAT_BUILD_TESTS=OFF",
        "-DEXPAT_BUILD_TOOLS=OFF",
        "-DEXPAT_SHARED_LIBS=OFF",
    )


def build_iconv(ctx: BuildContext) -> None:
    autotools(ctx, extract(ctx, "iconv"), "--disable-nls")


def build_xml2(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "xml2"),
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
        install_prefix="/",
        install_destdir=ctx.prefix,
    )
    _set_pkg_config_variables(
        ctx.prefix / "lib" / "pkgconfig" / "libxml-2.0.pc", {"prefix": str(ctx.prefix)}
    )


def build_udfread(ctx: BuildContext) -> None:
    meson(ctx, extract(ctx, "udfread"), "-Denable_examples=false")


def build_bluray(ctx: BuildContext) -> None:
    meson(
        ctx,
        extract(ctx, "bluray"),
        "-Denable_docs=false",
        "-Denable_tools=false",
        "-Denable_devtools=false",
        "-Denable_examples=false",
        "-Dbdj_jar=disabled",
        "-Dembed_udfread=false",
        "-Dfontconfig=enabled",
        "-Dfreetype=enabled",
        "-Dlibxml2=enabled",
    )


def build_dvdcss(ctx: BuildContext) -> None:
    meson(
        ctx,
        extract(ctx, "dvdcss"),
        "-Denable_docs=false",
        "-Denable_examples=false",
    )


def build_dvdread(ctx: BuildContext) -> None:
    meson(
        ctx,
        extract(ctx, "dvdread"),
        "-Denable_docs=false",
        "-Dlibdvdcss=enabled",
    )


def build_dvdnav(ctx: BuildContext) -> None:
    meson(
        ctx,
        extract(ctx, "dvdnav"),
        "-Denable_docs=false",
        "-Denable_examples=false",
    )


def build_aribcaption(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "aribcaption"),
        "-DARIBCC_SHARED_LIBRARY=OFF",
        "-DARIBCC_BUILD_TESTS=OFF",
        "-DARIBCC_USE_FREETYPE=ON",
        "-DARIBCC_USE_EMBEDDED_FREETYPE=OFF",
    )
    _ensure_static_cpp_runtime(ctx, ctx.prefix / "lib" / "pkgconfig" / "libaribcaption.pc")


def build_openal(ctx: BuildContext) -> None:
    source = extract(ctx, "openal")
    if ctx.target.windows:
        fmt_header = source / "fmt-11.1.1" / "include" / "fmt" / "format.h"
        fmt_text = fmt_header.read_text(encoding="utf-8")
        include_marker = "#  include <cstdint>  // uint32_t\n"
        if fmt_text.count(include_marker) != 1:
            raise RuntimeError("OpenAL Soft's bundled fmt has an unexpected include layout")
        fmt_header.write_text(
            fmt_text.replace(include_marker, include_marker + "#  include <stdlib.h>  // free\n"),
            encoding="utf-8",
        )
    args = [
        "-DLIBTYPE=STATIC",
        "-DALSOFT_UTILS=OFF",
        "-DALSOFT_EXAMPLES=OFF",
        "-DALSOFT_TESTS=OFF",
        "-DALSOFT_BACKEND_JACK=OFF",
        "-DALSOFT_BACKEND_PORTAUDIO=OFF",
        "-DALSOFT_BACKEND_PIPEWIRE=OFF",
        "-DALSOFT_BACKEND_PULSEAUDIO=OFF",
    ]
    if ctx.target.linux:
        args += ["-DALSOFT_BACKEND_ALSA=ON", "-DALSOFT_BACKEND_OSS=OFF"]
    cmake(ctx, source, *args)
    pkg_config = ctx.prefix / "lib" / "pkgconfig" / "openal.pc"
    _ensure_static_cpp_runtime(ctx, pkg_config)
    if ctx.target.windows:
        _append_pkg_config_tokens(pkg_config, "Libs.private", ["-lole32", "-luuid"])


def build_rubberband(ctx: BuildContext) -> None:
    source = extract(ctx, "rubberband")
    if ctx.target.macos:
        mathmisc_header = source / "src" / "common" / "mathmisc.h"
        header_text = mathmisc_header.read_text(encoding="utf-8")
        sysutils_include = '#include "sysutils.h"\n'
        if header_text.count(sysutils_include) != 1:
            raise RuntimeError("Rubber Band mathmisc.h has an unexpected include layout")
        mathmisc_header.write_text(
            header_text.replace(sysutils_include, sysutils_include + "#include <cstddef>\n", 1),
            encoding="utf-8",
        )
    meson(
        ctx,
        source,
        "-Dfft=builtin",
        "-Dresampler=builtin",
        "-Djni=disabled",
        "-Dladspa=disabled",
        "-Dlv2=disabled",
        "-Dvamp=disabled",
        "-Dcmdline=disabled",
        "-Dtests=disabled",
    )
    _append_pkg_config_tokens(
        ctx.prefix / "lib" / "pkgconfig" / "rubberband.pc", "Libs.private", ["-lm"]
    )
    _ensure_static_cpp_runtime(ctx, ctx.prefix / "lib" / "pkgconfig" / "rubberband.pc")


def build_soxr(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "soxr"),
        "-DWITH_OPENMP=OFF",
        "-DBUILD_TESTS=OFF",
        "-DBUILD_EXAMPLES=OFF",
    )
    pc = ctx.prefix / "lib" / "pkgconfig"
    pc.mkdir(exist_ok=True)
    if not (pc / "soxr.pc").exists():
        (pc / "soxr.pc").write_text(
            f"prefix={ctx.prefix}\nlibdir=${{prefix}}/lib\nincludedir=${{prefix}}/include\n\n"
            "Name: soxr\nDescription: High quality resampling library\nVersion: 0.1.3\n"
            "Libs: -L${libdir} -lsoxr\nLibs.private: -lm\nCflags: -I${includedir}\n",
            encoding="utf-8",
        )


def build_lv2(ctx: BuildContext) -> None:
    meson(ctx, extract(ctx, "lv2"), "-Ddocs=disabled", "-Dtests=disabled", "-Donline_docs=false")


def build_zix(ctx: BuildContext) -> None:
    meson(
        ctx,
        extract(ctx, "zix"),
        "-Ddocs=disabled",
        "-Dbenchmarks=disabled",
        "-Dtests=disabled",
        "-Dtests_cpp=disabled",
    )


def build_serd(ctx: BuildContext) -> None:
    meson(ctx, extract(ctx, "serd"), "-Ddocs=disabled", "-Dtools=disabled", "-Dtests=disabled")


def build_sord(ctx: BuildContext) -> None:
    meson(ctx, extract(ctx, "sord"), "-Ddocs=disabled", "-Dtools=disabled", "-Dtests=disabled")


def build_sratom(ctx: BuildContext) -> None:
    meson(ctx, extract(ctx, "sratom"), "-Ddocs=disabled", "-Dtests=disabled")


def build_lilv(ctx: BuildContext) -> None:
    meson(
        ctx,
        extract(ctx, "lilv"),
        "-Ddocs=disabled",
        "-Dtools=disabled",
        "-Dtests=disabled",
        "-Dbindings_py=disabled",
    )


def build_ffi(ctx: BuildContext) -> None:
    autotools(ctx, extract(ctx, "ffi"), "--disable-docs", "--disable-multi-os-directory")


def build_pcre2(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "pcre2"),
        "-DPCRE2_BUILD_PCRE2_8=ON",
        "-DPCRE2_BUILD_PCRE2_16=OFF",
        "-DPCRE2_BUILD_PCRE2_32=OFF",
        "-DPCRE2_BUILD_TESTS=OFF",
        "-DPCRE2_BUILD_PCRE2GREP=OFF",
    )


def build_glib(ctx: BuildContext) -> None:
    source = extract(ctx, "glib")
    if ctx.target.macos:
        # Darwin has no libc-provided gettext API. This is the exact fallback
        # commit referenced by GLib's proxy-libintl.wrap, staged explicitly so
        # Meson's nodownload mode remains hermetic.
        proxy_libintl = extract(ctx, "proxy_libintl")
        shutil.copytree(
            proxy_libintl,
            source / "subprojects" / "proxy-libintl",
            dirs_exist_ok=True,
        )
    meson(
        ctx,
        source,
        "-Dtests=false",
        "-Dinstalled_tests=false",
        "-Dglib_debug=disabled",
        "-Dnls=disabled",
        "-Dintrospection=disabled",
        "-Ddocumentation=false",
        "-Dman-pages=disabled",
        "-Dsysprof=disabled",
        "-Dlibelf=disabled",
        "-Dbsymbolic_functions=false",
        "-Dselinux=disabled",
        "-Dlibmount=disabled",
        "-Ddtrace=disabled",
        "-Dsystemtap=disabled",
        "-Dpkgconfig.relocatable=true",
        install_prefix="/",
        install_destdir=ctx.prefix,
    )


def build_pixman(ctx: BuildContext) -> None:
    meson(ctx, extract(ctx, "pixman"), "-Dtests=disabled", "-Ddemos=disabled")


def build_cairo(ctx: BuildContext) -> None:
    meson(
        ctx,
        extract(ctx, "cairo"),
        "-Dglib=enabled",
        "-Dpng=enabled",
        "-Dzlib=enabled",
        "-Dfreetype=enabled",
        "-Dfontconfig=enabled",
        "-Dxlib=disabled",
        "-Dxcb=disabled",
        "-Dxlib-xcb=disabled",
        "-Dtee=disabled",
        "-Dlzo=disabled",
        "-Dspectre=disabled",
        "-Dsymbol-lookup=disabled",
        "-Dgtk2-utils=disabled",
        "-Dtests=disabled",
    )


def build_pango(ctx: BuildContext) -> None:
    meson(
        ctx,
        extract(ctx, "pango"),
        "-Dcairo=enabled",
        "-Dfreetype=enabled",
        "-Dfontconfig=enabled",
        "-Dlibthai=disabled",
        "-Dxft=disabled",
        "-Dsysprof=disabled",
        "-Dintrospection=disabled",
        "-Ddocumentation=false",
        "-Dman-pages=false",
        "-Dbuild-testsuite=false",
        "-Dbuild-examples=false",
        "-Dpkgconfig.relocatable=true",
        install_prefix="/",
        install_destdir=ctx.prefix,
    )


def build_rsvg(ctx: BuildContext) -> None:
    source = extract(ctx, "rsvg")
    meson_build = source / "meson.build"
    meson_text = meson_build.read_text(encoding="utf-8")
    rsvg_convert_subdir = "subdir('rsvg_convert')\n"
    if meson_text.count(rsvg_convert_subdir) != 1:
        raise RuntimeError("librsvg's rsvg-convert Meson subdirectory was not found exactly once")
    meson_build.write_text(meson_text.replace(rsvg_convert_subdir, ""), encoding="utf-8")
    vendor = source / "vendor"
    config = run(
        "cargo",
        "vendor",
        "--locked",
        "--versioned-dirs",
        vendor,
        cwd=source,
        env=ctx.env(),
        capture=True,
    )
    cargo_home = Path(ctx.env()["CARGO_HOME"])
    cargo_home.mkdir(parents=True, exist_ok=True)
    config = config.replace('directory = "vendor"', f'directory = "{vendor}"')
    (cargo_home / "config.toml").write_text(config, encoding="utf-8")
    meson(
        ctx,
        source,
        "-Davif=disabled",
        "-Dpixbuf=disabled",
        "-Dpixbuf-loader=disabled",
        "-Dintrospection=disabled",
        "-Dvala=disabled",
        "-Ddocs=disabled",
        "-Dtests=false",
    )


def build_vulkan_headers(ctx: BuildContext) -> None:
    cmake(ctx, extract(ctx, "vulkan_headers"), "-DVULKAN_HEADERS_ENABLE_TESTS=OFF")


def build_vulkan_loader(ctx: BuildContext) -> None:
    source = extract(ctx, "vulkan_loader")
    shutil.copytree(
        ctx.source_dirs["vulkan_headers"], source / "Vulkan-Headers", dirs_exist_ok=True
    )
    cmake(
        ctx,
        source,
        "-DVULKAN_SHIM_IMPERSONATE=ON",
    )


def build_opencl_headers(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "opencl_headers"),
        "-DBUILD_TESTING=OFF",
        "-DOPENCL_HEADERS_BUILD_TESTING=OFF",
    )


def build_opencl_loader(ctx: BuildContext) -> None:
    source = extract(ctx, "opencl_loader")
    cmake(
        ctx,
        source,
        "-DBUILD_TESTING=OFF",
        "-DENABLE_OPENCL_LAYERS=OFF",
        "-DOPENCL_ICD_LOADER_DISABLE_OPENCLON12=ON",
        "-DOPENCL_ICD_LOADER_PIC=ON",
        "-DOPENCL_ICD_LOADER_BUILD_TESTING=OFF",
        "-DOPENCL_ICD_LOADER_BUILD_SHARED_LIBS=OFF",
        f"-DOPENCL_ICD_LOADER_HEADERS_DIR={ctx.prefix / 'include'}",
    )
    pkg_config = ctx.prefix / "lib" / "pkgconfig" / "OpenCL.pc"
    if ctx.target.windows:
        _replace_pkg_config_token_suffix(pkg_config, "-lOpenCL", "-l:OpenCL.a")
    private = ["-lole32", "-lshlwapi", "-lcfgmgr32"] if ctx.target.windows else ["-ldl", "-pthread"]
    _append_pkg_config_tokens(pkg_config, "Libs.private", private)


def build_libdrm(ctx: BuildContext) -> None:
    source = extract(ctx, "libdrm")
    build_dir = ctx.work_root / "libdrm"
    run(
        "meson",
        "setup",
        build_dir,
        source,
        f"--prefix={ctx.prefix}",
        "--libdir=lib",
        "--default-library=shared",
        "--buildtype=release",
        "--wrap-mode=nodownload",
        "-Dintel=disabled",
        "-Dradeon=disabled",
        "-Damdgpu=disabled",
        "-Dnouveau=disabled",
        "-Dvmwgfx=disabled",
        "-Dfreedreno=disabled",
        "-Dvc4=disabled",
        "-Detnaviv=disabled",
        "-Dtests=false",
        "-Dudev=false",
        env=ctx.env(),
    )
    run("meson", "compile", "-C", build_dir, "-j", str(ctx.jobs), env=ctx.env())
    run("meson", "install", "-C", build_dir, env=ctx.env())

    library_dir = ctx.prefix / "lib"
    _generate_import_library(
        ctx,
        ctx.source_dirs["implib"],
        library_dir / "libdrm.so.2",
        library_dir / "libdrm.a",
    )
    _add_implib_link_flags(library_dir / "pkgconfig" / "libdrm.pc")


def prepare_implib(ctx: BuildContext) -> None:
    extract(ctx, "implib")


def _generate_import_library(
    ctx: BuildContext, implib: Path, shared_library: Path, output: Path
) -> None:
    if not shared_library.is_file():
        raise FileNotFoundError(f"shared library for import shim is missing: {shared_library}")
    work = ctx.work_root / f"implib-{output.stem}"
    work.mkdir(parents=True, exist_ok=True)
    target = "x86_64-linux-gnu" if ctx.target.arch == "x86_64" else "aarch64-linux-gnu"
    run(
        sys.executable,
        implib / "implib-gen.py",
        "--target",
        target,
        "--dlopen",
        "--lazy-load",
        shared_library,
        cwd=work,
        env=ctx.env(),
    )
    generated = sorted((*work.glob("*.tramp.S"), *work.glob("*.init.c")))
    if not generated:
        raise RuntimeError(f"Implib.so generated no sources for {shared_library.name}")
    objects: list[Path] = []
    env = ctx.env()
    for source in generated:
        object_file = work / f"{source.name}.o"
        run(
            *shlex.split(env["CC"]),
            *shlex.split(env["CFLAGS"]),
            "-Wa,--noexecstack",
            "-DIMPLIB_HIDDEN_SHIMS",
            "-c",
            source,
            "-o",
            object_file,
            cwd=work,
            env=env,
        )
        objects.append(object_file)
    run(env["AR"], "rcs", output, *objects, cwd=work, env=env)


def _add_implib_link_flags(pkg_config: Path) -> None:
    lines = pkg_config.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("Libs.private:"):
            lines[index] = f"{line} -ldl -pthread"
            break
    else:
        lines.append("Libs.private: -ldl -pthread")
    pkg_config.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _set_pkg_config_fields(pkg_config: Path, fields: dict[str, str]) -> None:
    lines = pkg_config.read_text(encoding="utf-8").splitlines()
    remaining = dict(fields)
    for index, line in enumerate(lines):
        key = line.partition(":")[0]
        if key in remaining:
            lines[index] = f"{key}: {remaining.pop(key)}"
    lines.extend(f"{key}: {value}" for key, value in remaining.items())
    pkg_config.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _set_pkg_config_variables(pkg_config: Path, variables: dict[str, str]) -> None:
    lines = pkg_config.read_text(encoding="utf-8").splitlines()
    remaining = dict(variables)
    for index, line in enumerate(lines):
        key, separator, _ = line.partition("=")
        if separator and key in remaining:
            lines[index] = f"{key}={remaining.pop(key)}"
    lines.extend(f"{key}={value}" for key, value in remaining.items())
    pkg_config.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _remove_pkg_config_tokens(pkg_config: Path, unwanted: set[str]) -> None:
    lines = pkg_config.read_text(encoding="utf-8").splitlines()
    cleaned = [" ".join(token for token in line.split() if token not in unwanted) for line in lines]
    pkg_config.write_text("\n".join(cleaned) + "\n", encoding="utf-8")


def _replace_pkg_config_token_suffix(pkg_config: Path, suffix: str, replacement: str) -> None:
    lines = pkg_config.read_text(encoding="utf-8").splitlines()
    replaced = [
        " ".join(replacement if token.endswith(suffix) else token for token in line.split())
        for line in lines
    ]
    pkg_config.write_text("\n".join(replaced) + "\n", encoding="utf-8")


def _append_pkg_config_tokens(pkg_config: Path, field: str, tokens: list[str]) -> None:
    lines = pkg_config.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        key, separator, value = line.partition(":")
        if separator and key == field:
            current = value.split()
            current.extend(token for token in tokens if token not in current)
            lines[index] = f"{field}: {' '.join(current)}"
            break
    else:
        lines.append(f"{field}: {' '.join(tokens)}")
    pkg_config.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ensure_static_cpp_runtime(ctx: BuildContext, pkg_config: Path) -> None:
    runtime = "-lstdc++" if ctx.target.linux else "-lc++"
    _replace_pkg_config_token_suffix(pkg_config, "libstdc++.a", runtime)
    _replace_pkg_config_token_suffix(pkg_config, "libc++.a", runtime)
    if runtime not in pkg_config.read_text(encoding="utf-8").split():
        _append_pkg_config_tokens(pkg_config, "Libs.private", [runtime])


def build_libva(ctx: BuildContext) -> None:
    source = extract(ctx, "libva")
    implib = ctx.source_dirs["implib"]
    build_dir = ctx.work_root / "libva"
    run(
        "meson",
        "setup",
        build_dir,
        source,
        f"--prefix={ctx.prefix}",
        "--libdir=lib",
        "--default-library=shared",
        "--buildtype=release",
        "--wrap-mode=nodownload",
        "-Denable_docs=false",
        "-Ddisable_drm=false",
        "-Ddriverdir=/usr/lib64/dri",
        "-Dwith_glx=no",
        "-Dwith_wayland=no",
        "-Dwith_x11=no",
        env=ctx.env(),
    )
    run("meson", "compile", "-C", build_dir, "-j", str(ctx.jobs), env=ctx.env())
    run("meson", "install", "-C", build_dir, env=ctx.env())

    library_dir = ctx.prefix / "lib"
    _generate_import_library(ctx, implib, library_dir / "libva.so.2", library_dir / "libva.a")
    _generate_import_library(
        ctx, implib, library_dir / "libva-drm.so.2", library_dir / "libva-drm.a"
    )
    for shared_library in library_dir.glob("libva*.so*"):
        shared_library.unlink()
    for shared_library in library_dir.glob("libdrm*.so*"):
        shared_library.unlink()
    _add_implib_link_flags(library_dir / "pkgconfig" / "libva.pc")
    _add_implib_link_flags(library_dir / "pkgconfig" / "libva-drm.pc")


def build_freetype(ctx: BuildContext) -> None:
    meson(
        ctx,
        extract(ctx, "freetype"),
        "-Dbrotli=disabled",
        "-Dbzip2=disabled",
        "-Dharfbuzz=disabled",
    )


def build_fontconfig(ctx: BuildContext) -> None:
    meson(
        ctx,
        extract(ctx, "fontconfig"),
        "-Ddoc=disabled",
        "-Dtests=disabled",
        "-Dtools=disabled",
        "-Dpkgconfig.relocatable=true",
        install_prefix="/",
        install_destdir=ctx.prefix,
    )


def build_harfbuzz(ctx: BuildContext) -> None:
    args = [
        "-Dtests=disabled",
        "-Ddocs=disabled",
        "-Dutilities=disabled",
        "-Dglib=disabled",
        "-Dgobject=disabled",
        "-Dcairo=disabled",
    ]
    if ctx.target.macos:
        args.append("-Dcoretext=enabled")
    meson(
        ctx,
        extract(ctx, "harfbuzz"),
        *args,
    )


def build_fribidi(ctx: BuildContext) -> None:
    meson(ctx, extract(ctx, "fribidi"), "-Ddocs=false", "-Dbin=false", "-Dtests=false")


def build_libass(ctx: BuildContext) -> None:
    args = ["-Dtest=disabled", "-Dcompare=disabled", "-Dprofile=disabled", "-Dfontconfig=enabled"]
    if ctx.target.windows:
        args.append("-Ddirectwrite=enabled")
    meson(ctx, extract(ctx, "ass"), *args)


def build_aom(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "aom"),
        "-DENABLE_DOCS=OFF",
        "-DENABLE_EXAMPLES=OFF",
        "-DENABLE_TESTS=OFF",
        "-DENABLE_TOOLS=OFF",
    )


def build_opus(ctx: BuildContext) -> None:
    args = ["--disable-doc", "--disable-extra-programs"]
    if ctx.target.name == "windows-arm64":
        args.append("--disable-rtcd")
    autotools(ctx, extract(ctx, "opus"), *args)


def build_dav1d(ctx: BuildContext) -> None:
    meson(ctx, extract(ctx, "dav1d"), "-Denable_tests=false", "-Denable_tools=false")


def build_svtav1(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "svtav1"),
        "-DBUILD_APPS=OFF",
        "-DBUILD_TESTING=OFF",
    )


def build_vpx(ctx: BuildContext) -> None:
    source = extract(ctx, "vpx")
    targets = {
        "linux-x86_64": "x86_64-linux-gcc",
        "linux-arm64": "arm64-linux-gcc",
        "windows-x86_64": "x86_64-win64-gcc",
        "windows-arm64": "arm64-win64-gcc",
        "macos-arm64": "arm64-darwin20-gcc",
    }
    build_dir = ctx.work_root / "vpx"
    build_dir.mkdir(parents=True, exist_ok=True)
    install_root = ctx.work_root / "vpx-install"
    if install_root.exists():
        shutil.rmtree(install_root)
    run(
        source / "configure",
        "--prefix=/ffmpeg",
        f"--target={targets[ctx.target.name]}",
        "--disable-shared",
        "--enable-static",
        "--disable-examples",
        "--disable-tools",
        "--disable-unit-tests",
        "--enable-vp9-highbitdepth",
        cwd=build_dir,
        env=ctx.env(),
    )
    make(ctx, build_dir, variables=(f"DESTDIR={install_root}",))
    shutil.copytree(install_root / "ffmpeg", ctx.prefix, dirs_exist_ok=True)
    _set_pkg_config_variables(
        ctx.prefix / "lib" / "pkgconfig" / "vpx.pc", {"prefix": str(ctx.prefix)}
    )


def build_lamer(ctx: BuildContext) -> None:
    source = extract(ctx, "lamer")
    run("make", f"-j{ctx.jobs}", "lib", cwd=source, env=ctx.env())
    run("make", f"PREFIX={ctx.prefix}", "install", cwd=source, env=ctx.env())


def build_png(ctx: BuildContext) -> None:
    cmake(ctx, extract(ctx, "png"), "-DPNG_SHARED=OFF", "-DPNG_TESTS=OFF", "-DPNG_TOOLS=OFF")


def build_webp(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "webp"),
        "-DWEBP_BUILD_EXTRAS=OFF",
        "-DWEBP_BUILD_ANIM_UTILS=OFF",
        "-DWEBP_BUILD_CWEBP=OFF",
        "-DWEBP_BUILD_DWEBP=OFF",
        "-DWEBP_BUILD_GIF2WEBP=OFF",
        "-DWEBP_BUILD_IMG2WEBP=OFF",
        "-DWEBP_BUILD_VWEBP=OFF",
        "-DWEBP_BUILD_WEBPINFO=OFF",
        "-DWEBP_BUILD_WEBPMUX=OFF",
    )


def build_vmaf(ctx: BuildContext) -> None:
    meson(
        ctx,
        extract(ctx, "vmaf") / "libvmaf",
        "-Denable_tests=false",
        "-Denable_docs=false",
        "-Denable_tools=false",
    )
    _ensure_static_cpp_runtime(ctx, ctx.prefix / "lib" / "pkgconfig" / "libvmaf.pc")


def build_x264(ctx: BuildContext) -> None:
    source = extract(ctx, "x264")
    args = [
        f"--prefix={ctx.prefix}",
        "--enable-static",
        "--disable-cli",
        "--enable-pic",
    ]
    if ctx.target.host:
        args += [f"--host={ctx.target.host}", "--cross-prefix=" + ctx.target.host + "-"]
    run(source / "configure", *args, cwd=source, env=ctx.env())
    make(ctx, source)


def build_x265(ctx: BuildContext) -> None:
    cmake(ctx, extract(ctx, "x265") / "source", "-DENABLE_CLI=OFF", "-DENABLE_SHARED=OFF")
    _ensure_static_cpp_runtime(ctx, ctx.prefix / "lib" / "pkgconfig" / "x265.pc")


def build_openjpeg(ctx: BuildContext) -> None:
    cmake(ctx, extract(ctx, "openjpeg"), "-DBUILD_CODEC=OFF", "-DBUILD_TESTING=OFF")


def build_zimg(ctx: BuildContext) -> None:
    autotools(ctx, extract(ctx, "zimg"), autoreconf=True)


def build_lc3(ctx: BuildContext) -> None:
    meson(ctx, extract(ctx, "lc3"), "-Dtools=false", "-Dpython=false")


def build_mysofa(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "mysofa"),
        "-DBUILD_TESTS=OFF",
        "-DBUILD_SHARED_LIBS=OFF",
        "-DCMAKE_INSTALL_LIBDIR=/lib",
        "-DCMAKE_INSTALL_INCLUDEDIR=/include",
        "-DCMAKE_INSTALL_DATADIR=/share",
        install_prefix="/",
        install_destdir=ctx.prefix,
    )
    _set_pkg_config_variables(
        ctx.prefix / "lib" / "pkgconfig" / "libmysofa.pc", {"prefix": str(ctx.prefix)}
    )


def build_qrencode(ctx: BuildContext) -> None:
    cmake(ctx, extract(ctx, "qrencode"), "-DWITH_TOOLS=NO", "-DWITH_TESTS=NO")


def build_quirc(ctx: BuildContext) -> None:
    source = extract(ctx, "quirc")
    env = ctx.env()
    work = ctx.work_root / "quirc"
    work.mkdir(parents=True, exist_ok=True)
    objects: list[Path] = []
    for filename in ("decode.c", "identify.c", "quirc.c", "version_db.c"):
        object_file = work / f"{Path(filename).stem}.o"
        run(
            *shlex.split(env["CC"]),
            *shlex.split(env["CFLAGS"]),
            "-I",
            source / "lib",
            "-c",
            source / "lib" / filename,
            "-o",
            object_file,
            env=env,
        )
        objects.append(object_file)
    archive = work / "libquirc.a"
    run(env["AR"], "rcs", archive, *objects, env=env)
    run(env["RANLIB"], archive, env=env)
    (ctx.prefix / "include").mkdir(exist_ok=True)
    (ctx.prefix / "lib").mkdir(exist_ok=True)
    shutil.copy2(source / "lib" / "quirc.h", ctx.prefix / "include")
    shutil.copy2(archive, ctx.prefix / "lib")
    pc = ctx.prefix / "lib" / "pkgconfig"
    pc.mkdir(exist_ok=True)
    (pc / "quirc.pc").write_text(
        f"prefix={ctx.prefix}\nlibdir=${{prefix}}/lib\nincludedir=${{prefix}}/include\n\n"
        "Name: quirc\nDescription: QR decoder\nVersion: 1.2\nLibs: -L${libdir} -lquirc\n"
        "Cflags: -I${includedir}\n",
        encoding="utf-8",
    )


def build_sdl2(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "sdl2"),
        "-DSDL_SHARED=OFF",
        "-DSDL_STATIC=ON",
        "-DSDL_TEST=OFF",
        "-DSDL_TESTS=OFF",
        "-DSDL2_DISABLE_SDL2MAIN=ON",
    )
    if ctx.target.windows:
        _remove_pkg_config_tokens(
            ctx.prefix / "lib" / "pkgconfig" / "sdl2.pc",
            {"-mwindows", "-lmingw32", "-lSDL2main", "-Dmain=SDL_main"},
        )


def build_srt(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "srt"),
        "-DENABLE_SHARED=OFF",
        "-DENABLE_STATIC=ON",
        "-DENABLE_APPS=OFF",
        "-DENABLE_ENCRYPTION=ON",
    )
    _ensure_static_cpp_runtime(ctx, ctx.prefix / "lib" / "pkgconfig" / "srt.pc")


def build_rist(ctx: BuildContext) -> None:
    source = extract(ctx, "rist")
    if ctx.target.name == "windows-arm64":
        timing_source = source / "contrib" / "mbedtls" / "library" / "timing.c"
        timing_text = timing_source.read_text(encoding="utf-8")
        process_include = "#include <process.h>\n"
        if timing_text.count(process_include) != 1:
            raise RuntimeError("librist's bundled mbedTLS has an unexpected include layout")
        timing_source.write_text(
            timing_text.replace(process_include, process_include + "#include <sys/time.h>\n", 1),
            encoding="utf-8",
        )
    args = ["-Dtest=false", "-Dbuilt_tools=false", "-Dbuiltin_cjson=true"]
    if ctx.target.windows:
        args.append("-Dhave_mingw_pthreads=true")
    meson(
        ctx,
        source,
        *args,
    )


def build_ssh(ctx: BuildContext) -> None:
    def declare_windows_strndup(build_dir: Path) -> None:
        if not ctx.target.windows:
            return
        with (build_dir / "config.h").open("a", encoding="utf-8") as config:
            config.write("\n#include <stddef.h>\nchar *strndup(const char *, size_t);\n")

    source = extract(ctx, "ssh")
    args = [
        "-DWITH_EXAMPLES=OFF",
        "-DWITH_SERVER=OFF",
        "-DWITH_GCRYPT=OFF",
        "-DWITH_GSSAPI=OFF",
    ]
    if ctx.target.windows:
        args.append("-DHAVE_STRNDUP=YES")
        cmake(ctx, source, *args, after_configure=declare_windows_strndup)
    else:
        cmake(ctx, source, *args)
    private_libs = "-lpthread"
    if ctx.target.windows:
        private_libs += " -liphlpapi -lws2_32"
    _set_pkg_config_fields(
        ctx.prefix / "lib" / "pkgconfig" / "libssh.pc",
        {
            "Requires.private": "libssl libcrypto zlib",
            "Cflags.private": "-DLIBSSH_STATIC",
            "Libs.private": private_libs,
        },
    )


def build_nvcodec(ctx: BuildContext) -> None:
    source = extract(ctx, "nvcodec")
    run("make", f"PREFIX={ctx.prefix}", "install", cwd=source, env=ctx.env())


def build_amf(ctx: BuildContext) -> None:
    source = extract(ctx, "amf")
    include = ctx.prefix / "include" / "AMF"
    include.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source / "AMF", include, dirs_exist_ok=True)


def build_vpl(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "vpl"),
        "-DCMAKE_INSTALL_BINDIR=/bin",
        "-DCMAKE_INSTALL_LIBDIR=/lib",
        "-DCMAKE_INSTALL_INCLUDEDIR=/include",
        "-DCMAKE_INSTALL_DATAROOTDIR=/share",
        "-DCMAKE_INSTALL_DATADIR=/share",
        "-DCMAKE_INSTALL_SYSCONFDIR=/etc",
        "-DINSTALL_EXAMPLES=OFF",
        "-DBUILD_TESTS=OFF",
        "-DBUILD_EXAMPLES=OFF",
        install_prefix="/",
        install_destdir=ctx.prefix,
    )
    _ensure_static_cpp_runtime(ctx, ctx.prefix / "lib" / "pkgconfig" / "vpl.pc")


def build_placebo(ctx: BuildContext) -> None:
    source = extract(ctx, "placebo")
    # Meson must run on Python 3.12; 3.14 rejects libplacebo's VkXML(ET.parse(...)).
    submodules = {
        "placebo_vulkan": source / "3rdparty" / "Vulkan-Headers",
        "placebo_fast_float": source / "3rdparty" / "fast_float",
        "placebo_glad": source / "3rdparty" / "glad",
        "placebo_jinja": source / "3rdparty" / "jinja",
        "placebo_markupsafe": source / "3rdparty" / "markupsafe",
    }
    for name, destination in submodules.items():
        shutil.copytree(extract(ctx, name), destination, dirs_exist_ok=True)
    platform = ["-Dd3d11=enabled"] if ctx.target.windows else ["-Dd3d11=disabled"]
    meson(
        ctx,
        source,
        "-Dvulkan=enabled",
        "-Dvk-proc-addr=enabled",
        f"-Dvulkan-registry={ctx.prefix / 'share' / 'vulkan' / 'registry' / 'vk.xml'}",
        "-Dopengl=disabled",
        "-Dglslang=disabled",
        "-Dshaderc=enabled",
        "-Ddemos=false",
        "-Dtests=false",
        "-Dbench=false",
        *platform,
    )
    _ensure_static_cpp_runtime(ctx, ctx.prefix / "lib" / "pkgconfig" / "libplacebo.pc")


def build_spirv_cross(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "spirv_cross"),
        "-DSPIRV_CROSS_SHARED=OFF",
        "-DSPIRV_CROSS_STATIC=ON",
        "-DSPIRV_CROSS_CLI=OFF",
        "-DSPIRV_CROSS_ENABLE_TESTS=OFF",
        "-DSPIRV_CROSS_FORCE_PIC=ON",
        "-DSPIRV_CROSS_ENABLE_CPP=OFF",
    )
    pkg_config = ctx.prefix / "lib" / "pkgconfig" / "spirv-cross-c-shared.pc"
    pkg_config.write_text(
        "prefix=${pcfiledir}/../..\n"
        "exec_prefix=${prefix}\n"
        "libdir=${prefix}/lib\n"
        "includedir=${prefix}/include/spirv_cross\n\n"
        "Name: spirv-cross-c-shared\n"
        "Description: C API for SPIRV-Cross\n"
        "Version: 0.68.0\n"
        "Libs: -L${libdir} -lspirv-cross-c -lspirv-cross-glsl -lspirv-cross-hlsl "
        "-lspirv-cross-reflect -lspirv-cross-msl -lspirv-cross-util -lspirv-cross-core\n"
        "Cflags: -I${includedir}\n",
        encoding="utf-8",
    )
    _ensure_static_cpp_runtime(ctx, pkg_config)


def build_shaderc(ctx: BuildContext) -> None:
    source = extract(ctx, "shaderc")
    dependencies = {
        "shaderc_abseil": source / "third_party" / "abseil_cpp",
        "shaderc_effcee": source / "third_party" / "effcee",
        "shaderc_glslang": source / "third_party" / "glslang",
        "shaderc_googletest": source / "third_party" / "googletest",
        "shaderc_re2": source / "third_party" / "re2",
        "shaderc_spirv_headers": source / "third_party" / "spirv-headers",
        "shaderc_spirv_tools": source / "third_party" / "spirv-tools",
    }
    extracted = {name: extract(ctx, name) for name in dependencies}
    for name, destination in dependencies.items():
        shutil.copytree(extracted[name], destination, dirs_exist_ok=True)
    shutil.copytree(
        extracted["shaderc_spirv_headers"] / "include" / "spirv" / "unified1",
        ctx.prefix / "include" / "spirv-headers",
        dirs_exist_ok=True,
    )
    cmake(
        ctx,
        source,
        "-DSHADERC_SKIP_TESTS=ON",
        "-DSHADERC_SKIP_EXAMPLES=ON",
        "-DSHADERC_SKIP_COPYRIGHT_CHECK=ON",
        "-DENABLE_EXCEPTIONS=ON",
        "-DENABLE_GLSLANG_BINARIES=OFF",
        "-DSPIRV_SKIP_EXECUTABLES=ON",
        "-DSPIRV_TOOLS_BUILD_STATIC=ON",
    )
    for directory in (ctx.prefix / "lib", ctx.prefix / "bin"):
        for pattern in ("*shaderc_shared*", "*SPIRV-Tools-shared*", "glslc", "glslc.exe"):
            for path in directory.glob(pattern):
                path.unlink()
    build_dir = ctx.work_root / source.name
    shutil.copy2(build_dir / "libshaderc_util" / "libshaderc_util.a", ctx.prefix / "lib")
    combined = ctx.prefix / "lib" / "pkgconfig" / "shaderc_combined.pc"
    _ensure_static_cpp_runtime(ctx, combined)
    shutil.copy2(combined, ctx.prefix / "lib" / "pkgconfig" / "shaderc.pc")


def build_fdk_aac(ctx: BuildContext) -> None:
    autotools(ctx, extract(ctx, "fdk_aac"), "--disable-example", "--with-pic", autoreconf=True)


def install_moltenvk(ctx: BuildContext) -> None:
    source = extract(ctx, "moltenvk")
    candidates = list(source.rglob("MoltenVK.xcframework/macos-arm64/libMoltenVK.a"))
    if not candidates:
        candidates = list(source.rglob("MoltenVK.xcframework/macos-arm64_x86_64/libMoltenVK.a"))
    if len(candidates) != 1:
        raise FileNotFoundError("MoltenVK static XCFramework arm64 slice was not found")
    (ctx.prefix / "lib").mkdir(exist_ok=True)
    shutil.copy2(candidates[0], ctx.prefix / "lib" / "libMoltenVK.a")
    vulkan_alias = ctx.prefix / "lib" / "libvulkan.a"
    if vulkan_alias.exists() or vulkan_alias.is_symlink():
        vulkan_alias.unlink()
    vulkan_alias.symlink_to("libMoltenVK.a")
    headers = source / "MoltenVK" / "include"
    if not headers.is_dir():
        raise FileNotFoundError("MoltenVK headers were not found")
    shutil.copytree(headers, ctx.prefix / "include", dirs_exist_ok=True)
    pc = ctx.prefix / "lib" / "pkgconfig"
    pc.mkdir(exist_ok=True)
    (pc / "vulkan.pc").write_text(
        f"prefix={ctx.prefix}\nlibdir=${{prefix}}/lib\nincludedir=${{prefix}}/include\n\n"
        "Name: Vulkan\nDescription: MoltenVK static Vulkan implementation\nVersion: 1.4.2\n"
        "Libs: -L${libdir} -lvulkan\n"
        "Libs.private: -lc++ -framework Metal -framework Foundation -framework QuartzCore "
        "-framework CoreGraphics -framework IOSurface -framework IOKit -framework AppKit\n"
        "Cflags: -I${includedir}\n",
        encoding="utf-8",
    )


# This is deliberately a fixed, readable build order. Add dependencies where they belong.
RECIPES: tuple[Recipe, ...] = (
    ("LLVM-MinGW notices", windows, record_llvm_mingw),
    ("zlib", always, build_zlib),
    ("openssl", always, build_openssl),
    ("expat", always, build_expat),
    ("iconv", always, build_iconv),
    ("xz", always, build_xz),
    ("libxml2", always, build_xml2),
    ("libffi", unix, build_ffi),
    ("pcre2", unix, build_pcre2),
    ("glib", unix, build_glib),
    ("vulkan-headers", always, build_vulkan_headers),
    ("MoltenVK", macos, install_moltenvk),
    ("vulkan-loader", not_macos, build_vulkan_loader),
    ("opencl-headers", not_macos, build_opencl_headers),
    ("opencl-loader", not_macos, build_opencl_loader),
    ("Implib.so", linux, prepare_implib),
    ("libdrm", linux, build_libdrm),
    ("libva", linux, build_libva),
    ("libpng", always, build_png),
    ("freetype", always, build_freetype),
    ("fontconfig", always, build_fontconfig),
    ("harfbuzz", always, build_harfbuzz),
    ("fribidi", always, build_fribidi),
    ("pixman", unix, build_pixman),
    ("cairo", unix, build_cairo),
    ("pango", unix, build_pango),
    ("libass", always, build_libass),
    ("libudfread", always, build_udfread),
    ("libbluray", always, build_bluray),
    ("libdvdcss", always, build_dvdcss),
    ("libdvdread", always, build_dvdread),
    ("libdvdnav", always, build_dvdnav),
    ("libaribcaption", always, build_aribcaption),
    ("aom", always, build_aom),
    ("opus", always, build_opus),
    ("dav1d", always, build_dav1d),
    ("librsvg", unix, build_rsvg),
    ("svt-av1", always, build_svtav1),
    ("libvpx", always, build_vpx),
    ("libmp3lame", always, build_lamer),
    ("libwebp", always, build_webp),
    ("libvmaf", always, build_vmaf),
    ("x264", always, build_x264),
    ("x265", always, build_x265),
    ("openjpeg", always, build_openjpeg),
    ("zimg", always, build_zimg),
    ("lc3", always, build_lc3),
    ("mysofa", always, build_mysofa),
    ("openal", always, build_openal),
    ("rubberband", always, build_rubberband),
    ("soxr", always, build_soxr),
    ("lv2", always, build_lv2),
    ("zix", always, build_zix),
    ("serd", always, build_serd),
    ("sord", always, build_sord),
    ("sratom", always, build_sratom),
    ("lilv", always, build_lilv),
    ("qrencode", always, build_qrencode),
    ("quirc", always, build_quirc),
    ("sdl2", always, build_sdl2),
    ("srt", always, build_srt),
    ("rist", always, build_rist),
    ("ssh", always, build_ssh),
    (
        "nv-codec-headers",
        not_macos,
        build_nvcodec,
    ),
    (
        "amf-headers",
        not_macos,
        build_amf,
    ),
    ("oneVPL", lambda ctx: not_macos(ctx) and x86(ctx), build_vpl),
    ("shaderc", always, build_shaderc),
    ("SPIRV-Cross", windows, build_spirv_cross),
    ("libplacebo", always, build_placebo),
    ("fdk-aac", fdk, build_fdk_aac),
)


def selected_recipes(ctx: BuildContext) -> tuple[Recipe, ...]:
    return tuple(recipe for recipe in RECIPES if recipe[1](ctx))


def build_dependencies(ctx: BuildContext) -> None:
    for name, _, recipe in selected_recipes(ctx):
        print(f"::group::{name}", flush=True)
        recipe(ctx)
        print("::endgroup::", flush=True)

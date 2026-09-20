from __future__ import annotations

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
    cmake(ctx, extract(ctx, "zlib"), "-DZLIB_BUILD_EXAMPLES=OFF")


def build_xz(ctx: BuildContext) -> None:
    cmake(
        ctx, extract(ctx, "xz"), "-DXZ_TOOL_XZ=OFF", "-DXZ_TOOL_XZDEC=OFF", "-DXZ_TOOL_LZMADEC=OFF"
    )


def build_openssl(ctx: BuildContext) -> None:
    source = extract(ctx, "openssl")
    platform = {
        "linux-x86_64": "linux-x86_64",
        "linux-arm64": "linux-aarch64",
        "windows-x86_64": "mingw64",
        "windows-arm64": "mingw64",
        "macos-arm64": "darwin64-arm64-cc",
    }[ctx.target.name]
    run(
        source / "Configure",
        platform,
        f"--prefix={ctx.prefix}",
        f"--openssldir={ctx.prefix / 'ssl'}",
        "no-shared",
        "no-tests",
        "no-apps",
        "no-docs",
        cwd=source,
        env=ctx.env(),
    )
    run("make", f"-j{ctx.jobs}", cwd=source, env=ctx.env())
    run("make", "install_sw", cwd=source, env=ctx.env())


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
    autotools(
        ctx,
        extract(ctx, "xml2"),
        "--without-python",
        "--without-http",
        autoreconf=True,
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
    autotools(ctx, extract(ctx, "dvdcss"), "--disable-doc", autoreconf=True)


def build_dvdread(ctx: BuildContext) -> None:
    autotools(ctx, extract(ctx, "dvdread"), "--disable-apidoc", autoreconf=True)


def build_dvdnav(ctx: BuildContext) -> None:
    autotools(ctx, extract(ctx, "dvdnav"), "--disable-examples", autoreconf=True)


def build_aribcaption(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "aribcaption"),
        "-DARIBCC_SHARED_LIBRARY=OFF",
        "-DARIBCC_BUILD_TESTS=OFF",
        "-DARIBCC_USE_FREETYPE=ON",
        "-DARIBCC_USE_EMBEDDED_FREETYPE=OFF",
    )


def build_openal(ctx: BuildContext) -> None:
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
        args += ["-DALSOFT_BACKEND_ALSA=OFF", "-DALSOFT_BACKEND_OSS=OFF"]
    cmake(ctx, extract(ctx, "openal"), *args)


def build_rubberband(ctx: BuildContext) -> None:
    meson(
        ctx,
        extract(ctx, "rubberband"),
        "-Dfft=builtin",
        "-Dresampler=builtin",
        "-Djni=disabled",
        "-Dladspa=disabled",
        "-Dlv2=disabled",
        "-Dvamp=disabled",
        "-Dcmdline=disabled",
        "-Dtests=disabled",
    )


def build_soxr(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "soxr"),
        "-DWITH_OPENMP=OFF",
        "-DBUILD_TESTS=OFF",
        "-DBUILD_EXAMPLES=OFF",
        "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
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
    meson(
        ctx,
        extract(ctx, "glib"),
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
    )


def build_rsvg(ctx: BuildContext) -> None:
    source = extract(ctx, "rsvg")
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
        "-Drsvg-convert=disabled",
        "-Dintrospection=disabled",
        "-Dvala=disabled",
        "-Ddocs=disabled",
        "-Dtests=false",
    )


def build_gmp(ctx: BuildContext) -> None:
    autotools(ctx, extract(ctx, "gmp"), "--disable-assembly")


def build_unistring(ctx: BuildContext) -> None:
    autotools(ctx, extract(ctx, "unistring"), "--disable-rpath")


def build_nettle(ctx: BuildContext) -> None:
    autotools(ctx, extract(ctx, "nettle"), "--disable-documentation", "--disable-openssl")


def build_gnutls(ctx: BuildContext) -> None:
    autotools(
        ctx,
        extract(ctx, "gnutls"),
        "--disable-cxx",
        "--disable-doc",
        "--disable-guile",
        "--disable-libdane",
        "--disable-nls",
        "--disable-tests",
        "--disable-tools",
        "--with-included-libtasn1",
        "--without-p11-kit",
    )


def build_vulkan_headers(ctx: BuildContext) -> None:
    cmake(ctx, extract(ctx, "vulkan_headers"), "-DVULKAN_HEADERS_ENABLE_TESTS=OFF")


def build_vulkan_loader(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "vulkan_loader"),
        "-DBUILD_TESTS=OFF",
        "-DBUILD_WSI_XCB_SUPPORT=OFF",
        "-DBUILD_WSI_XLIB_SUPPORT=OFF",
        "-DBUILD_WSI_WAYLAND_SUPPORT=OFF",
        "-DBUILD_WSI_DIRECTFB_SUPPORT=OFF",
    )


def build_opencl_headers(ctx: BuildContext) -> None:
    cmake(ctx, extract(ctx, "opencl_headers"), "-DOPENCL_HEADERS_BUILD_TESTING=OFF")


def build_opencl_loader(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "opencl_loader"),
        "-DOPENCL_ICD_LOADER_BUILD_TESTING=OFF",
        "-DOPENCL_ICD_LOADER_BUILD_SHARED_LIBS=OFF",
        f"-DOPENCL_ICD_LOADER_HEADERS_DIR={ctx.prefix / 'include'}",
    )


def build_libdrm(ctx: BuildContext) -> None:
    meson(
        ctx,
        extract(ctx, "libdrm"),
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
    )


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
    for source in generated:
        object_file = work / f"{source.name}.o"
        run(
            "ccache",
            "gcc",
            "-O2",
            "-fPIC",
            "-Wa,--noexecstack",
            "-DIMPLIB_HIDDEN_SHIMS",
            "-c",
            source,
            "-o",
            object_file,
            cwd=work,
            env=ctx.env(),
        )
        objects.append(object_file)
    run("ar", "rcs", output, *objects, cwd=work, env=ctx.env())


def _add_implib_link_flags(pkg_config: Path) -> None:
    lines = pkg_config.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("Libs.private:"):
            lines[index] = f"{line} -ldl -pthread"
            break
    else:
        lines.append("Libs.private: -ldl -pthread")
    pkg_config.write_text("\n".join(lines) + "\n", encoding="utf-8")


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
    meson(ctx, extract(ctx, "fontconfig"), "-Ddoc=disabled", "-Dtests=disabled", "-Dtools=disabled")


def build_harfbuzz(ctx: BuildContext) -> None:
    meson(
        ctx,
        extract(ctx, "harfbuzz"),
        "-Dtests=disabled",
        "-Ddocs=disabled",
        "-Dutilities=disabled",
        "-Dglib=disabled",
        "-Dgobject=disabled",
        "-Dcairo=disabled",
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
        "-DBUILD_DEC=OFF",
        "-DBUILD_ENC=ON",
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
    run(
        source / "configure",
        f"--prefix={ctx.prefix}",
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
    make(ctx, build_dir)


def build_lamer(ctx: BuildContext) -> None:
    source = extract(ctx, "lamer")
    run("make", f"-j{ctx.jobs}", cwd=source, env=ctx.env())
    run("make", f"PREFIX={ctx.prefix}", "install", cwd=source, env=ctx.env())


def build_png(ctx: BuildContext) -> None:
    cmake(ctx, extract(ctx, "png"), "-DPNG_SHARED=OFF", "-DPNG_TESTS=OFF", "-DPNG_TOOLS=OFF")


def build_webp(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "webp"),
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


def build_openjpeg(ctx: BuildContext) -> None:
    cmake(ctx, extract(ctx, "openjpeg"), "-DBUILD_CODEC=OFF", "-DBUILD_TESTING=OFF")


def build_zimg(ctx: BuildContext) -> None:
    autotools(ctx, extract(ctx, "zimg"), autoreconf=True)


def build_lc3(ctx: BuildContext) -> None:
    meson(ctx, extract(ctx, "lc3"), "-Dtools=false", "-Dpython=false")


def build_mysofa(ctx: BuildContext) -> None:
    cmake(ctx, extract(ctx, "mysofa"), "-DBUILD_TESTS=OFF", "-DBUILD_SHARED_LIBS=OFF")


def build_qrencode(ctx: BuildContext) -> None:
    cmake(ctx, extract(ctx, "qrencode"), "-DWITH_TOOLS=NO", "-DWITH_TESTS=NO")


def build_quirc(ctx: BuildContext) -> None:
    source = extract(ctx, "quirc")
    run("make", f"-j{ctx.jobs}", "libquirc.a", cwd=source, env=ctx.env())
    (ctx.prefix / "include").mkdir(exist_ok=True)
    (ctx.prefix / "lib").mkdir(exist_ok=True)
    shutil.copy2(source / "lib" / "quirc.h", ctx.prefix / "include")
    shutil.copy2(source / "libquirc.a", ctx.prefix / "lib")
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
        "-DSDL_TEST_LIBRARY=OFF",
    )


def build_srt(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "srt"),
        "-DENABLE_SHARED=OFF",
        "-DENABLE_STATIC=ON",
        "-DENABLE_APPS=OFF",
        "-DENABLE_ENCRYPTION=OFF",
    )


def build_rist(ctx: BuildContext) -> None:
    meson(ctx, extract(ctx, "rist"), "-Dtest=false", "-Dtools=false", "-Dbuiltin_cjson=true")


def build_ssh(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "ssh"),
        "-DWITH_STATIC_LIB=ON",
        "-DWITH_EXAMPLES=OFF",
        "-DWITH_SERVER=OFF",
        "-DWITH_GCRYPT=OFF",
    )


def build_curl(ctx: BuildContext) -> None:
    tls = (
        ["-DCURL_USE_SCHANNEL=ON", "-DCURL_USE_OPENSSL=OFF"]
        if ctx.target.windows
        else ["-DCURL_USE_OPENSSL=ON"]
    )
    cmake(
        ctx,
        extract(ctx, "curl"),
        "-DBUILD_CURL_EXE=OFF",
        "-DBUILD_TESTING=OFF",
        "-DCURL_USE_LIBPSL=OFF",
        "-DCURL_ZSTD=OFF",
        "-DCURL_BROTLI=OFF",
        "-DCURL_DISABLE_LDAP=ON",
        *tls,
    )


def build_nvcodec(ctx: BuildContext) -> None:
    source = extract(ctx, "nvcodec")
    run("make", f"PREFIX={ctx.prefix}", "install", cwd=source, env=ctx.env())


def build_amf(ctx: BuildContext) -> None:
    source = extract(ctx, "amf")
    include = ctx.prefix / "include" / "AMF"
    include.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source / "amf" / "public" / "include", include, dirs_exist_ok=True)


def build_vpl(ctx: BuildContext) -> None:
    cmake(
        ctx,
        extract(ctx, "vpl"),
        "-DINSTALL_EXAMPLES=OFF",
        "-DBUILD_TESTS=OFF",
        "-DBUILD_EXAMPLES=OFF",
        "-DBUILD_TOOLS=OFF",
    )


def build_placebo(ctx: BuildContext) -> None:
    source = extract(ctx, "placebo")
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
        "-Dshaderc=disabled",
        "-Ddemos=false",
        "-Dtests=false",
        "-Dbench=false",
        *platform,
    )


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
    headers = source / "MoltenVK" / "include"
    if not headers.is_dir():
        raise FileNotFoundError("MoltenVK headers were not found")
    shutil.copytree(headers, ctx.prefix / "include", dirs_exist_ok=True)
    pc = ctx.prefix / "lib" / "pkgconfig"
    pc.mkdir(exist_ok=True)
    (pc / "vulkan.pc").write_text(
        f"prefix={ctx.prefix}\nlibdir=${{prefix}}/lib\nincludedir=${{prefix}}/include\n\n"
        "Name: Vulkan\nDescription: MoltenVK static Vulkan implementation\nVersion: 1.4.2\n"
        "Libs: -L${libdir} -lMoltenVK\n"
        "Libs.private: -lc++ -framework Metal -framework Foundation -framework QuartzCore "
        "-framework CoreGraphics -framework IOSurface -framework IOKit -framework AppKit\n"
        "Cflags: -I${includedir}\n",
        encoding="utf-8",
    )


# This is deliberately a fixed, readable build order. Add dependencies where they belong.
RECIPES: tuple[Recipe, ...] = (
    ("LLVM-MinGW notices", windows, record_llvm_mingw),
    ("zlib", always, build_zlib),
    ("openssl", lambda ctx: ctx.target.name != "windows-arm64", build_openssl),
    ("expat", always, build_expat),
    ("iconv", always, build_iconv),
    ("xz", always, build_xz),
    ("libxml2", always, build_xml2),
    ("libffi", unix, build_ffi),
    ("pcre2", unix, build_pcre2),
    ("glib", unix, build_glib),
    ("gmp", linux, build_gmp),
    ("unistring", linux, build_unistring),
    ("nettle", linux, build_nettle),
    ("gnutls", linux, build_gnutls),
    ("vulkan-headers", always, build_vulkan_headers),
    ("MoltenVK", macos, install_moltenvk),
    ("vulkan-loader", not_macos, build_vulkan_loader),
    ("opencl-headers", not_macos, build_opencl_headers),
    ("opencl-loader", not_macos, build_opencl_loader),
    ("libdrm", linux, build_libdrm),
    ("Implib.so", linux, prepare_implib),
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
    ("ssh", lambda ctx: ctx.target.name != "windows-arm64", build_ssh),
    ("curl", always, build_curl),
    (
        "nv-codec-headers",
        lambda ctx: not_macos(ctx) and ctx.target.name != "windows-arm64",
        build_nvcodec,
    ),
    ("amf-headers", lambda ctx: not_macos(ctx) and x86(ctx), build_amf),
    ("oneVPL", lambda ctx: not_macos(ctx) and x86(ctx), build_vpl),
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

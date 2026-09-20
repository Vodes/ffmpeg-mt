from __future__ import annotations

import atexit
import hashlib
import os
import shutil
import subprocess
import tarfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from urllib.parse import unquote, urlparse

import niquests

from .model import BuildContext, Source

HTTP_SESSION = niquests.Session(
    timeout=(10, 60),
    retries=niquests.RetryConfiguration(
        total=5,
        backoff_factor=0.5,
        status_forcelist={429, 500, 502, 503, 504},
        allowed_methods={"GET"},
        respect_retry_after_header=True,
    ),
    headers={"User-Agent": "ffmpeg-mt-builder/1"},
)
atexit.register(HTTP_SESSION.close)


def run(
    *command: str | os.PathLike[str],
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    capture: bool = False,
) -> str:
    printable = " ".join(map(str, command))
    print(f"+ {printable}", flush=True)
    result = subprocess.run(
        [str(item) for item in command],
        cwd=cwd,
        env=env,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )
    return result.stdout if capture else ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download_url(url: str, destination: Path) -> None:
    parsed = urlparse(url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if parsed.scheme == "file":
        shutil.copyfile(Path(unquote(parsed.path)), destination)
        return
    with HTTP_SESSION.get(url, stream=True, allow_redirects=True) as response:
        response.raise_for_status()
        with destination.open("wb") as output:
            for block in response.iter_content(chunk_size=1024 * 1024):
                if block:
                    output.write(block)


def fetch_text(url: str) -> str:
    with HTTP_SESSION.get(url, allow_redirects=True) as response:
        response.raise_for_status()
        content = response.text
        if not isinstance(content, str):
            raise TypeError(f"expected a text response from {url}")
        return content


def download(source: Source, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    url_path = urlparse(source.url).path
    archive_suffix = next(
        (
            suffix
            for suffix in (".tar.gz", ".tar.xz", ".tar.bz2", ".tar.zst", ".tgz", ".tar")
            if url_path.endswith(suffix)
        ),
        Path(url_path).suffix,
    )
    archive = destination / f"{source.name}-{source.version}{archive_suffix}"
    if archive.exists() and sha256(archive) == source.sha256:
        return archive
    archive.unlink(missing_ok=True)
    partial = archive.with_suffix(archive.suffix + ".part")
    partial.unlink(missing_ok=True)
    print(f"Downloading {source.url}", flush=True)
    download_url(source.url, partial)
    actual = sha256(partial)
    if actual != source.sha256:
        partial.unlink(missing_ok=True)
        raise ValueError(f"{source.name}: SHA-256 mismatch: expected {source.sha256}, got {actual}")
    partial.replace(archive)
    return archive


def extract(ctx: BuildContext, name: str) -> Path:
    source = ctx.sources[name]
    archive = download(source, ctx.downloads)
    destination = ctx.source_root / name
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    extract_tar(archive, destination, name)
    roots = [item for item in destination.iterdir() if item.name not in {"__MACOSX"}]
    result = roots[0] if len(roots) == 1 and roots[0].is_dir() else destination
    ctx.source_dirs[name] = result
    return result


def extract_tar(archive: Path, destination: Path, name: str | None = None) -> None:
    label = name or archive.name
    with tarfile.open(archive) as bundle:
        try:
            bundle.extractall(destination, filter="data")
        except tarfile.FilterError as error:
            raise ValueError(f"{label}: unsafe archive member: {error}") from error


def _host_args(ctx: BuildContext) -> list[str]:
    return [f"--host={ctx.target.host}"] if ctx.target.host else []


def autotools(
    ctx: BuildContext,
    source: Path,
    *arguments: str,
    configure: str = "configure",
    autoreconf: bool = False,
    install_prefix: str | Path | None = None,
    install_destdir: Path | None = None,
) -> None:
    env = ctx.env()
    if autoreconf:
        run("autoreconf", "-fiv", cwd=source, env=env)
    build_dir = ctx.work_root / source.name
    build_dir.mkdir(parents=True, exist_ok=True)
    run(
        str(source / configure),
        f"--prefix={install_prefix if install_prefix is not None else ctx.prefix}",
        "--disable-shared",
        "--enable-static",
        *_host_args(ctx),
        *arguments,
        cwd=build_dir,
        env=env,
    )
    run("make", f"-j{ctx.jobs}", cwd=build_dir, env=env)
    install_variables = [f"DESTDIR={install_destdir}"] if install_destdir is not None else []
    run("make", *install_variables, "install", cwd=build_dir, env=env)


def cmake(
    ctx: BuildContext,
    source: Path,
    *arguments: str,
    install_prefix: str | Path | None = None,
    install_destdir: Path | None = None,
    after_configure: Callable[[Path], None] | None = None,
) -> None:
    build_dir = ctx.work_root / source.name
    toolchain: list[str] = []
    env = ctx.env()
    selected_prefix = install_prefix if install_prefix is not None else ctx.prefix
    if ctx.target.windows:
        toolchain = [f"-DCMAKE_TOOLCHAIN_FILE={ctx.root / 'cmake' / 'llvm-mingw.cmake'}"]
    else:
        env["CC"] = "clang" if ctx.target.macos else "gcc"
        env["CXX"] = "clang++" if ctx.target.macos else "g++"
    run(
        "cmake",
        "-S",
        source,
        "-B",
        build_dir,
        "-GNinja",
        f"-DCMAKE_INSTALL_PREFIX={selected_prefix}",
        "-DCMAKE_INSTALL_LIBDIR=lib",
        "-DCMAKE_BUILD_TYPE=Release",
        "-DBUILD_SHARED_LIBS=OFF",
        "-DCMAKE_C_COMPILER_LAUNCHER=ccache",
        "-DCMAKE_CXX_COMPILER_LAUNCHER=ccache",
        *toolchain,
        *arguments,
        env=env,
    )
    if after_configure is not None:
        after_configure(build_dir)
    run("cmake", "--build", build_dir, "--parallel", str(ctx.jobs), env=env)
    install_env = dict(env)
    if install_destdir is not None:
        install_env["DESTDIR"] = str(install_destdir)
    run("cmake", "--install", build_dir, env=install_env)


def meson(
    ctx: BuildContext,
    source: Path,
    *arguments: str,
    install_prefix: str | Path | None = None,
    install_destdir: Path | None = None,
) -> None:
    build_dir = ctx.work_root / source.name
    cross: list[str] = []
    env = ctx.env()
    selected_prefix = install_prefix if install_prefix is not None else ctx.prefix
    if ctx.target.windows:
        cross = ["--cross-file", str(ctx.root / "meson" / f"{ctx.target.name}.ini")]
    run(
        "meson",
        "setup",
        build_dir,
        source,
        f"--prefix={selected_prefix}",
        "--libdir=lib",
        "--default-library=static",
        "--buildtype=release",
        "--wrap-mode=nodownload",
        *cross,
        *arguments,
        env=env,
    )
    run("meson", "compile", "-C", build_dir, "-j", str(ctx.jobs), env=env)
    install_env = dict(env)
    if install_destdir is not None:
        install_env["DESTDIR"] = str(install_destdir)
    run("meson", "install", "-C", build_dir, env=install_env)


def make(
    ctx: BuildContext,
    directory: Path,
    *targets: str,
    variables: Iterable[str] = (),
) -> None:
    selected = targets or ("all",)
    run("make", f"-j{ctx.jobs}", *variables, *selected, cwd=directory, env=ctx.env())
    if not targets:
        run("make", *variables, "install", cwd=directory, env=ctx.env())


def copy_license_files(source_dir: Path, patterns: Sequence[str], destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    copied: set[str] = set()
    for pattern in patterns:
        for item in sorted(source_dir.glob(pattern)):
            relative = item.relative_to(source_dir)
            key = relative.as_posix()
            if item.is_file() and key not in copied:
                output = destination / relative
                output.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, output)
                copied.add(key)
    if not copied:
        raise FileNotFoundError(f"no license file matching {patterns!r} in {source_dir}")

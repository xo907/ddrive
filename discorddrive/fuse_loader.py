"""Locates the WinFsp FUSE DLL or Linux libfuse and imports the vendored fusepy module."""

import ctypes
from ctypes.util import find_library
import glob
import logging
import os
import platform
import shutil
import subprocess
import sys

log = logging.getLogger("discorddrive.fuse_loader")


def _winfsp_install_dirs():
    dirs = []
    try:
        import winreg

        for flags in (winreg.KEY_READ | winreg.KEY_WOW64_32KEY, winreg.KEY_READ):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WinFsp", 0, flags) as k:
                    dirs.append(winreg.QueryValueEx(k, "InstallDir")[0])
            except OSError:
                pass
    except ImportError:
        pass
    dirs.append(os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "WinFsp"))
    return dirs


def find_winfsp_dll():
    if sys.platform != "win32":
        return None
    machine = platform.machine().lower()
    arch = "a64" if machine in ("arm64", "aarch64") else ("x64" if sys.maxsize > 2**32 else "x86")
    for d in _winfsp_install_dirs():
        direct = os.path.join(d, "bin", f"winfsp-{arch}.dll")
        if os.path.exists(direct):
            return direct
        # WinFsp 2.1+ uses a side-by-side layout: <InstallDir>\SxS\sxs.<stamp>\bin\
        sxs = sorted(glob.glob(os.path.join(d, "SxS", "*", "bin", f"winfsp-{arch}.dll")),
                     key=os.path.getmtime, reverse=True)
        if sxs:
            return sxs[0]
    return None


def find_linux_fuse_lib():
    """Finds libfuse.so.2 on Linux across Debian, Ubuntu, Raspberry Pi OS, Fedora, Arch."""
    if not sys.platform.startswith("linux"):
        return None

    # 1. Standard ctypes find_library
    try:
        lib = find_library("fuse")
        if lib:
            return lib
    except Exception:
        pass

    # 2. Try loading common sonames directly via standard dynamic linker search
    for name in ("libfuse.so.2", "libfuse.so"):
        try:
            ctypes.CDLL(name)
            return name
        except (OSError, TypeError):
            pass

    # 3. Search common multiarch and standard filesystem paths
    patterns = [
        "/lib/*/libfuse.so.2*",
        "/usr/lib/*/libfuse.so.2*",
        "/lib/libfuse.so.2*",
        "/usr/lib/libfuse.so.2*",
        "/usr/local/lib/libfuse.so.2*",
        "/lib/*/libfuse.so*",
        "/usr/lib/*/libfuse.so*",
    ]
    for pattern in patterns:
        for match in sorted(glob.glob(pattern)):
            try:
                ctypes.CDLL(match)
                return match
            except OSError:
                pass

    return None


def unmount(mount_point, lazy=False) -> bool:
    """Unmount a FUSE mount on Linux with fusermount (FUSE 2), fusermount3 (FUSE 3; also
    handles FUSE 2 mounts) or, as root without either, plain umount."""
    tool = shutil.which("fusermount") or shutil.which("fusermount3")
    if tool:
        cmd = [tool, "-uz" if lazy else "-u", mount_point]
    else:
        cmd = ["umount", "-l", mount_point] if lazy else ["umount", mount_point]
    try:
        return subprocess.run(cmd, capture_output=True).returncode == 0
    except OSError:
        return False


class _DummyOperations:
    pass


class _DummyFuseOSError(OSError):
    """Like the real one: made from an error number alone, which it then carries."""

    def __init__(self, errno):
        super().__init__(errno, os.strerror(errno))


class _DummyFuseModule:
    Operations = _DummyOperations
    FuseOSError = _DummyFuseOSError

    def __init__(self, err_msg: str):
        self._err_msg = err_msg

    def FUSE(self, *args, **kwargs):
        raise RuntimeError(self._err_msg)


FUSE_ERROR = None

_LIBFUSE_MISSING = (
    "libfuse2 was not found on this Linux system.\n"
    "FUSE is required to mount the virtual DiscordDrive filesystem.\n\n"
    "To install it on Debian / Ubuntu / Raspberry Pi OS: ./run.sh -> Tools -> Install requirements\n"
    "or: apt install libfuse2   (libfuse2t64 on Debian 13+ / Ubuntu 24.04+)"
)


def load_fuse():
    global FUSE_ERROR
    if not os.environ.get("FUSE_LIBRARY_PATH"):
        if sys.platform == "win32":
            dll = find_winfsp_dll()
            if not dll:
                FUSE_ERROR = (
                    "WinFsp was not found. Install it from https://winfsp.dev/rel/ "
                    "(the default 'Core' feature is enough) and try again."
                )
                return _DummyFuseModule(FUSE_ERROR)
            os.environ["FUSE_LIBRARY_PATH"] = dll
        elif sys.platform.startswith("linux"):
            lib = find_linux_fuse_lib()
            if lib:
                os.environ["FUSE_LIBRARY_PATH"] = lib
            else:
                FUSE_ERROR = _LIBFUSE_MISSING
                return _DummyFuseModule(FUSE_ERROR)

    try:
        from ._vendor import fuse
        return fuse
    except (EnvironmentError, OSError) as e:
        if sys.platform.startswith("linux"):
            FUSE_ERROR = _LIBFUSE_MISSING
        else:
            FUSE_ERROR = f"Failed to load FUSE: {e}"
        return _DummyFuseModule(FUSE_ERROR)


fuse = load_fuse()


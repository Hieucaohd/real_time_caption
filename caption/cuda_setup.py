"""Make NVIDIA libraries (cuBLAS, cuDNN) discoverable on Windows.

CTranslate2 loads cuBLAS/cuDNN lazily through the normal DLL search path. The
``nvidia-*-cu12`` wheels drop their DLLs into ``site-packages/nvidia/<lib>/bin``,
which Windows does not search by default, so we register those folders here. A
packaged GPU build carries the same folders under ``_internal/nvidia``; a CPU build
has none, and then CUDA must not be attempted (a missing cuDNN can abort the process
instead of raising).
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path

from .paths import BUNDLE_DIR

_NVIDIA_LIBS = ("cublas", "cudnn", "cuda_nvrtc")
_REQUIRED_DLLS = ("cublas64_12.dll", "cublasLt64_12.dll", "cudnn64_9.dll")


def _nvidia_roots() -> list[Path]:
    roots = [BUNDLE_DIR / "nvidia"]
    try:
        import nvidia  # namespace package provided by nvidia-*-cu12 wheels

        roots += [Path(p) for p in getattr(nvidia, "__path__", [])]
    except ImportError:
        pass
    return roots


def register_nvidia_dlls() -> list[str]:
    """Add NVIDIA ``bin`` folders to the DLL search path. Returns the folders added."""
    added: list[str] = []
    for root in _nvidia_roots():
        for lib in _NVIDIA_LIBS:
            bin_dir = root / lib / "bin"
            if not bin_dir.is_dir() or str(bin_dir) in added:
                continue
            os.add_dll_directory(str(bin_dir))
            # Some loaders ignore AddDllDirectory, so PATH is updated as well.
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            added.append(str(bin_dir))
    return added


def cuda_runtime_available() -> bool:
    """True if the CUDA libraries CTranslate2 needs can actually be loaded."""
    for name in _REQUIRED_DLLS:
        try:
            ctypes.WinDLL(name)
        except OSError:
            return False
    return True

"""Make pip-installed NVIDIA libraries (cuBLAS, cuDNN) discoverable on Windows.

CTranslate2 loads cuBLAS/cuDNN lazily through the normal DLL search path. The
``nvidia-*-cu12`` wheels drop their DLLs into ``site-packages/nvidia/<lib>/bin``,
which Windows does not search by default, so we register those folders here.
"""

from __future__ import annotations

import os

_NVIDIA_LIBS = ("cublas", "cudnn", "cuda_nvrtc")


def register_nvidia_dlls() -> list[str]:
    """Add NVIDIA wheel ``bin`` folders to the DLL search path. Returns the folders added."""
    try:
        import nvidia  # namespace package provided by nvidia-*-cu12 wheels
    except ImportError:
        return []

    added: list[str] = []
    for root in list(getattr(nvidia, "__path__", [])):
        for lib in _NVIDIA_LIBS:
            bin_dir = os.path.join(root, lib, "bin")
            if not os.path.isdir(bin_dir):
                continue
            os.add_dll_directory(bin_dir)
            # Some loaders ignore AddDllDirectory, so PATH is updated as well.
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
            added.append(bin_dir)
    return added

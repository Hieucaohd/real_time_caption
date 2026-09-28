"""Build a portable Windows folder: dist/RealTimeCaption/RealTimeCaption.exe (no Python needed).

    python packaging/build_exe.py                  CPU build (runs on any Windows 10/11 PC)
    python packaging/build_exe.py --gpu            + NVIDIA cuBLAS/cuDNN (~2 GB) for fast GPU captions
    python packaging/build_exe.py --with-models    + the Whisper models already downloaded in models/
    python packaging/build_exe.py --zip            + dist/RealTimeCaption-<variant>.zip to copy around

The result is a PyInstaller "onedir" build: a folder with the .exe and an _internal/
folder. (A single-file .exe would unpack hundreds of MB to %TEMP% on every start.)
Settings, prompts, transcripts and downloaded models are kept next to the .exe.
"""

from __future__ import annotations

import argparse
import shutil
import site
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGING = ROOT / "packaging"
NAME = "RealTimeCaption"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
ICON = ROOT / "assets" / "icon.ico"
NVIDIA_LIBS = ("cublas", "cudnn", "cuda_nvrtc")

# Packages whose DLLs / data files PyInstaller's import analysis doesn't find on its own.
COLLECT_ALL = [
    "ctranslate2",  # CTranslate2 runtime DLLs
    "pyaudiowpatch",  # PortAudio with WASAPI loopback
    "uiautomation",  # reading Windows Live Captions
    "tkinterweb",  # HTML view for ChatGPT answers
    "tkinterweb_tkhtml",  # its Tkhtml binaries
    "pythonmonkey",  # JavaScript runtime used by the offline MathJax bridge
    "pminit",  # PythonMonkey runtime bootstrap files
    "resvg_py",  # native SVG-to-PNG renderer for MathJax output
    "playwright",  # driver (node.exe) used to talk to Chrome over CDP
]
COLLECT_DATA = ["faster_whisper"]  # bundled Silero VAD model
COLLECT_SUBMODULES = ["mdit_py_plugins", "comtypes"]


def site_packages() -> Path:
    for path in map(Path, site.getsitepackages()):
        if (path / "faster_whisper").is_dir():
            return path
    raise SystemExit("Run this with the project's virtual environment (.venv) so all libraries are found.")


def pyinstaller_args(gpu: bool) -> list[str]:
    sep = ";"  # PyInstaller's src;dest separator on Windows
    args = [
        str(ROOT / "main.py"),
        "--name", NAME,
        "--noconfirm",
        "--clean",
        "--windowed",  # no console window
        "--icon", str(ICON),
        "--distpath", str(DIST),
        "--workpath", str(BUILD),
        "--specpath", str(BUILD),
        "--add-data", f"{ROOT / 'prompts'}{sep}prompts",
        "--add-data", f"{ICON}{sep}assets",
        "--add-data", f"{ROOT / 'assets' / 'mathjax-runtime'}{sep}assets/mathjax-runtime",
    ]
    for pkg in COLLECT_ALL:
        args += ["--collect-all", pkg]
    for pkg in COLLECT_DATA:
        args += ["--collect-data", pkg]
    for pkg in COLLECT_SUBMODULES:
        args += ["--collect-submodules", pkg]
    if gpu:
        nvidia = site_packages() / "nvidia"
        for lib in NVIDIA_LIBS:
            for dll in sorted((nvidia / lib / "bin").glob("*.dll")):
                args += ["--add-binary", f"{dll}{sep}nvidia/{lib}/bin"]
    return args


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gpu", action="store_true", help="bundle NVIDIA cuBLAS/cuDNN for GPU transcription")
    parser.add_argument("--with-models", action="store_true", help="bundle the Whisper models in models/")
    parser.add_argument("--zip", action="store_true", help="also create a .zip of the build")
    opts = parser.parse_args()

    import PyInstaller.__main__

    if not ICON.exists():
        sys.path.insert(0, str(PACKAGING))
        import make_icon

        ICON.parent.mkdir(parents=True, exist_ok=True)
        make_icon.draw().save(ICON, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])

    PyInstaller.__main__.run(pyinstaller_args(opts.gpu))

    app_dir = DIST / NAME
    shutil.copy2(PACKAGING / "Open Chrome for ChatGPT.bat", app_dir)
    shutil.copy2(PACKAGING / "HUONG-DAN.txt", app_dir)
    if opts.with_models:
        models = ROOT / "models"
        if models.is_dir():
            print("Copying Whisper models…")
            shutil.copytree(models, app_dir / "models", dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns(".locks", "*.lock", "*.incomplete"))
        else:
            print("No models/ folder yet: run the app once to download a model, then rebuild.")

    variant = "gpu" if opts.gpu else "cpu"
    size_mb = sum(f.stat().st_size for f in app_dir.rglob("*") if f.is_file()) / 2**20
    print(f"\nBuilt {app_dir} ({variant}, {size_mb:,.0f} MB)")
    if opts.zip:
        archive = shutil.make_archive(str(DIST / f"{NAME}-{variant}"), "zip", DIST, NAME)
        print(f"Zipped to {archive}")


if __name__ == "__main__":
    main()

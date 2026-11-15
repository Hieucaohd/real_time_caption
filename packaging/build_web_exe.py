"""Build and smoke-test dist/RealTimeCaptionWeb.exe (onefile, no Python/Node needed)."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / 'build' / 'web-exe'
OUTPUT = ROOT / 'dist' / 'RealTimeCaptionWeb.exe'


def pyinstaller_args(with_translation=False):
    args = [str(ROOT / 'web_main.py'), '--name', 'RealTimeCaptionWeb', '--onefile',
            '--windowed', '--noconfirm', '--clean', '--icon', str(ROOT / 'assets/icon.ico'),
            '--distpath', str(OUTPUT.parent), '--workpath', str(WORK), '--specpath', str(WORK)]
    for source, destination in [('web/dist', 'web/dist'), ('prompts', 'prompts'),
                                ('assets/icon.ico', 'assets'), ('assets/mathjax-runtime', 'assets/mathjax-runtime')]:
        args += ['--add-data', f'{ROOT / source};{destination}']
    for package in ('playwright', 'uiautomation', 'pyaudiowpatch', 'tkinterweb', 'tkinterweb_tkhtml',
                    'pythonmonkey', 'pminit', 'resvg_py'):
        args += ['--collect-all', package]
    for package in ('docx', 'reportlab'):
        args += ['--collect-data', package]
    for package in ('uvicorn', 'comtypes', 'mdit_py_plugins', 'reportlab'):
        args += ['--collect-submodules', package]
    for module in ('web_api.overlay', 'web_api.selftest'):
        args += ['--hidden-import', module]
    # The web app captures only Windows Live Captions; no Whisper/CUDA models.
    excluded = ['faster_whisper', 'ctranslate2', 'nvidia', 'IPython', 'pytest', 'pandas', 'scipy']
    if with_translation:
        for package in ('torch', 'transformers', 'sentencepiece', 'sacremoses'):
            args += ['--collect-all', package]
    else:
        excluded += ['torch', 'transformers', 'sentencepiece', 'sacremoses']
    for module in excluded:
        args += ['--exclude-module', module]
    return args


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--with-translation', action='store_true', help='include optional VinAI dependencies (install requirements-translation.txt first)')
    args = parser.parse_args()
    if sys.platform != 'win32':
        raise SystemExit('Build this Windows EXE on Windows.')
    if not (ROOT / 'web/dist/index.html').is_file():
        raise SystemExit('Run build_web_exe.bat to build the React assets first.')
    if args.with_translation:
        import importlib.util
        for name in ('torch', 'transformers', 'sentencepiece', 'sacremoses'):
            if importlib.util.find_spec(name) is None:
                raise SystemExit('Install requirements-translation.txt before using --with-translation.')
    WORK.mkdir(parents=True, exist_ok=True)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    print('Packaging web app. Progress is saved to build/web-exe/build.log.', flush=True)
    with (WORK / 'build.log').open('w', encoding='utf-8') as logfile:
        result = subprocess.run([sys.executable, '-m', 'PyInstaller', *pyinstaller_args(args.with_translation)],
                                cwd=ROOT, stdout=logfile, stderr=subprocess.STDOUT)
    if result.returncode:
        raise SystemExit(f'PyInstaller failed. See {WORK / "build.log"}')
    print('Checking the actual EXE in an isolated data folder...', flush=True)
    with tempfile.TemporaryDirectory(prefix='rtc-web-build-') as folder:
        check = subprocess.run([str(OUTPUT), '--self-test', '--data-dir', folder], timeout=180)
        report = Path(folder) / 'web-selftest.json'
        if report.exists():
            shutil.copy2(report, WORK / 'selftest.json')
        log = Path(folder) / 'caption-web.log'
        if log.exists():
            shutil.copy2(log, WORK / 'selftest.log')
        if check.returncode or not report.is_file() or not json.loads(report.read_text(encoding='utf-8'))['ok']:
            raise SystemExit(f'EXE verification failed. See {WORK / "selftest.json"} and selftest.log.')
    shutil.copy2(ROOT / 'packaging/WEB-EXE-HUONG-DAN.txt', OUTPUT.parent / 'WEB-EXE-HUONG-DAN.txt')
    print(f'Built and verified: {OUTPUT} ({OUTPUT.stat().st_size / 2**20:.1f} MB)', flush=True)


if __name__ == '__main__':
    main()

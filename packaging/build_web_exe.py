"""Build and smoke-test a Git-tagged web EXE (onefile, no Python/Node needed)."""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / 'build' / 'web-exe'


def build_identity(tag):
    match = re.fullmatch(r'web-build-(\d{4})(\d{4})-(\d{4})(\d{2})-(\d{3})', tag, flags=re.ASCII)
    if not match:
        raise ValueError('Expected build tag: web-build-YYYYMMDD-HHMMSS-fff')
    year, month_day, hour_minute, second, millis = map(int, match.groups())
    # Windows fixed version fields are four 16-bit integers. The visible string
    # versions below retain the complete Git tag, including its prefix.
    version = (year, month_day, hour_minute, second * 1000 + millis)
    if any(value > 65535 for value in version):
        raise ValueError('Build tag cannot be represented as a Windows version')
    return f'RealTimeCaptionWeb-{tag}', version


def write_version_info(tag, destination):
    name, version = build_identity(tag)
    from PyInstaller.utils.win32.versioninfo import (
        VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct,
    )
    info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=version, prodvers=version, mask=0x3f, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
        kids=[StringFileInfo([StringTable('040904B0', [
            StringStruct('FileDescription', 'Real-time Caption Web'),
            StringStruct('ProductName', 'Real-time Caption Web'),
            StringStruct('FileVersion', tag), StringStruct('ProductVersion', tag),
            StringStruct('InternalName', name), StringStruct('OriginalFilename', name + '.exe'),
        ])]), VarFileInfo([VarStruct('Translation', [0x0409, 1200])])],
    )
    destination.write_text(str(info), encoding='utf-8')


def pyinstaller_args(tag, version_file, with_translation=False):
    name, _ = build_identity(tag)
    args = [str(ROOT / 'web_main.py'), '--name', name, '--version-file', str(version_file), '--onefile',
            '--windowed', '--noconfirm', '--clean', '--icon', str(ROOT / 'assets/icon.ico'),
            '--distpath', str(ROOT / 'dist'), '--workpath', str(WORK), '--specpath', str(WORK)]
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
    parser.add_argument('--build-tag', required=True, help='Git tag created by build_web_exe.bat')
    parser.add_argument('--with-translation', action='store_true', help='include optional VinAI dependencies (install requirements-translation.txt first)')
    args = parser.parse_args()
    if sys.platform != 'win32':
        raise SystemExit('Build this Windows EXE on Windows.')
    try:
        name, _ = build_identity(args.build_tag)
    except ValueError as exc:
        parser.error(str(exc))
    tagged_commit = subprocess.check_output(['git', 'rev-parse', '--verify', f'refs/tags/{args.build_tag}^{{commit}}'], cwd=ROOT, text=True).strip()
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if tagged_commit != head:
        raise SystemExit('Build tag does not point to the checked-out commit.')
    output = ROOT / 'dist' / (name + '.exe')
    if not (ROOT / 'web/dist/index.html').is_file():
        raise SystemExit('Run build_web_exe.bat to build the React assets first.')
    if args.with_translation:
        import importlib.util
        for name in ('torch', 'transformers', 'sentencepiece', 'sacremoses'):
            if importlib.util.find_spec(name) is None:
                raise SystemExit('Install requirements-translation.txt before using --with-translation.')
    WORK.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    version_file = WORK / 'version-info.txt'
    write_version_info(args.build_tag, version_file)
    print('Packaging web app. Progress is saved to build/web-exe/build.log.', flush=True)
    with (WORK / 'build.log').open('w', encoding='utf-8') as logfile:
        result = subprocess.run([sys.executable, '-m', 'PyInstaller', *pyinstaller_args(args.build_tag, version_file, args.with_translation)],
                                cwd=ROOT, stdout=logfile, stderr=subprocess.STDOUT)
    if result.returncode:
        raise SystemExit(f'PyInstaller failed. See {WORK / "build.log"}')
    print('Checking the actual EXE in an isolated data folder...', flush=True)
    with tempfile.TemporaryDirectory(prefix='rtc-web-build-') as folder:
        check = subprocess.run([str(output), '--self-test', '--data-dir', folder], timeout=180)
        report = Path(folder) / 'web-selftest.json'
        if report.exists():
            shutil.copy2(report, WORK / 'selftest.json')
        log = Path(folder) / 'caption-web.log'
        if log.exists():
            shutil.copy2(log, WORK / 'selftest.log')
        if check.returncode or not report.is_file() or not json.loads(report.read_text(encoding='utf-8'))['ok']:
            raise SystemExit(f'EXE verification failed. See {WORK / "selftest.json"} and selftest.log.')
    guide = (ROOT / 'packaging/WEB-EXE-HUONG-DAN.txt').read_text(encoding='utf-8')
    (output.parent / 'WEB-EXE-HUONG-DAN.txt').write_text(guide.replace('RealTimeCaptionWeb.exe', output.name), encoding='utf-8')
    print(f'Built and verified: {output} ({output.stat().st_size / 2**20:.1f} MB)', flush=True)


if __name__ == '__main__':
    main()

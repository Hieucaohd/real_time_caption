"""Run the local web server, or launch its packaged EXE with Chrome 9222."""
from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

PORT = 8765


def ensure(port=PORT):
    from caption import paths
    url = f'http://127.0.0.1:{port}/api/health'

    def ready():
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                data = json.load(response)
                return (data.get('app') == 'real-time-caption-web'
                        and Path(data.get('data_dir', '')).resolve() == paths.DATA_DIR)
        except (OSError, ValueError):
            return False

    if ready():
        return
    if not (paths.BUNDLE_DIR / 'web' / 'dist' / 'index.html').is_file():
        raise RuntimeError('React build is missing. Run setup_web.bat or rebuild the EXE.')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', port)) == 0:
            raise RuntimeError(f'Port {port} is used by another app or data folder. Use --port with a different port.')
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    command = ([sys.executable, '--serve'] if paths.FROZEN else
               [sys.executable, str(paths.BUNDLE_DIR / 'web_main.py'), '--serve'])
    env = os.environ.copy()
    env['RTC_DATA_DIR'] = str(paths.DATA_DIR)
    if paths.FROZEN:
        # The server outlives the launcher: give it its own onefile extraction.
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    with (paths.DATA_DIR / 'caption-web.log').open('a', encoding='utf-8') as logfile:
        process = subprocess.Popen([*command, '--port', str(port)], env=env,
            cwd=paths.DATA_DIR, stdin=subprocess.DEVNULL, stdout=logfile, stderr=logfile,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        if ready():
            print(f'Real-time Caption: http://127.0.0.1:{port}')
            return
        if process.poll() is not None:
            break
        time.sleep(.3)
    raise RuntimeError(f'Web server failed to start. See {paths.DATA_DIR / "caption-web.log"}.')


def open_chrome(port=PORT):
    """Reuse debugging Chrome when present; never reset an existing login/profile."""
    from caption import paths
    app_url = f'http://127.0.0.1:{port}'
    try:
        with urllib.request.urlopen('http://127.0.0.1:9222/json/list', timeout=3) as response:
            tabs = json.load(response)
    except (OSError, ValueError):
        tabs = None
    if tabs is not None:
        if not isinstance(tabs, list):
            raise RuntimeError('Port 9222 is not a Chrome debugging endpoint.')
        existing = next((t for t in tabs if t.get('type') == 'page' and t.get('url', '').rstrip('/') == app_url), None)
        endpoint = ('/json/activate/' + urllib.parse.quote(existing['id'], safe='') if existing
                    else '/json/new?' + urllib.parse.quote(app_url, safe=''))
        request = urllib.request.Request('http://127.0.0.1:9222' + endpoint, method='GET' if existing else 'PUT')
        with urllib.request.urlopen(request, timeout=5):
            pass
        return
    candidates = [Path(os.environ.get(key, '')) / 'Google/Chrome/Application/chrome.exe'
                  for key in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA') if os.environ.get(key)]
    chrome = next((p for p in candidates if p.is_file()), None)
    if not chrome:
        raise RuntimeError(f'Install Google Chrome, then reopen the app. Server URL: {app_url}')
    command = [str(chrome), '--remote-debugging-port=9222',
               '--user-data-dir=' + str(paths.DATA_DIR / 'chrome-profile'),
               '--no-first-run', '--no-default-browser-check', app_url, 'https://chatgpt.com']
    # External Chrome must not inherit the bundled DLL search directory.
    if paths.FROZEN and sys.platform == 'win32':
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        subprocess.Popen(command, cwd=paths.DATA_DIR, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    finally:
        if paths.FROZEN and sys.platform == 'win32':
            ctypes.windll.kernel32.SetDllDirectoryW(str(paths.BUNDLE_DIR))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--ensure', action='store_true')
    mode.add_argument('--serve', action='store_true')
    mode.add_argument('--open', action='store_true')
    mode.add_argument('--self-test', action='store_true')
    mode.add_argument('--overlay', metavar='URL')
    parser.add_argument('--overlay-parent', type=int)
    parser.add_argument('--port', type=int, default=PORT)
    parser.add_argument('--data-dir', type=Path)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('--port must be between 1 and 65535')
    if args.data_dir:
        os.environ['RTC_DATA_DIR'] = str(args.data_dir.resolve())
    from caption import paths
    paths.DATA_DIR.mkdir(parents=True, exist_ok=True)
    paths.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault('MPLCONFIGDIR', str(paths.CACHE_DIR / 'matplotlib'))
    logfile = paths.DATA_DIR / 'caption-web.log'
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s',
                        handlers=[logging.FileHandler(logfile, encoding='utf-8')])
    paths.install_default_prompts()
    if args.self_test:
        from web_api.selftest import run
        return run()
    if args.overlay:
        from web_api.overlay import main as overlay_main
        overlay_main(args.overlay, args.overlay_parent)
        return 0
    if args.ensure or args.open or (paths.FROZEN and not args.serve):
        ensure(args.port)
        if not args.ensure:
            open_chrome(args.port)
        return 0
    import uvicorn
    from web_api.server import create_app
    # Windowed PyInstaller has no console streams; use our file logger.
    uvicorn.run(create_app(), host='127.0.0.1', port=args.port, access_log=False,
                loop='asyncio', http='h11', ws='websockets-sansio', log_config=None)
    return 0


if __name__ == '__main__':
    multiprocessing.freeze_support()
    try:
        raise SystemExit(main())
    except Exception as exc:
        logging.exception('Web startup failed')
        if getattr(sys, 'frozen', False) and '--serve' not in sys.argv and '--self-test' not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), 'Real-time Caption Web', 0x10)
        elif sys.stderr:
            print(str(exc), file=sys.stderr)
        raise SystemExit(1)

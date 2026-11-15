"""Run/ensure the local FastAPI server used by launch_chrome.bat."""
from __future__ import annotations

import argparse
import json
import logging
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8765


def ensure(port=PORT):
    url = f'http://127.0.0.1:{port}/api/health'
    def ready():
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                data = json.load(response)
                return data.get('app') == 'real-time-caption-web' and Path(data.get('data_dir', '')).resolve() == ROOT
        except (OSError, ValueError):
            return False
    if ready():
        return
    import fastapi, uvicorn, multipart  # fail before spawning if setup was skipped
    if not (ROOT / 'web' / 'dist' / 'index.html').is_file():
        raise RuntimeError('React build is missing. Run setup_web.bat first.')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', port)) == 0:
            raise RuntimeError(f'Port {port} is used by another app. Stop that app first.')
    with (ROOT / 'caption-web.log').open('a', encoding='utf-8') as logfile:
        process = subprocess.Popen([sys.executable, str(ROOT / 'web_main.py'), '--port', str(port)],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=logfile, stderr=logfile,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if ready():
            print(f'Real-time Caption: http://127.0.0.1:{port}')
            return
        if process.poll() is not None:
            break
        time.sleep(.3)
    raise RuntimeError('Web server failed to start. See caption-web.log.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ensure', action='store_true')
    parser.add_argument('--port', type=int, default=PORT)
    args = parser.parse_args()
    if args.ensure:
        ensure(args.port)
        return
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s',
                        handlers=[logging.FileHandler(ROOT / 'caption-web.log', encoding='utf-8')])
    from caption.cuda_setup import register_nvidia_dlls
    register_nvidia_dlls()
    import uvicorn
    from web_api.server import create_app
    uvicorn.run(create_app(), host='127.0.0.1', port=args.port, access_log=False)


if __name__ == '__main__':
    main()

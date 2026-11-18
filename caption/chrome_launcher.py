"""Launch debugging Chrome through the shared ChatGPT tab-opening gate."""
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path


def open_browser(app_url=None):
    """Reuse debugging Chrome when present; never reset an existing login/profile."""
    from . import paths
    from .chatgpt_tab_gate import opening
    try:
        with urllib.request.urlopen('http://127.0.0.1:9222/json/list', timeout=3) as response:
            tabs = json.load(response)
    except (OSError, ValueError):
        tabs = None
    if tabs is not None:
        if app_url is None:
            from .chatgpt import open_new_chatgpt_tab
            open_new_chatgpt_tab()
            return
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
               '--no-first-run', '--no-default-browser-check', *([app_url] if app_url else []), 'https://chatgpt.com']
    # External Chrome must not inherit the bundled DLL search directory.
    if paths.FROZEN and sys.platform == 'win32':
        import ctypes
        ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        with opening():
            subprocess.Popen(command, cwd=paths.DATA_DIR, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    finally:
        if paths.FROZEN and sys.platform == 'win32':
            ctypes.windll.kernel32.SetDllDirectoryW(str(paths.BUNDLE_DIR))


import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from caption import paths
import web_main


class WebLauncherTests(unittest.TestCase):
    def test_explicit_data_folder_is_shared_and_independent_of_cwd(self):
        with tempfile.TemporaryDirectory() as folder:
            env = {**os.environ, 'RTC_DATA_DIR': folder}
            result = subprocess.run([sys.executable, '-c',
                'from caption.paths import DATA_DIR,CONVERSATIONS_DB; print(DATA_DIR); print(CONVERSATIONS_DB)'],
                env=env, capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.splitlines(), [str(Path(folder).resolve()), str(Path(folder).resolve() / 'conversations.sqlite3')])

    def test_frozen_ensure_spawns_server_with_independent_extraction(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'web/dist').mkdir(parents=True)
            (root / 'web/dist/index.html').write_text('built')
            health = io.BytesIO(json.dumps({'app':'real-time-caption-web', 'data_dir':str(root)}).encode())
            with patch.object(paths, 'FROZEN', True), patch.object(paths, 'DATA_DIR', root), \
                 patch.object(paths, 'BUNDLE_DIR', root), patch('web_main.urllib.request.urlopen', side_effect=[OSError(), health]), \
                 patch('web_main.socket.socket') as sock, patch('web_main.subprocess.Popen') as spawn:
                sock.return_value.__enter__.return_value.connect_ex.return_value = 1
                web_main.ensure(19876)
                command = spawn.call_args.args[0]
                self.assertEqual(command, [sys.executable, '--serve', '--port', '19876'])
                self.assertEqual(spawn.call_args.kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')
                self.assertEqual(spawn.call_args.kwargs['env']['RTC_DATA_DIR'], str(root))

    def test_ensure_never_reuses_server_with_different_database(self):
        health = io.BytesIO(json.dumps({'app':'real-time-caption-web', 'data_dir':'C:/different-data'}).encode())
        with patch('web_main.urllib.request.urlopen', return_value=health), \
             patch('web_main.socket.socket') as sock, patch('web_main.subprocess.Popen') as spawn:
            sock.return_value.__enter__.return_value.connect_ex.return_value = 0
            with self.assertRaisesRegex(RuntimeError, 'another app or data folder'):
                web_main.ensure()
            spawn.assert_not_called()

    def test_open_chrome_preserves_existing_tab_and_uses_put_for_new_tab(self):
        for existing in (True, False):
            with self.subTest(existing=existing):
                tabs = [{'id':'saved-tab', 'type':'page', 'url':'http://127.0.0.1:8765/'}] if existing else []
                with patch('web_main.urllib.request.urlopen', side_effect=[io.BytesIO(json.dumps(tabs).encode()), io.BytesIO(b'ok')]) as request, \
                     patch('web_main.subprocess.Popen') as spawn:
                    web_main.open_chrome()
                    call = request.call_args.args[0]
                    self.assertEqual(call.method, 'GET' if existing else 'PUT')
                    self.assertIn('/json/activate/saved-tab' if existing else '/json/new?', call.full_url)
                    spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()

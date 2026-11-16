"""Settings UI against real API/SQLite with Windows controls replaced by a fake."""
from contextlib import contextmanager
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from test_web_api import WebApiTests
from test_livecaptions_settings import FakeControls
from caption import livecaptions_settings as captions
from web_api.server import create_app


def main():
    fixture = WebApiTests(); fixture.setUp()
    controls = FakeControls()
    @contextmanager
    def connected(_):
        yield controls
    server = uvicorn.Server(uvicorn.Config(create_app(fixture.service), host='127.0.0.1', port=0, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline: raise RuntimeError('Server did not start')
        time.sleep(.05)
    try:
        with patch.object(captions, 'windows_controls', connected), sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width': 1400, 'height': 1000})
            errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
            page.route('**/api/chrome-tabs', lambda route: route.fulfill(json=[]))
            page.goto(f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}')
            page.get_by_role('button', name='Settings', exact=True).click()
            panel = page.get_by_role('region', name='Cài đặt Windows Live Captions')
            expect(panel.get_by_role('button', name='Lưu và áp dụng cho Windows')).to_be_enabled()
            assert controls.mic_calls == 0  # Opening settings does not manipulate Windows.
            panel.get_by_role('button', name='Tải lại ngôn ngữ và trạng thái từ Windows').click()
            expect(panel.get_by_role('checkbox')).to_be_checked()
            language = panel.get_by_role('combobox', name='Ngôn ngữ Windows Live Captions')
            language.select_option('Japanese (Japan)')
            panel.get_by_role('checkbox').uncheck()
            panel.get_by_role('button', name='Lưu và áp dụng cho Windows').click()
            expect(panel.get_by_role('status')).to_contain_text('Đã lưu và xác nhận')
            assert fixture.service.store.livecaptions_preferences() == {'microphone': False, 'language': 'Japanese (Japan)'}
            assert controls.language == 'Japanese (Japan)' and not controls.mic
            page.reload()
            page.get_by_role('button', name='Settings', exact=True).click()
            expect(language).to_have_value('Japanese (Japan)')
            expect(panel.get_by_role('checkbox')).not_to_be_checked()
            # Failed automation keeps a readable error and leaves persisted preferences intact.
            panel.get_by_role('checkbox').check()
            with patch.object(captions, 'apply_windows', side_effect=captions.CaptionsSettingsError('Windows chưa sẵn sàng')):
                panel.get_by_role('button', name='Lưu và áp dụng cho Windows').click()
                expect(panel.get_by_role('alert')).to_have_text('Windows chưa sẵn sàng')
            assert fixture.service.store.livecaptions_preferences()['microphone'] is False
            controls.ready = False
            panel.get_by_role('button', name='Lưu và áp dụng cho Windows').click()
            expect(panel.get_by_role('status')).to_contain_text('tải gói ngôn ngữ')
            expect(panel.get_by_role('status')).not_to_contain_text('xác nhận')
            page.evaluate("document.documentElement.dataset.theme = 'dark'")
            page.screenshot(path='cache/livecaptions-settings-dark.png')
            assert not errors, errors
            browser.close()
            print('Live Captions settings UI: scan, apply, SQLite reload, errors and setup-required state passed.')
    finally:
        server.should_exit = True; thread.join(timeout=10); fixture.tearDown()


if __name__ == '__main__':
    main()

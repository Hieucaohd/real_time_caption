"""Exercise source settings and transcript display against a real local API, without a mic."""
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from test_dictation_api import DictationApiTests
from test_web_api import FakeReader
from caption.transcriber import Event
from web_api.server import create_app


def main():
    fixture = DictationApiTests(); fixture.setUp()
    server = uvicorn.Server(uvicorn.Config(create_app(fixture.service), host='127.0.0.1', port=0, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise AssertionError('Server did not start')
        time.sleep(.05)
    try:
        with patch('web_api.service.chatgpt_dictation.ChatGPTDictationReader', side_effect=lambda emit, *a: FakeReader(emit)), sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width': 1500, 'height': 1000})
            errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}')
            source = page.get_by_role('combobox', name='Nguồn transcript', exact=True)
            source.select_option('chatgpt')
            page.get_by_role('button', name='Settings', exact=True).click()
            source_setting = page.get_by_role('combobox', name='Nguồn transcript trong Settings')
            expect(source_setting).to_have_value('chatgpt')
            page.get_by_role('spinbutton', name='Khoảng lấy văn bản (giây)').fill('20')
            page.get_by_role('checkbox', name='Luân phiên 2 tab để giảm khoảng trống').uncheck()
            page.get_by_role('button', name='Save settings', exact=True).click()
            expect(page.get_by_role('button', name='Close settings')).to_have_count(0)
            page.reload()
            expect(source).to_have_value('chatgpt')
            page.get_by_role('button', name='Settings', exact=True).click()
            expect(page.get_by_role('spinbutton', name='Khoảng lấy văn bản (giây)')).to_have_value('20')
            expect(page.get_by_role('checkbox', name='Luân phiên 2 tab để giảm khoảng trống')).not_to_be_checked()
            page.get_by_role('button', name='Close settings').click()
            page.get_by_role('button', name='Start / Continue', exact=True).click()
            expect(source).to_be_disabled()
            fixture.service.on_caption(Event('final', 'Microphone transcript test.', True))
            expect(page.locator('.transcript pre')).to_contain_text('Microphone transcript test.')
            page.get_by_role('button', name='Pause', exact=True).click()
            expect(source).to_be_enabled()
            source.select_option('windows')
            expect(source).to_have_value('windows')
            page.reload()
            expect(source).to_have_value('windows')
            expect(page.locator('.transcript pre')).to_contain_text('Microphone transcript test.')
            assert not errors, errors
            browser.close()
            print('Dictation UI passed: exclusive source, saved interval/mode, disabled while recording, transcript persistence, source switch.')
    finally:
        server.should_exit = True; thread.join(10); fixture.tearDown()


if __name__ == '__main__':
    main()

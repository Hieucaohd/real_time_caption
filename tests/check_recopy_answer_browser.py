"""Chat/node recopy controls against the real API and SQLite, with a fake provider."""
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from test_recopy_answer import RecopyApiTests, RESULT
from caption.settings import Settings
from web_api.server import create_app


def main():
    fixture = RecopyApiTests(); fixture.setUp()
    settings = Settings.load(); settings.chatgpt_selected_tab = 'saved'; settings.save()
    server = uvicorn.Server(uvicorn.Config(create_app(fixture.service), host='127.0.0.1', port=0, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline: raise AssertionError('Server did not start')
        time.sleep(.05)
    release = threading.Event()
    def copy(*args):
        release.wait(5)
        return RESULT
    try:
        with patch('web_api.recopy.recopy_response', side_effect=copy) as copied, patch('caption.chatgpt.ask') as ask, sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width': 1500, 'height': 1000})
            errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}')
            page.locator('nav button').nth(1).click()
            page.get_by_role('button', name='Lấy lại câu trả lời', exact=True).click()
            try:
                expect(page.get_by_role('button', name='Đang lấy lại…', exact=True)).to_be_disabled()
                expect(page.locator('.message.bot')).to_contain_text('Original answer')
            finally:
                release.set()
            expect(page.locator('.message.bot')).to_contain_text('Actual answer')
            expect(page.locator('.recopy-answer [role=status]')).to_contain_text('Đã lấy lại và lưu')
            copied.assert_called_once()
            page.locator('nav button').nth(2).click()
            page.locator(f'[data-node-id="{fixture.child["id"]}"]').click()
            expect(page.locator('.research-question h3')).to_have_text('Explain')
            page.get_by_role('button', name='Lấy lại câu trả lời', exact=True).click()
            expect(page.locator('.research-answer')).to_contain_text('Actual answer')
            expect(page.locator('.research-reader-head .research-status')).to_contain_text('Đã trả lời')
            assert copied.call_count == 2
            ask.assert_not_called()
            page.reload()
            page.locator('nav button').nth(2).click()
            expect(page.locator('.research-answer')).to_contain_text('Actual answer')
            copied.side_effect = lambda *args: {**RESULT, 'content': ''}
            page.get_by_role('button', name='Lấy lại câu trả lời', exact=True).click()
            expect(page.locator('.recopy-answer [role=alert]')).to_contain_text('vẫn rỗng')
            expect(page.locator('.research-answer')).to_contain_text('Actual answer')
            assert not errors, errors
            browser.close()
            print('Recopy UI: Chat and empty child, loading guard, database reload, preserved answer on empty copy, no resend passed.')
    finally:
        release.set(); server.should_exit = True; thread.join(10); fixture.tearDown()


if __name__ == '__main__':
    main()

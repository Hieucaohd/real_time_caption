"""First-run profile and Free/Paid controls in Chrome, using an isolated settings file."""
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import expect, sync_playwright

from caption.settings import Settings
from test_web_api import WebApiTests
from web_api.server import create_app


def main():
    fixture = WebApiTests()
    fixture.setUp()
    settings = Settings.load()
    settings.chatgpt_plan = settings.chatgpt_login_status = ''
    settings.chat_new_chat = True  # This check never needs a ChatGPT tab scan.
    settings.chat_screenshot = True
    settings.save()
    mid = fixture.service.store.add_message(fixture.cid, 'bot', 'ChatGPT', 'Parent answer')
    fixture.service.research.store.import_answer(fixture.cid, mid)
    server = uvicorn.Server(uvicorn.Config(create_app(fixture.service), host='127.0.0.1', port=0, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() >= deadline:
                raise RuntimeError('Test server did not start')
            time.sleep(.05)
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width':1440, 'height':900})
            page.route('**/api/chrome-tabs', lambda r: r.fulfill(json=[{'key':'tab', 'title':'Test tab'}]))
            page.goto(f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}')
            dialog = page.get_by_role('dialog', name='Thiết lập ChatGPT')
            button = dialog.get_by_role('button', name='Lưu và tiếp tục')
            expect(dialog).to_be_visible()
            expect(button).to_be_disabled()
            dialog.get_by_label('Gói ChatGPT').select_option('free')
            expect(dialog.get_by_text('Với gói Free', exact=False)).to_be_visible()
            expect(button).to_be_disabled()
            dialog.get_by_label('Đã đăng nhập ChatGPT trên Chrome của app?').select_option('signed_out')
            button.click()
            expect(dialog).to_have_count(0)
            page.reload()
            page.get_by_role('button', name='Chat', exact=True).click()
            expect(dialog).to_have_count(0)
            for name in ('Attach files', 'Screenshot'):
                expect(page.get_by_role('button', name=name, exact=True)).to_be_disabled()
            composer = page.locator('.chat-composer textarea')
            composer.evaluate('''el => {
                const data = new DataTransfer();
                data.items.add(new File(['image'], 'clipboard.png', {type: 'image/png'}));
                el.dispatchEvent(new ClipboardEvent('paste', {bubbles: true, clipboardData: data}));
            }''')
            sent = []
            def send(route):
                sent.append(route.request.post_data_json)
                route.fulfill(json={'id': 'test', 'status': 'queued'})
            page.route('**/api/conversations/*/jobs', send)
            composer.fill('Text still works on Free')
            page.get_by_role('button', name='Send', exact=True).click()
            expect(composer).to_have_value('')
            assert sent[0]['files'] == []
            page.locator('.chat-settings summary').click()
            expect(page.get_by_role('checkbox', name='Auto screenshot', exact=True)).to_be_disabled()
            page.get_by_role('button', name='Chat realtime', exact=True).click()
            expect(page.get_by_role('button', name='Attach files', exact=True)).to_be_disabled()
            expect(page.get_by_role('button', name='Screenshot', exact=True)).to_be_disabled()
            page.get_by_role('button', name='Nghiên cứu theo nhánh', exact=True).click()
            page.get_by_role('button', name='Tạo nhánh con', exact=True).click()
            branch = page.get_by_role('dialog', name='Tạo nhánh con')
            expect(branch.get_by_role('button', name='Attach files', exact=True)).to_be_disabled()
            expect(branch.get_by_role('checkbox', name='Auto screenshot', exact=True)).to_be_disabled()
            branch.get_by_role('button', name='Đóng tạo nhánh').click()

            page.get_by_role('button', name='Settings', exact=True).click()
            expect(page.get_by_label('Gói ChatGPT')).to_have_value('free')
            expect(page.get_by_label('Đã đăng nhập ChatGPT trên Chrome của app?')).to_have_value('signed_out')
            page.get_by_label('Gói ChatGPT').select_option('pro')
            page.get_by_label('Đã đăng nhập ChatGPT trên Chrome của app?').select_option('signed_in')
            page.get_by_role('button', name='Save settings', exact=True).click()
            expect(page.get_by_label('Gói ChatGPT')).to_have_count(0)
            page.get_by_role('button', name='Chat', exact=True).click()
            expect(page.get_by_role('button', name='Attach files', exact=True)).to_be_enabled()
            expect(page.get_by_role('button', name='Screenshot', exact=True)).to_be_enabled()
            assert Settings.load().chatgpt_plan == 'pro'
            assert Settings.load().chatgpt_login_status == 'signed_in'
            browser.close()
        print('ChatGPT profile browser checks passed: setup, reload, Free controls, Settings, Pro controls.')
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        fixture.tearDown()


if __name__ == '__main__':
    main()

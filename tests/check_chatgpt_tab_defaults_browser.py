"""Automatic first-tab selection/new-tab fallback in Chat and child composer."""
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from test_web_api import WebApiTests
from web_api.server import create_app
from caption.settings import Settings


def main():
    fixture = WebApiTests(); fixture.setUp()
    fixture.service.busy = True
    settings = Settings.load(); settings.chatgpt_selected_tab = 'second'; settings.save()
    mid = fixture.service.store.add_message(fixture.cid, 'bot', 'ChatGPT', 'A parent answer.')
    store = fixture.service.research.store
    store.import_answer(fixture.cid, mid)
    store.save_defaults({'selected_tab': 'deleted-tab'})
    server = uvicorn.Server(uvicorn.Config(create_app(fixture.service), host='127.0.0.1', port=0, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline: raise RuntimeError('Server did not start')
        time.sleep(.05)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width':1440, 'height':1000})
            errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
            available = [{'key':'first', 'title':'First ChatGPT'}, {'key':'second', 'title':'Second ChatGPT'}]
            opened = []; failure = False
            def tabs(route):
                if route.request.method == 'GET':
                    route.fulfill(json=available)
                else:
                    if failure:
                        route.fulfill(status=400, json={'detail':'Cannot open Chrome tab'})
                    else:
                        opened.append(route.request.headers.get('x-rtc-tab'))
                        route.fulfill(json={'key':f'created-{len(opened)}', 'title':'New ChatGPT'})
            page.route('**/api/chrome-tabs', tabs)
            page.goto(f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}')
            page.locator('nav button').nth(1).click()
            page.locator('.chat-settings summary').click()
            chat = page.get_by_label('chat ChatGPT tab', exact=True)
            expect(chat).to_have_value('first')
            assert Settings.load().chatgpt_selected_tab == 'first'
            assert not opened
            chat.select_option('second')
            page.wait_for_timeout(1200)  # Bootstrap polling must not override an intentional selection.
            expect(chat).to_have_value('second')
            page.get_by_role('button', name='Refresh Chrome tabs', exact=True).click()
            expect(chat).to_have_value('first')
            available.clear()
            page.get_by_role('button', name='Refresh Chrome tabs', exact=True).click()
            expect(chat).to_have_value('created-1')
            assert opened == [page.locator('html').get_attribute('data-rtc-tab-id')]
            page.wait_for_timeout(1200)
            assert len(opened) == 1  # No automatic rescan loop when Chrome's list lags.
            # Empty list in a new child composer creates and selects exactly one tab.
            page.locator('nav button').nth(2).click()
            page.get_by_role('button', name='Tạo nhánh con', exact=True).click()
            dialog = page.locator('.branch-composer')
            child = dialog.get_by_label('research ChatGPT tab', exact=True)
            expect(child).to_have_value('created-2')
            assert len(opened) == 2
            dialog.get_by_role('button', name='Đóng tạo nhánh', exact=True).click()
            available.extend([{'key':'first', 'title':'First ChatGPT'}, {'key':'second', 'title':'Second ChatGPT'}])
            page.get_by_role('button', name='Tạo nhánh con', exact=True).click()
            expect(child).to_have_value('first')
            assert len(opened) == 2
            dialog.get_by_role('button', name='Đóng tạo nhánh', exact=True).click()
            available.clear(); failure = True
            page.get_by_role('button', name='Tạo nhánh con', exact=True).click()
            expect(dialog.get_by_role('alert')).to_have_text('Cannot open Chrome tab')
            dialog.get_by_label('Câu hỏi nhánh', exact=True).fill('Keep this question')
            expect(dialog.get_by_role('button', name='Xếp hàng 1 nhánh', exact=True)).to_be_disabled()
            failure = False
            dialog.get_by_role('button', name='Refresh Chrome tabs', exact=True).click()
            expect(child).to_have_value('created-3')
            expect(dialog.get_by_label('Câu hỏi nhánh', exact=True)).to_have_value('Keep this question')
            assert len(opened) == 3
            assert not errors, errors
            browser.close()
            print('Automatic ChatGPT tabs: first tab, manual choice, refresh, empty-list creation, no duplicates, child defaults and retry passed.')
    finally:
        server.should_exit = True; thread.join(timeout=10); fixture.tearDown()


if __name__ == '__main__':
    main()

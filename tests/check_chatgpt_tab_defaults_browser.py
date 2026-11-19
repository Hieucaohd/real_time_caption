"""Check saved Chrome tabs on Chat entry and recover missing selections."""
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
    settings = Settings.load(); settings.chatgpt_selected_tab = 'second'
    settings.chatgpt_selected_tab_title = 'Second ChatGPT'; settings.save()
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
            opened = []; scans = []; sent = []; failure = False
            def tabs(route):
                if route.request.method == 'GET':
                    scans.append(True)
                    route.fulfill(json=available)
                else:
                    if failure:
                        route.fulfill(status=400, json={'detail':'Cannot open Chrome tab'})
                    else:
                        opened.append(route.request.headers.get('x-rtc-tab'))
                        route.fulfill(json={'key':f'created-{len(opened)}', 'title':'New ChatGPT'})
            page.route('**/api/chrome-tabs', tabs)
            def send(route):
                sent.append(route.request.post_data_json)
                route.fulfill(json={'id': 'mock-job', 'status': 'queued'})
            page.route('**/api/conversations/*/jobs', send)
            page.goto(f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}')
            page.locator('nav button').nth(1).click()
            page.locator('.chat-settings summary').click()
            chat = page.get_by_label('chat ChatGPT tab', exact=True)
            expect(chat).to_have_value('second')
            expect(chat.locator('option[value="first"]')).to_have_count(1)
            assert Settings.load().chatgpt_selected_tab == 'second'
            assert len(scans) == 1
            assert not opened
            page.get_by_role('button', name='Refresh Chrome tabs', exact=True).click()
            expect(chat).to_be_enabled()
            expect(chat).to_have_value('second')
            assert len(scans) == 2
            available.pop()
            page.locator('nav button').nth(0).click()
            page.locator('nav button').nth(1).click()
            page.locator('.chat-settings summary').click()
            expect(chat).to_have_value('first')
            warning = page.get_by_role('status').filter(has_text='không còn mở')
            expect(warning).to_contain_text('“Second ChatGPT” không còn mở. Đã chọn tab đầu tiên trong danh sách: “First ChatGPT”.')
            assert len(scans) == 3 and not opened and not sent
            page.get_by_label('chat Power', exact=True).select_option('2')
            page.get_by_label('chat transcript lines', exact=True).fill('42')
            page.get_by_role('checkbox', name='Đính kèm transcript', exact=True).uncheck()
            page.get_by_role('checkbox', name='Đính kèm lịch sử chat', exact=True).check()
            page.get_by_role('checkbox', name='Auto screenshot', exact=True).check()
            page.get_by_role('checkbox', name='New ChatGPT chat', exact=True).check()
            page.wait_for_timeout(1200)  # Bootstrap polling must not override an intentional selection.
            expect(chat).to_have_value('first')
            assert Settings.load().chatgpt_selected_tab == 'second'  # Still a draft.
            page.locator('nav button').nth(0).click()
            page.locator('nav button').nth(1).click()
            page.locator('.chat-settings summary').click()
            expect(chat).to_have_value('first')
            assert len(scans) == 3  # New-chat mode does not use a saved tab.
            # Sending uses all current draft options, without silently saving them.
            page.locator('.chat-composer textarea').fill('Use these draft options')
            page.get_by_role('button', name='Send', exact=True).click()
            page.wait_for_timeout(200)
            assert sent[-1]['selected_tab'] == ''
            assert sent[-1]['power'] == 2 and sent[-1]['max_lines'] == 42
            assert sent[-1]['attach_transcript'] is False
            assert sent[-1]['attach_history'] and sent[-1]['screenshot'] and sent[-1]['new_chat']
            assert Settings.load().chatgpt_selected_tab == 'second'
            page.get_by_role('button', name='Lưu cài đặt Chat', exact=True).click()
            expect(page.locator('.toast')).to_contain_text('Đã lưu cài đặt Chat')
            saved = Settings.load()
            assert saved.chatgpt_selected_tab == 'first' and saved.chatgpt_selected_tab_title == 'First ChatGPT'
            assert saved.chat_chatgpt_power == 2 and saved.chat_max_lines == 42
            assert saved.chat_attach_transcript is False and saved.chat_attach_history is True
            assert saved.chat_screenshot is True and saved.chat_new_chat is True
            assert saved.chat_prompt
            page.reload()
            page.locator('nav button').nth(1).click()
            page.locator('.chat-settings summary').click()
            expect(chat).to_have_value('first')
            expect(chat.locator('option:checked')).to_have_text('First ChatGPT')
            expect(page.get_by_label('chat Power', exact=True)).to_have_value('2')
            expect(page.get_by_label('chat transcript lines', exact=True)).to_have_value('42')
            expect(page.get_by_role('checkbox', name='Đính kèm transcript', exact=True)).not_to_be_checked()
            expect(page.get_by_role('checkbox', name='New ChatGPT chat', exact=True)).to_be_checked()
            assert len(scans) == 3
            # A new local conversation also keeps the saved ChatGPT tab.
            page.get_by_role('button', name='New conversation', exact=True).click()
            naming = page.get_by_role('dialog', name='Tạo cuộc hội thoại mới', exact=True)
            naming.get_by_label('Tên cuộc hội thoại', exact=True).fill('Another conversation')
            naming.get_by_role('button', name='Tạo cuộc hội thoại', exact=True).click()
            expect(naming).to_have_count(0)
            page.wait_for_timeout(300)
            expect(chat).to_have_value('first')
            assert len(scans) == 3
            # Return to the original conversation, which has a research root.
            page.get_by_role('button', name='Shared lesson', exact=False).click()
            available.clear()
            page.get_by_role('checkbox', name='New ChatGPT chat', exact=True).uncheck()
            expect(chat).to_have_value('created-1')
            expect(warning).to_contain_text('“First ChatGPT” không còn mở. Đã mở và chọn tab ChatGPT mới “New ChatGPT”.')
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
            dialog.get_by_role('button', name='Đóng tạo nhánh', exact=True).click()
            # A first-time Chat with no saved tab still discovers/creates once.
            defaults = Settings.load(); defaults.chatgpt_selected_tab = ''; defaults.chat_new_chat = False; defaults.save()
            page.reload()
            page.locator('nav button').nth(1).click()
            page.locator('.chat-settings summary').click()
            expect(chat).to_have_value('created-4')
            scan_count = len(scans)
            available.append({'key': 'created-4', 'title': 'New ChatGPT'})
            page.locator('nav button').nth(0).click()
            page.locator('nav button').nth(1).click()
            page.locator('.chat-settings summary').click()
            expect(chat).to_have_value('created-4')
            expect(chat).to_be_enabled()
            assert len(scans) == scan_count + 1 and len(opened) == 4
            page.locator('nav button').nth(1).click()
            expect(chat).to_be_enabled()
            assert len(scans) == scan_count + 2 and len(opened) == 4
            assert not errors, errors
            browser.close()
            print('Chat tab validation, fallback, draft save, new-chat mode, reload and child defaults passed.')
    finally:
        server.should_exit = True; thread.join(timeout=10); fixture.tearDown()


if __name__ == '__main__':
    main()

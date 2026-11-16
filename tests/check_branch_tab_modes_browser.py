"""Automatic tab modes in real Chrome, with temporary SQLite and mocked Chrome actions."""
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from test_web_api import WebApiTests
from web_api.server import create_app


def main():
    fixture = WebApiTests(); fixture.setUp()
    fixture.service.busy = True  # Never send a real ChatGPT question.
    store = fixture.service.research.store
    fixture.service.store.add_message(fixture.cid, 'user', 'You', 'Parent question')
    mid = fixture.service.store.add_message(fixture.cid, 'bot', 'ChatGPT', 'Parent answer',
        response_conversation_url='https://chatgpt.com/c/parent-source', response_position=4)
    root = store.import_answer(fixture.cid, mid)
    server = uvicorn.Server(uvicorn.Config(create_app(fixture.service), host='127.0.0.1', port=0, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline: raise RuntimeError('Test server did not start')
        time.sleep(.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width':1440, 'height':1000})
            errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
            calls = []; caller_ids = []; failed_branch = False

            def chrome_tabs(route):
                if route.request.method == 'GET':
                    # Deliberately stale scan: the returned tab must still appear and be selected.
                    route.fulfill(json=[{'key':'old-tab', 'title':'Existing tab'}])
                else:
                    caller_ids.append(route.request.headers.get('x-rtc-tab'))
                    calls.append('new')
                    route.fulfill(json={'key':f'new-{len(calls)}', 'title':'New conversation'})

            def branch_tab(route):
                nonlocal failed_branch
                caller_ids.append(route.request.headers.get('x-rtc-tab'))
                assert f'/{root["id"]}/chatgpt-branch' in route.request.url
                calls.append('branch')
                if not failed_branch:
                    failed_branch = True
                    route.fulfill(status=400, json={'detail':'Branch temporarily unavailable'})
                else:
                    route.fulfill(json={'key':f'branch-{len(calls)}', 'title':'Branch from parent'})

            page.route('**/api/chrome-tabs', chrome_tabs)
            page.route('**/api/conversations/*/research/*/chatgpt-branch', branch_tab)
            page.goto(f'http://127.0.0.1:{port}')
            page.locator('nav button').nth(2).click()
            page.get_by_role('button', name='Tạo nhánh con', exact=True).click()
            dialog = page.locator('.branch-composer')
            new = dialog.get_by_label('Tạo cuộc hội thoại ChatGPT mới', exact=True)
            branch = dialog.get_by_label('Tạo nhánh từ cuộc hội thoại của node cha', exact=True)
            tabs = dialog.get_by_label('research ChatGPT tab', exact=True)
            question = dialog.get_by_label('Câu hỏi nhánh', exact=True)
            save = dialog.get_by_role('button', name='Lưu cài đặt', exact=True)
            close = dialog.get_by_role('button', name='Đóng tạo nhánh', exact=True)
            send = dialog.get_by_role('button', name='Xếp hàng 1 nhánh', exact=True)
            question.fill('Keep draft')
            new.check()
            expect(tabs).to_be_disabled()
            expect(tabs).to_have_value('new-1')
            assert caller_ids[-1] == page.locator('html').get_attribute('data-rtc-tab-id')
            expect(branch).not_to_be_checked()
            save.click()
            expect(dialog).to_contain_text('Đã lưu vào database')
            assert store.defaults()['tab_mode'] == 'new'
            close.click()
            page.reload()
            page.locator('nav button').nth(2).click()
            page.get_by_role('button', name='Tạo nhánh con', exact=True).click()
            expect(new).to_be_checked()
            expect(tabs).to_be_disabled()
            expect(tabs).to_have_value('new-2')
            question.fill('Keep draft and attachment')
            dialog.locator('input[type=file]').set_input_files({
                'name':'context.txt', 'mimeType':'text/plain', 'buffer':b'keep attachment'})
            expect(dialog.locator('.composer-files')).to_contain_text('context.txt')
            branch.check()
            expect(new).not_to_be_checked()
            expect(branch).to_be_checked()
            expect(tabs).to_be_disabled()
            expect(dialog.get_by_role('alert')).to_contain_text('Branch temporarily unavailable')
            expect(send).to_be_disabled()
            expect(tabs).to_have_value('')
            dialog.get_by_role('button', name='Thử tạo tab lại', exact=True).click()
            expect(tabs).to_have_value('branch-4')
            expect(question).to_have_value('Keep draft and attachment')
            expect(dialog.locator('.composer-files')).to_contain_text('context.txt')
            save.click()
            expect(dialog).to_contain_text('Đã lưu vào database')
            assert store.defaults()['tab_mode'] == 'branch'
            close.click()
            page.reload()
            page.locator('nav button').nth(2).click()
            page.get_by_role('button', name='Tạo nhánh con', exact=True).click()
            expect(branch).to_be_checked()
            expect(tabs).to_have_value('branch-5')
            assert caller_ids[-1] == page.locator('html').get_attribute('data-rtc-tab-id')
            question.fill('Queued into the prepared branch')
            dialog.get_by_label('research Power', exact=True).select_option('2')
            with page.expect_response('**/research/*/children') as queued:
                send.click()
            # A queue validation error must not create another external tab on retry.
            assert queued.value.status == 400, queued.value.text()
            expect(dialog.get_by_role('alert')).to_contain_text('chưa có transcript')
            expect(tabs).to_have_value('branch-5')
            dialog.get_by_label('Đính kèm transcript', exact=True).uncheck()
            with page.expect_response('**/research/*/children') as queued:
                send.click()
            assert queued.value.status == 200, queued.value.text()
            expect(dialog).to_have_count(0)
            children = [node for node in store.list(fixture.cid) if node['parent_id']]
            assert len(children) == 1
            child = store.get(fixture.cid, children[0]['id'])
            assert child['config']['selected_tab'] == 'branch-5'
            assert child['config']['tab_mode'] == 'branch'
            assert child['config']['new_chat'] is False
            assert calls == ['new', 'new', 'branch', 'branch', 'branch'], calls
            # A saved branch mode cannot accidentally send from a source-less parent.
            store.update(fixture.cid, child['id'], status='done', answer='Child without source')
            page.get_by_label('Tìm node', exact=True).fill('Queued into')
            page.locator('.research-search-results button').first.click()
            page.get_by_label('Tìm node', exact=True).fill('')
            page.get_by_role('button', name='Tạo nhánh con', exact=True).click()
            expect(branch).to_be_checked()
            expect(dialog.get_by_role('alert')).to_contain_text('Node cha chưa có link')
            question.fill('Needs a target tab')
            expect(send).to_be_disabled()
            assert len(calls) == 5
            new.check()
            expect(branch).not_to_be_checked()
            expect(tabs).to_have_value('new-6')
            new.uncheck()
            expect(tabs).to_be_enabled()
            tabs.select_option('old-tab')
            save.click()
            expect(dialog).to_contain_text('Đã lưu vào database')
            assert store.defaults()['tab_mode'] == 'existing'
            close.click()
            page.get_by_role('button', name='Tạo nhánh con', exact=True).click()
            expect(new).not_to_be_checked()
            expect(branch).not_to_be_checked()
            expect(tabs).to_be_enabled()
            expect(tabs).to_have_value('old-tab')
            assert len(calls) == 6
            assert all(caller_ids) and len(set(caller_ids)) == 3, caller_ids
            assert not errors, errors
            browser.close()
            print('Tab mode browser checks passed: exclusivity, automatic creation, locked dropdown, SQLite reload, stale scan, failure/retry, source validation, draft preservation, correct queued tab.')
    finally:
        server.should_exit = True; thread.join(timeout=10)
        fixture.tearDown()


if __name__ == '__main__':
    main()

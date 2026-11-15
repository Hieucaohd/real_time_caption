"""Real Chrome composer checks against temporary data; never sends to ChatGPT."""
import json
import re
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
    settings = Settings.load(); settings.chatgpt_selected_tab = 'existing'; settings.chat_chatgpt_power = 2; settings.save()
    store = fixture.service.research.store
    fixture.service.store.add_message(fixture.cid, 'user', 'You', 'Question')
    mid = fixture.service.store.add_message(fixture.cid, 'bot', 'ChatGPT', 'An explanation of limits.')
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
            tabs = [{'key':'existing', 'title':'Existing conversation'}]
            page.route('**/api/chrome-tabs', lambda route: route.fulfill(json=tabs))
            page.goto(f'http://127.0.0.1:{port}')
            page.locator('nav button').nth(1).click()
            page.locator('.chat-settings > summary').click()
            chat_prompts = page.get_by_label('chat Prompt', exact=True).locator('option').all_text_contents()
            expect(page.get_by_label('chat Power', exact=True)).to_have_value('2')
            page.get_by_label('Đính kèm transcript', exact=True).uncheck()
            page.locator('nav button').nth(2).click()
            tabs[0]['title'] = 'Updated conversation title'
            page.get_by_role('button', name='Tạo nhánh con', exact=True).click()
            dialog = page.locator('.branch-composer')
            expect(page.get_by_label('research ChatGPT tab', exact=True).locator('option:checked')).to_have_text('Updated conversation title')
            expect(dialog.get_by_label('New ChatGPT chat', exact=True)).not_to_be_checked()
            expect(page.get_by_label('research Power', exact=True)).to_have_value('2')
            assert page.get_by_label('research Prompt', exact=True).locator('option').all_text_contents() == chat_prompts
            assert not page.get_by_text('Yêu cầu cách giải thích', exact=True).count()
            question = page.get_by_label('Câu hỏi nhánh', exact=True)
            question.fill('Keep this draft')
            tabs.append({'key':'new-tab', 'title':'Newly opened conversation'})
            dialog.get_by_role('button', name='Refresh Chrome tabs', exact=True).click()
            expect(page.get_by_label('research ChatGPT tab', exact=True).locator('option')).to_have_count(3)
            expect(question).to_have_value('Keep this draft')
            page.get_by_label('research ChatGPT tab', exact=True).select_option('new-tab')

            # The same prompt editor creates a template visible in both tabs.
            dialog.get_by_role('button', name='Edit prompt', exact=True).click()
            editor = page.get_by_role('dialog', name='Edit prompt', exact=True)
            expect(editor.locator('.prompt-editor')).to_have_value('Question: {message}\n')
            editor.locator('input').fill('Composer test')
            editor.locator('.prompt-editor').fill('Shared template: {message}')
            editor.get_by_role('button', name='Save new version', exact=True).click()
            expect(editor).to_have_count(0)
            expect(page.get_by_label('research Prompt', exact=True)).to_have_value('v2 - Composer test')
            expect(question).to_have_value('Keep this draft')

            # Clipboard image is uploaded immediately, then frozen in SQLite when queued.
            question.evaluate('''el => {
              const canvas = document.createElement('canvas'); canvas.width = canvas.height = 8;
              const data = atob(canvas.toDataURL('image/png').split(',')[1]);
              const file = new File([Uint8Array.from(data, c => c.charCodeAt(0))], 'pasted.png', {type:'image/png'});
              const transfer = new DataTransfer(); transfer.items.add(file);
              el.dispatchEvent(new ClipboardEvent('paste', {bubbles:true, clipboardData:transfer}));
            }''')
            expect(dialog.locator('.composer-files')).to_contain_text('pasted.png')
            # Exercise screenshot selection without displaying the OS picker.
            page.evaluate('''() => {
              navigator.mediaDevices.getDisplayMedia = async () => {
                const canvas = document.createElement('canvas'); canvas.width = canvas.height = 24;
                const stream = canvas.captureStream(5);
                setTimeout(() => canvas.getContext('2d').fillRect(0,0,24,24), 50);
                return stream;
              };
            }''')
            dialog.get_by_role('button', name='Screenshot', exact=True).click()
            expect(dialog.locator('.composer-files')).to_contain_text('screen.png')
            dialog.get_by_role('button', name=re.compile(r'^Bỏ .*screen\.png$')).click()
            expect(dialog.locator('.composer-files')).not_to_contain_text('screen.png')
            # Saving preferences keeps the question/files intact and never sends a branch.
            page.route('**/api/research/defaults', lambda route: route.fulfill(status=500, json={'detail':'Save failed'}))
            dialog.get_by_role('button', name='Lưu cài đặt', exact=True).click()
            expect(dialog.get_by_role('alert')).to_contain_text('Save failed')
            assert store.defaults() == {}
            page.unroute('**/api/research/defaults')
            dialog.get_by_role('button', name='Lưu cài đặt', exact=True).click()
            expect(dialog.get_by_role('status')).to_contain_text('Đã lưu vào database')
            expect(question).to_have_value('Keep this draft')
            expect(dialog.locator('.composer-files')).to_contain_text('pasted.png')
            assert len(store.list(fixture.cid)) == 1
            saved = store.defaults()
            assert saved['selected_tab'] == 'new-tab'
            assert saved['prompt'] == 'v2 - Composer test'
            assert 'question' not in saved and 'files' not in saved
            question.fill('Explain limits')
            question.press('Shift+Enter'); question.type('With examples')
            expect(question).to_have_value('Explain limits\nWith examples')
            page.screenshot(path='cache/branch-composer.png')
            question.press('Enter')
            expect(dialog).to_have_count(0)
            nodes = [n for n in store.list(fixture.cid) if n['parent_id']]
            assert len(nodes) == 1
            node = store.get(fixture.cid, nodes[0]['id'], private=True)
            assert node['request']['prompt'] == 'Shared template: Explain limits\nWith examples'
            assert node['config']['new_chat'] is False
            assert node['config']['selected_tab'] == 'new-tab'
            assert node['config']['power'] == 2
            assert len(node['request']['attachments']) == 1
            with fixture.service.store._connect() as db:
                assert db.execute('SELECT count(*) FROM research_node_assets WHERE node_id=?', (node['id'],)).fetchone()[0] == 1
            page.locator('nav button').nth(1).click()
            expect(page.get_by_label('chat Prompt', exact=True).locator('option')).to_have_count(2)
            expect(page.get_by_label('chat ChatGPT tab', exact=True)).to_have_value('existing')
            # Batch mode leaves Enter for a new question instead of submitting.
            page.locator('nav button').nth(2).click()
            page.locator(f'[data-node-id="{root["id"]}"] .research-node').click()
            page.get_by_role('button', name='Tạo nhánh con', exact=True).click()
            dialog.get_by_label('Tạo nhiều nhánh: mỗi dòng là một câu hỏi', exact=True).check()
            question.fill('First'); question.press('Enter'); question.type('Second')
            expect(question).to_have_value('First\nSecond')
            expect(dialog.get_by_label('New ChatGPT chat', exact=True)).not_to_be_checked()
            dialog.get_by_role('button', name='Xếp hàng 2 nhánh', exact=True).click()
            expect(dialog).to_have_count(0)
            assert len(store.list(fixture.cid)) == 4
            # A full reload restores DB defaults, without carrying over the old draft/assets.
            page.reload()
            page.locator('nav button').nth(2).click()
            page.locator(f'[data-node-id="{root["id"]}"] .research-node').click()
            page.get_by_role('button', name='Tạo nhánh con', exact=True).click()
            expect(page.get_by_label('research ChatGPT tab', exact=True)).to_have_value('new-tab')
            expect(page.get_by_label('research Prompt', exact=True)).to_have_value('v2 - Composer test')
            expect(question).to_have_value('')
            expect(dialog.locator('.composer-files')).to_be_empty()
            page.get_by_label('research Power', exact=True).select_option('0')
            dialog.get_by_role('button', name='Đóng tạo nhánh', exact=True).click()
            tabs.append({'key':'another-tab', 'title':'Opened since last branch'})
            page.get_by_role('button', name='Tạo nhánh con', exact=True).click()
            expect(page.get_by_label('research ChatGPT tab', exact=True).locator('option')).to_have_count(4)
            expect(page.get_by_label('research ChatGPT tab', exact=True)).to_have_value('new-tab')
            expect(page.get_by_label('research Power', exact=True)).to_have_value('2')
            assert not errors, errors
            print('Composer browser checks passed: refresh, shared prompts/editor, paste, screenshot, Enter, batch, SQLite defaults, save failure/retry, reload.')
            browser.close()
    finally:
        server.should_exit = True; thread.join(timeout=10)
        fixture.tearDown()


if __name__ == '__main__':
    main()

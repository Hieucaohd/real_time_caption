"""Chrome integration smoke test using an isolated DB and mocked ChatGPT only."""
from io import BytesIO
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from PIL import Image
from playwright.sync_api import sync_playwright, expect
from test_realtime_chat import RealtimeChatTests, tab
from web_api.server import create_app


def main():
    fixture = RealtimeChatTests(); fixture.setUp()
    fixture.rt.store.save(fixture.cid, {**fixture.config, 'selected_tab':'', 'summary_tab':''})
    server = uvicorn.Server(uvicorn.Config(create_app(fixture.service), host='127.0.0.1', port=0, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError('Test server did not start')
        time.sleep(.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        with patch('web_api.realtime_chat.chatgpt.open_new_chatgpt_tab', side_effect=[tab('actions-tab'), tab('summary-tab')]) as opened, sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width':1440, 'height':1000})
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{port}')
            page.get_by_role('button', name='Chat realtime', exact=True).click()
            panel = page.locator('.realtime-chat')
            panel.locator('summary').click()
            expect(page.get_by_label('realtime Prompt', exact=True)).to_be_visible()
            for kind, label, text in [('actions', 'Gợi ý hành động', 'BROWSER ACTIONS: return "actions" JSON'),
                                      ('summary', 'Tóm tắt', 'BROWSER SUMMARY: summarize decisions')]:
                page.get_by_role('button', name='Sửa prompt ' + label, exact=True).click()
                dialog = page.get_by_role('dialog', name='Sửa prompt ' + label, exact=True)
                expect(dialog).to_be_visible()
                dialog.get_by_label('Tên mẫu realtime', exact=True).fill('Meeting ' + kind)
                dialog.get_by_label('Nội dung prompt realtime', exact=True).fill(text)
                dialog.get_by_role('button', name='Lưu thành mẫu mới', exact=True).click()
                expect(dialog).to_have_count(0)
                expect(page.get_by_label('Prompt ' + label, exact=True)).to_have_value('Meeting ' + kind)
            page.get_by_label('Chu kỳ Chat realtime', exact=True).fill('5')
            page.get_by_label('realtime Power', exact=True).select_option('2')
            page.get_by_label('realtime Summary Power', exact=True).select_option('0')
            expect(page.get_by_label('realtime ChatGPT tab', exact=True)).to_have_count(0)
            expect(page.get_by_label('Tab tóm tắt realtime', exact=True)).to_have_count(0)
            expect(page.get_by_label('realtime transcript lines', exact=True)).to_have_count(0)
            for label in ('Đính kèm transcript', 'Đính kèm lịch sử chat', 'New ChatGPT chat'):
                expect(panel.get_by_label(label, exact=True)).to_have_count(0)
            panel.get_by_role('button', name='Lưu cài đặt', exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('Đã lưu')
            panel.locator('summary').click()
            panel.get_by_role('button', name='Start', exact=True).click()
            expect(panel.get_by_role('button', name='Explain', exact=True)).to_be_visible(timeout=10000)
            expect(panel.locator('.rt-summaries')).to_contain_text('Summary of new content')
            assert opened.call_count == 2
            panel.locator('summary').click()
            expect(page.get_by_label('realtime Power', exact=True)).to_be_disabled()
            expect(page.get_by_label('realtime Summary Power', exact=True)).to_be_disabled()
            expect(page.get_by_label('realtime Prompt', exact=True)).to_be_disabled()
            for label in ('Gợi ý hành động', 'Tóm tắt'):
                expect(page.get_by_label('Prompt ' + label, exact=True)).to_be_disabled()
                expect(page.get_by_role('button', name='Sửa prompt ' + label, exact=True)).to_be_disabled()
            expect(page.get_by_label('Chu kỳ Chat realtime', exact=True)).to_be_disabled()
            expect(panel.get_by_label('Auto screenshot', exact=True)).to_be_disabled()
            expect(panel.get_by_role('button', name='Edit prompt', exact=True)).to_be_disabled()
            expect(panel.get_by_role('button', name='Lưu cài đặt', exact=True)).to_be_disabled()
            panel.locator('summary').click()
            panel.get_by_role('button', name='Explain', exact=True).click()
            expect(panel.locator('.chat-scroll')).to_contain_text('Answer to the question')
            image_data = BytesIO(); Image.new('RGB', (200,120), '#247b59').save(image_data, format='PNG')
            panel.locator('input[type=file]').set_input_files({'name':'screen.png','mimeType':'image/png','buffer':image_data.getvalue()})
            expect(panel.locator('.composer .attachment-card')).to_have_count(1)
            panel.locator('.composer .attachment-preview').click()
            expect(page.get_by_role('dialog', name='Xem ảnh đính kèm')).to_be_visible()
            page.get_by_label('Đóng xem ảnh', exact=True).click()
            page.get_by_label('Câu hỏi Chat realtime', exact=True).fill('My manual follow-up')
            panel.get_by_role('button', name='Send', exact=True).click()
            expect(panel.locator('.chat-scroll .message.bot')).to_have_count(2)
            expect(panel.locator('.chat-scroll .message.bot').last).to_contain_text('Answer to the question')
            expect(panel.locator('.chat-scroll .attachment-card')).to_have_count(1)
            fixture.add_line('Second transcript for new summary')
            fixture.due()
            expect(panel.locator('.rt-summary')).to_have_count(2, timeout=10000)
            panel.get_by_role('button', name='Stop', exact=True).click()
            expect(panel.get_by_role('button', name='Start', exact=True)).to_be_enabled()
            page.reload()
            page.get_by_role('button', name='Chat realtime', exact=True).click()
            expect(panel.locator('.rt-summary')).to_have_count(2)
            expect(panel.locator('.chat-scroll .message.bot')).to_have_count(2)
            panel.locator('summary').click()
            expect(page.get_by_label('Chu kỳ Chat realtime', exact=True)).to_have_value('5')
            expect(page.get_by_label('realtime Power', exact=True)).to_have_value('2')
            expect(page.get_by_label('realtime Summary Power', exact=True)).to_have_value('0')
            expect(page.get_by_label('realtime Summary Power', exact=True)).to_be_enabled()
            expect(page.get_by_label('realtime Power', exact=True)).to_be_enabled()
            for kind, label in [('actions', 'Gợi ý hành động'), ('summary', 'Tóm tắt')]:
                expect(page.get_by_label('Prompt ' + label, exact=True)).to_have_value('Meeting ' + kind)
            assert fixture.calls[0][1].startswith('BROWSER ')
            assert fixture.calls[1][1].startswith('BROWSER ')
            expect(panel.get_by_role('button', name='Lưu cài đặt', exact=True)).to_be_enabled()
            assert all(power == (0 if key=='summary-tab' else 2) for key,power in fixture.powers)
            page.screenshot(path='cache/realtime-chat-settings.png')
            panel.locator('summary').click()
            for theme in ('light', 'dark'):
                page.evaluate('(theme) => document.documentElement.dataset.theme=theme', theme)
                page.wait_for_timeout(350)
                page.screenshot(path=f'cache/realtime-chat-{theme}.png')
                expect(page.get_by_label('Câu hỏi Chat realtime', exact=True)).to_be_in_viewport()
                expect(panel.locator('.rt-summary-column')).to_be_in_viewport()
            page.set_viewport_size({'width':760, 'height':900})
            expect(page.get_by_label('Câu hỏi Chat realtime', exact=True)).to_be_in_viewport()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors, errors
            assert not fixture.rt.state(fixture.cid)['running']
            browser.close()
            print('Chat realtime browser checks passed: settings, two tabs, suggestions, manual chat, attachment preview, incremental summaries, Stop, reload, light/dark and narrow layout.')
    finally:
        server.should_exit = True; thread.join(timeout=10); fixture.tearDown()


if __name__ == '__main__':
    main()

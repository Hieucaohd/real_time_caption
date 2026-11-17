"""Real Chat UI/API with a closed-tab provider error; never send to real ChatGPT."""
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from test_web_api import WebApiTests
from test_chat_tab_recovery import tab
from caption import chatgpt
from caption.settings import Settings
from web_api.server import create_app


def main():
    fixture = WebApiTests(); fixture.setUp()
    settings = Settings.load()
    settings.chatgpt_selected_tab = 'closed'
    settings.chatgpt_selected_tab_title = 'Old ChatGPT'
    settings.chat_attach_transcript = False
    settings.chat_screenshot = False
    settings.save()
    server = uvicorn.Server(uvicorn.Config(create_app(fixture.service), host='127.0.0.1', port=0, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline: raise AssertionError('Server did not start')
        time.sleep(.05)
    try:
        with patch('web_api.service.chatgpt.ask', side_effect=[chatgpt.ChatGPTTabClosed('Closed'), 'Answer after manual retry']) as ask, \
                patch('web_api.service.chatgpt.list_chrome_chatgpt_tabs', return_value=[tab('first', 'First ChatGPT'), tab('second', 'Second ChatGPT')]) as scan, \
                sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}')
            page.locator('nav button').nth(1).click()
            page.locator('.chat-settings summary').click()
            chosen = page.get_by_label('chat ChatGPT tab', exact=True)
            expect(chosen).to_have_value('closed')
            scan.assert_not_called()
            page.get_by_label('chat Power', exact=True).select_option('2')
            page.locator('.chat-composer textarea').fill('Keep my question for manual retry')
            page.get_by_role('button', name='Send', exact=True).click()
            expect(page.locator('.job.error')).to_contain_text('hiện tại đang chọn tab này: First ChatGPT')
            expect(chosen).to_have_value('first')
            expect(page.get_by_label('chat Power', exact=True)).to_have_value('2')
            assert Settings.load().chatgpt_selected_tab == 'first'
            page.wait_for_timeout(1500)
            ask.assert_called_once(); scan.assert_called_once()
            assert len(fixture.service.jobs) == 1
            # Manual choice overrides the recovered tab only when Retry is clicked.
            chosen.select_option('second')
            page.get_by_role('button', name='Retry', exact=True).click()
            expect(page.locator('.job.done')).to_contain_text('Completed')
            assert ask.call_count == 2
            assert ask.call_args.kwargs['selected_tab'] == 'second'
            assert [m.role for m in fixture.service.store.messages(fixture.cid)] == ['user', 'bot']
            page.reload()
            page.locator('nav button').nth(1).click()
            page.locator('.chat-settings summary').click()
            expect(chosen).to_have_value('first')  # Recovered selection was persisted.
            scan.assert_called_once()  # Reload does not rediscover tabs/resend questions.
            assert ask.call_count == 2 and not errors, errors
            browser.close()
            print('Closed Chat tab: persisted first-tab fallback, visible notice, no automatic resend/rescan, explicit Retry and reload passed.')
    finally:
        server.should_exit = True; thread.join(10); fixture.tearDown()


if __name__ == '__main__':
    main()

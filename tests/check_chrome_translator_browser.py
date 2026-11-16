"""Real Settings, CDP adapter and worker/SQLite; fake only the browser AI model."""
from pathlib import Path
import socket
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from test_web_api import WebApiTests
from caption import chrome_translate
from caption.settings import Settings
from caption.translation import Config
from caption.transcriber import Event
from web_api.server import create_app

FAKE_API = """(() => {
  window.modelCreates = 0;
  window.Translator = {
    availability: async ({sourceLanguage}) => sourceLanguage === 'ko' ? 'unavailable' : 'downloadable',
    create: async (options) => {
      window.modelCreates++;
      options.monitor({addEventListener(type, listener) { listener({loaded: .5}); }});
      await new Promise(resolve => setTimeout(resolve, 100));
      return {translate: async (text, {signal}) => {
        if (signal.aborted) throw new DOMException('aborted', 'AbortError');
        return 'Bản dịch: ' + text;
      }};
    }
  };
})();"""


def main():
    fixture = WebApiTests(); fixture.setUp()
    server = uvicorn.Server(uvicorn.Config(create_app(fixture.service), host='127.0.0.1', port=0, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline: raise RuntimeError('Server did not start')
        time.sleep(.05)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); cdp_port = sock.getsockname()[1]
    try:
        with sync_playwright() as p, patch.object(chrome_translate, 'CDP_URL', f'http://127.0.0.1:{cdp_port}'):
            browser = p.chromium.launch(channel='chrome', headless=True, args=[f'--remote-debugging-port={cdp_port}'])
            page = browser.new_page(viewport={'width': 1400, 'height': 1100})
            page.add_init_script(FAKE_API)
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}')
            page.get_by_role('button', name='Settings', exact=True).click()
            page.get_by_role('combobox', name='Engine', exact=True).select_option('chrome')
            panel = page.get_by_role('region', name='Chrome Translator API')
            expect(panel).to_be_visible()
            page.get_by_role('combobox', name='Ngôn ngữ nguồn', exact=True).select_option('ko')
            expect(panel.get_by_role('alert')).to_contain_text('chưa hỗ trợ cặp')
            page.get_by_role('combobox', name='Ngôn ngữ nguồn', exact=True).select_option('ja')
            panel.get_by_role('button', name='Tải / khởi tạo gói ngôn ngữ').click()
            expect(panel.get_by_role('status')).to_contain_text('Sẵn sàng dịch ja → vi')
            page.get_by_role('button', name='Save settings', exact=True).click()
            expect(panel).not_to_be_visible()
            assert Settings.load().translation_engine == 'chrome'
            config = Config.from_settings(Settings.load())
            line = fixture.service.translations.line(fixture.cid, 'chrome:test', 'now', 'こんにちは')
            assert fixture.service.worker.submit(line, config)
            deadline = time.monotonic() + 15
            while True:
                history = fixture.service.translations.history(line['id'])
                if history and history[-1]['status'] not in ('queued', 'working'): break
                if time.monotonic() > deadline: raise AssertionError('Worker did not finish')
                page.wait_for_timeout(50)
            assert history[-1]['status'] == 'done', history
            assert history[-1]['translated_text'] == 'Bản dịch: こんにちは'
            assert history[-1]['engine'] == 'chrome'
            assert not fixture.service.worker.submit(line, config)  # Existing DB cache.
            assert page.evaluate('window.modelCreates') == 1
            page.locator('nav button').nth(4).click()
            live_toggle = page.get_by_role('checkbox', name='Dịch ngay khi đang nói', exact=True)
            expect(live_toggle).to_be_visible()
            live_toggle.click()
            expect(live_toggle).to_be_checked()
            page.get_by_role('checkbox', name='Auto translate', exact=True).click()
            expect(page.get_by_role('checkbox', name='Auto translate', exact=True)).to_be_checked()
            fixture.post(f'/api/conversations/{fixture.cid}/capture')
            fixture.service.on_caption(Event('partial', 'Unfinished speech'))
            fixture.service.on_caption(Event('partial', 'Unfinished speech with more words'))
            preview = page.get_by_label('Bản dịch tạm', exact=True)
            expect(preview).to_contain_text('Bản dịch: Unfinished speech with more words', timeout=15000)
            assert len(fixture.service.translations.lines(fixture.cid)) == 1  # No finalized caption emitted.
            fixture.post('/api/capture/pause')
            expect(preview).to_have_count(0)
            page.get_by_role('button', name='Settings', exact=True).click()
            expect(panel.get_by_role('status')).to_contain_text('Sẵn sàng')
            expect(page.locator('.modal').get_by_role('checkbox', name='Dịch ngay khi đang nói', exact=True)).to_be_checked()
            page.get_by_role('combobox', name='Engine', exact=True).select_option('google_web')
            expect(page.locator('.modal').get_by_role('checkbox', name='Dịch ngay khi đang nói', exact=True)).to_have_count(0)
            page.get_by_role('button', name='Close settings', exact=True).click()
            page.reload()
            page.get_by_role('button', name='Settings', exact=True).click()
            expect(page.get_by_role('combobox', name='Engine', exact=True)).to_have_value('chrome')
            # Old document models are gone; a model requiring download must be
            # prepared explicitly, never silently redownloaded by a background job.
            page.evaluate("window.__rtcChromeTranslator.start({id:'cold',text:'hello',source:'ja',target:'vi'})")
            page.wait_for_function("window.__rtcChromeTranslator.read('cold')?.status === 'error'")
            assert page.evaluate("window.__rtcChromeTranslator.read('cold').code") == 'setup'
            assert page.evaluate('window.modelCreates') == 0
            assert not errors, errors
            browser.close()
            print('Chrome Translator: Settings/save/reload, Chrome-only live preview before finalization, CDP worker, SQLite persistence/cache and no background downloads passed.')
    finally:
        server.should_exit = True; thread.join(timeout=10); fixture.tearDown()


if __name__ == '__main__':
    main()

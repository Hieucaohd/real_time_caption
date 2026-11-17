"""Real DOM observer -> SQLite -> WebSocket -> React, without external translation."""
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from test_web_api import WebApiTests
from caption.google_translate import _observe_output
from caption.settings import Settings
from caption.translation import Config
from web_api.server import create_app


def main():
    fixture = WebApiTests(); fixture.setUp()
    settings = Settings.load(); settings.translation_engine = 'google_web'; settings.save()
    config = Config.from_settings(settings)
    store = fixture.service.translations
    store.append_google(fixture.cid, config, 'Earlier source', 'Earlier translation.', 'earlier')
    server = uvicorn.Server(uvicorn.Config(create_app(fixture.service), host='127.0.0.1', port=0,
        ws='websockets-sansio', log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline: raise AssertionError('Server did not start')
        time.sleep(.05)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page()
            errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}')
            page.locator('nav button').nth(4).click()
            target = page.locator('.translation-reading p[lang="vi"]')
            expect(target).to_have_text('Earlier translation.')
            deadline = time.monotonic() + 5
            while not fixture.service.translation_updates.listeners:
                if time.monotonic() > deadline: raise AssertionError('No WebSocket subscriber')
                page.wait_for_timeout(50)
            # HTTP polling now cannot deliver translations: all updates must use WS.
            page.route(f'**/api/conversations/{fixture.cid}', lambda route: route.abort())
            google = browser.new_page()
            google.set_content('<span class="ryNqvb">Initial</span><div id="unrelated"></div>')
            updates = []
            def publish(text):
                updates.append(text)
                store.append_google(fixture.cid, config, 'Live source', text, 'current')
                fixture.service.worker.on_google_update(fixture.cid, config)
            with _observe_output(google, publish) as observer:
                google.evaluate("document.querySelector('.ryNqvb').firstChild.data = 'First version'")
                observer['flush']()
                expect(target).to_have_text('Earlier translation. First version')
                google.evaluate("document.querySelector('.ryNqvb').outerHTML = '<span class=ryNqvb>Entirely rewritten</span>'")
                observer['flush']()
                expect(target).to_have_text('Earlier translation. Entirely rewritten')
                google.evaluate("document.querySelector('.ryNqvb').textContent = 'Short'")
                observer['flush']()
                expect(target).to_have_text('Earlier translation. Short')
                google.evaluate("document.querySelector('.ryNqvb').remove()")
                observer['flush']()
                expect(target).to_have_text('Earlier translation.')
                google.evaluate("document.body.insertAdjacentHTML('beforeend', '<span jsname=W297wb>Fallback result</span>')")
                observer['flush']()
                expect(target).to_have_text('Earlier translation. Fallback result')
                count = len(updates)
                google.evaluate("document.querySelector('#unrelated').textContent = 'Do not translate me'")
                google.wait_for_timeout(100)
                assert len(updates) == count
            google.evaluate("document.querySelector('[jsname]').textContent = 'After cleanup'")
            google.wait_for_timeout(100)
            assert len(updates) == count
            page.unroute(f'**/api/conversations/{fixture.cid}')
            page.reload(); page.locator('nav button').nth(4).click()
            expect(target).to_have_text('Earlier translation. Fallback result')
            assert not errors, errors
            browser.close()
        print('DOM changes, complete rewrites, shortening, empty output, fallback, cleanup, WebSocket-only delivery and reload passed.')
    finally:
        server.should_exit = True; thread.join(10); fixture.tearDown()


if __name__ == '__main__':
    main()

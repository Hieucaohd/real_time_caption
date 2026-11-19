"""Render custom release defaults without contacting ChatGPT or recording audio."""
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from test_release_defaults import ReleaseDefaultsTests
from web_api.server import create_app


def main():
    fixture = ReleaseDefaultsTests()
    fixture.setUp()
    fixture.document['web'].update(initial_tab='realtime', theme='dark', realtime_auto_scroll=False)
    fixture.document['realtime_chat'].update(interval=45, power=0, reply_power=-1, summary_power=2)
    fixture.document['realtime_popup']['font_size'] = 9
    fixture.write()
    server = uvicorn.Server(uvicorn.Config(create_app(fixture.service), host='127.0.0.1', port=0, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError('Test server did not start')
            time.sleep(.05)
        base = f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width':1440, 'height':1000})
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto(base)
            panel = page.locator('.realtime-chat')
            expect(panel).to_be_visible()
            expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
            expect(panel.get_by_label('Tự cuộn', exact=True)).not_to_be_checked()
            panel.locator('summary').click()
            expect(page.get_by_label('Chu kỳ Chat realtime', exact=True)).to_have_value('45')
            expect(page.get_by_label('realtime Suggestions Power', exact=True)).to_have_value('0')
            expect(page.get_by_label('realtime Reply Power', exact=True)).to_have_value('-1')
            expect(page.get_by_label('realtime Summary Power', exact=True)).to_have_value('2')
            page.get_by_role('button', name='Transcript', exact=True).click()
            page.wait_for_timeout(1500)
            expect(panel).to_have_count(0)
            page.goto(base + '/?realtime_popup=' + fixture.cid)
            expect(page.get_by_label('Cỡ chữ popup', exact=True)).to_have_value('9')
            expect(page.locator('.realtime-popup')).to_have_css('--rt-popup-font-size', '9px')
            assert not errors, errors
            browser.close()
        print('Custom release defaults reach main UI and popup; polling preserves user navigation.')
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        fixture.tearDown()


if __name__ == '__main__':
    main()

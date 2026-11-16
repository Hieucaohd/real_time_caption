"""Read-only branch highlights against temporary SQLite data and headless Chrome."""
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
    fixture.service.busy = True
    research = fixture.service.research
    store = research.store
    text = 'Alpha concept. Beta concept. Alpha concept.'
    fixture.service.store.add_message(fixture.cid, 'user', 'You', 'Root question')
    mid = fixture.service.store.add_message(fixture.cid, 'bot', 'ChatGPT', text)
    root = store.import_answer(fixture.cid, mid)
    def children(start, excerpt, questions):
        return research.children(fixture.cid, root['id'], dict(questions=questions, excerpt=excerpt,
            parent_reading_mark=dict(start=start, end=start + len(excerpt), text=excerpt), config={'selected_tab':'fake'}))
    a, b = children(0, 'Alpha concept.', ['First explanation', 'First example'])
    c = children(text.rindex('Alpha'), 'Alpha concept.', ['Last occurrence'])[0]
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
            errors=[]; page.on('pageerror', lambda e: errors.append(str(e)))
            page.route('**/api/chrome-tabs', lambda route: route.fulfill(json=[]))
            page.goto(f'http://127.0.0.1:{port}')
            page.locator('nav button').nth(2).click()
            page.wait_for_function("CSS.highlights.get('rtc-branch-marks')?.size === 2")
            page.get_by_role('button', name='Bỏ dấu', exact=True).click()
            expect(page.get_by_role('button', name='Bỏ dấu', exact=True)).to_have_count(0)
            assert page.evaluate("CSS.highlights.get('rtc-branch-marks').size") == 2
            assert not page.evaluate("CSS.highlights.has('rtc-reading-mark')")
            assert page.locator('.research-answer').inner_text() == text

            def hover_mark(index):
                point = page.evaluate('''index => {
                    const rect=[...CSS.highlights.get('rtc-branch-marks')][index].getClientRects()[0];
                    return {x:rect.left+4,y:rect.top+4};
                }''', index)
                page.mouse.move(point['x'], point['y'])
            hover_mark(0)
            tooltip = page.get_by_role('tooltip')
            expect(tooltip).to_contain_text('First explanation')
            expect(tooltip).to_contain_text('First example')
            expect(tooltip).not_to_contain_text('Last occurrence')
            hover_mark(1)
            expect(tooltip).to_contain_text('Last occurrence')
            expect(tooltip).not_to_contain_text('First explanation')
            page.locator('.branch-marks-list summary').click()
            entries = page.locator('.branch-mark-entry')
            expect(entries).to_have_count(2)
            store.update(fixture.cid, a['id'], title='Renamed explanation')
            store.set_hidden(fixture.cid, b['id'], True)
            expect(entries.first).to_contain_text('Renamed explanation')
            expect(entries.first).to_contain_text('First example (đã ẩn)')
            research.delete(fixture.cid, c['id'])
            expect(entries).to_have_count(1)
            page.wait_for_function("CSS.highlights.get('rtc-branch-marks')?.size === 1")
            page.reload(); page.locator('nav button').nth(2).click()
            page.wait_for_function("CSS.highlights.get('rtc-branch-marks')?.size === 1")
            assert not page.evaluate("CSS.highlights.has('rtc-reading-mark')")
            page.locator('.branch-marks-list summary').click()
            expect(page.locator('.branch-marks-list')).to_contain_text('Renamed explanation')
            expect(page.locator('.branch-marks-list button')).to_have_count(0)
            page.screenshot(path='cache/research-branch-marks.png')
            assert not errors, errors
            print('Branch marks: repeated text, grouped children, hover, read-only list, reading-mark independence, rename/hide/delete and reload passed.')
            browser.close()
    finally:
        server.should_exit=True; thread.join(6); fixture.tearDown()


if __name__ == '__main__': main()

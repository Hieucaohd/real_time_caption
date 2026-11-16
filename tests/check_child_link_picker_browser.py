"""Select a passage, link an existing child, reload, hover and navigate."""
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
    manager = fixture.service.research
    store = manager.store
    text = 'Alpha concept. Beta concept. Alpha concept.'
    mid = fixture.service.store.add_message(fixture.cid, 'bot', 'ChatGPT', text)
    root = store.import_answer(fixture.cid, mid)
    children = manager.children(fixture.cid, root['id'], dict(questions=['Explain alpha', 'Explain beta'],
                                                            config={'selected_tab': 'fake'}))
    for node in children:
        store.update(fixture.cid, node['id'], status='done', answer='Answer ' + node['title'])
    server = uvicorn.Server(uvicorn.Config(create_app(fixture.service), host='127.0.0.1', port=0, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline: raise RuntimeError('Server did not start')
        time.sleep(.05)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 1000})
            errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
            page.route('**/api/chrome-tabs', lambda route: route.fulfill(json=[]))
            page.goto(f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}')
            page.locator('nav button').nth(2).click()
            expect(page.locator('.research-answer')).to_have_text(text)

            def choose_passage(start):
                # Native DOM selection: select the last identical passage, not the first.
                point = page.locator('.research-answer').evaluate('''(el, start) => {
                    const text = el.querySelector('p').firstChild;
                    const range = document.createRange();
                    range.setStart(text, start); range.setEnd(text, start + 13);
                    const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
                    const rect = range.getBoundingClientRect(); return {x:rect.left+4, y:rect.top+4};
                }''', start)
                page.mouse.click(point['x'], point['y'], button='right')
                page.get_by_role('menuitem', name='Chọn node con để liên kết', exact=True).click()
                return page.get_by_role('dialog', name='Chọn node con để liên kết', exact=True)

            picker = choose_passage(text.rindex('Alpha'))
            expect(picker.get_by_role('button', name='Lưu liên kết')).to_be_disabled()
            picker.get_by_role('textbox').fill('beta')
            expect(picker.get_by_role('radio')).to_have_count(1)
            picker.get_by_role('textbox').fill('alpha')
            picker.get_by_role('radio', name='Explain alpha', exact=True).check()
            picker.get_by_role('button', name='Lưu liên kết', exact=True).click()
            expect(picker).to_have_count(0)
            page.wait_for_function("CSS.highlights.get('rtc-branch-marks')?.size === 1")
            mark = store.branch_marks(fixture.cid, root['id'])[0]
            assert mark['id'] == children[0]['id']
            assert mark['mark']['start'] == text.rindex('Alpha')
            assert store.get(fixture.cid, root['id'])['reading_mark'] is None
            page.reload()
            page.locator('nav button').nth(2).click()
            page.wait_for_function("CSS.highlights.get('rtc-branch-marks')?.size === 1")
            point = page.evaluate('''() => {
                const r = [...CSS.highlights.get('rtc-branch-marks')][0].getBoundingClientRect();
                return {x:r.left+4,y:r.top+4};
            }''')
            page.mouse.move(point['x'], point['y'])
            expect(page.locator(f'[data-node-id="{children[0]["id"]}"] .branch-highlighted')).to_have_count(1)
            expect(page.locator(f'path[data-child-id="{children[0]["id"]}"].branch-highlighted')).to_have_count(1)
            page.mouse.click(point['x'], point['y'])
            expect(page.locator('.research-answer')).to_have_text('Answer Explain alpha')
            # Empty child list, Escape, then move an existing link with explicit notice.
            picker = choose_passage(0)
            expect(picker).to_contain_text('Node này chưa có node con')
            page.keyboard.press('Escape')
            expect(picker).to_have_count(0)
            page.locator('.research-breadcrumb').get_by_role('button', name='Gốc', exact=True).click()
            expect(page.locator('.research-answer')).to_have_text(text)
            picker = choose_passage(0)
            picker.get_by_role('radio', name='Explain alpha', exact=True).check()
            expect(picker).to_contain_text('Lưu sẽ chuyển liên kết')
            picker.get_by_role('button', name='Lưu liên kết', exact=True).click()
            expect(picker).to_have_count(0)
            assert store.branch_marks(fixture.cid, root['id'])[0]['mark']['start'] == 0
            assert len(store.list(fixture.cid)) == 3
            assert not errors, errors
            browser.close()
            print('Child link picker: persistence, replacement, selection, hover, navigation, empty list and Escape passed.')
    finally:
        server.should_exit = True; thread.join(timeout=10); fixture.tearDown()


if __name__ == '__main__':
    main()

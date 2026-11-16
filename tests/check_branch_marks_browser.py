"""Branch highlight navigation against temporary SQLite data and headless Chrome."""
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
    for child in (a, b, c):
        store.update(fixture.cid, child['id'], status='done', answer='Answer: ' + child['title'])
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
                return point
            def go_root():
                page.locator('.research-breadcrumb').get_by_role('button', name='Gốc', exact=True).click()
                page.wait_for_function("CSS.highlights.get('rtc-branch-marks')?.size === 2")
                expect(page.locator('.research-answer')).to_have_text(text)
            def glow(child):
                return page.locator(f'[data-node-id="{child["id"]}"] .research-node.branch-highlighted')
            def edge(child):
                return page.locator(f'.research-edges path[data-child-id="{child["id"]}"].branch-highlighted')
            hover_mark(0)
            tooltip = page.get_by_role('tooltip')
            expect(tooltip).to_contain_text('First explanation')
            expect(tooltip).to_contain_text('First example')
            expect(tooltip).not_to_contain_text('Last occurrence')
            expect(glow(a)).to_have_count(1)
            expect(glow(b)).to_have_count(1)
            expect(edge(a)).to_have_count(1)
            expect(edge(b)).to_have_count(1)
            expect(glow(c)).to_have_count(0)
            hover_mark(1)
            expect(tooltip).to_contain_text('Last occurrence')
            expect(tooltip).not_to_contain_text('First explanation')
            expect(glow(c)).to_have_count(1)
            expect(edge(c)).to_have_count(1)
            expect(glow(a)).to_have_count(0)
            assert glow(c).evaluate('el => getComputedStyle(el).borderColor') == 'rgb(125, 211, 252)'
            assert edge(c).evaluate('el => getComputedStyle(el).stroke') == 'rgb(125, 211, 252)'
            page.screenshot(path='cache/research-branch-marks-hover.png')
            # Hover expands a collapsed path only temporarily, without changing its saved state.
            page.mouse.move(5, 5)
            expect(page.locator('.research-node.branch-highlighted')).to_have_count(0)
            page.get_by_role('button', name='Thu nhánh Root question', exact=True).click()
            expect(page.locator(f'[data-node-id="{c["id"]}"]')).to_have_count(0)
            page.wait_for_timeout(500)
            saved_layout = store.graph_layout(fixture.cid, root['id'])
            hover_mark(1)
            expect(glow(c)).to_have_count(1)
            expect(edge(c)).to_have_count(1)
            page.mouse.move(5, 5)
            expect(page.locator(f'[data-node-id="{c["id"]}"]')).to_have_count(0)
            assert store.graph_layout(fixture.cid, root['id']) == saved_layout
            # A single linked child opens directly, including its answer and graph selection.
            point = hover_mark(1)
            page.mouse.click(point['x'], point['y'])
            expect(page.locator('.research-answer')).to_have_text('Answer: Last occurrence')
            expect(page.locator(f'[data-node-id="{c["id"]}"] .research-node.selected')).to_have_count(1)
            expect(page.locator('.research-node.branch-highlighted')).to_have_count(0)
            go_root()
            # Dragging to select text must not navigate or open the child chooser.
            point = hover_mark(0)
            page.mouse.down(); page.mouse.move(point['x'] + 65, point['y'], steps=8); page.mouse.up()
            assert page.evaluate('window.getSelection().toString().length') > 0
            expect(page.locator('.research-answer')).to_have_text(text)
            expect(page.get_by_role('dialog', name='Chọn node con', exact=True)).to_have_count(0)
            page.evaluate('window.getSelection().removeAllRanges()')
            # Multiple children use a chooser. Escape dismisses it; each entry lights its own edge.
            point = hover_mark(0); page.mouse.click(point['x'], point['y'])
            picker = page.get_by_role('dialog', name='Chọn node con', exact=True)
            expect(picker).to_be_visible()
            picker.get_by_role('button', name='First example', exact=True).hover()
            expect(glow(b)).to_have_count(1)
            expect(edge(b)).to_have_count(1)
            expect(glow(a)).to_have_count(0)
            page.keyboard.press('Escape')
            expect(picker).to_have_count(0)
            expect(page.locator('.research-node.branch-highlighted')).to_have_count(0)
            point = hover_mark(0); page.mouse.click(point['x'], point['y'])
            picker.get_by_role('button', name='First explanation', exact=True).click()
            expect(page.locator('.research-answer')).to_have_text('Answer: First explanation')
            go_root()
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
            expect(page.locator('.branch-marks-list .branch-mark-link')).to_have_count(2)
            # Keyboard navigation can open hidden children without unhiding them in the database.
            hidden_link = page.locator('.branch-marks-list').get_by_role('button', name='First example (đã ẩn)', exact=True)
            hidden_link.focus(); hidden_link.press('Enter')
            expect(page.locator('.research-answer')).to_have_text('Answer: First example')
            expect(page.get_by_label('Hiện node đã ẩn (1)', exact=True)).to_be_checked()
            assert store.get(fixture.cid, b['id'])['hidden']
            page.screenshot(path='cache/research-branch-marks.png')
            assert not errors, errors
            print('Branch marks: exact hover/edges, collapsed preview without saving, click navigation, multi-child chooser, selection drag, keyboard/hidden navigation, repeated text, rename/hide/delete and reload passed.')
            browser.close()
    finally:
        server.should_exit=True; thread.join(6); fixture.tearDown()


if __name__ == '__main__': main()

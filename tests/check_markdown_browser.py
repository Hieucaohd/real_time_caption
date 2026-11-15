"""Verify the complete Copy -> API -> browser Markdown pipeline in temporary data."""
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from test_web_api import WebApiTests
from web_api.server import create_app


ANSWER = r"""## Giới hạn và tính liên tục

**Ý tưởng chính:** xét hàm số khi một biến tiến gần một giá trị. Với \(x_1\) và \(x_2\), ta có:

\[\lim_{x\to a} f(x) = L\]

### Ma trận và hệ phương trình

1. Đây là công thức khối bên trong danh sách:

   \[\begin{aligned}x+y&=3\\x-y&=1\end{aligned}\]

   - Công thức trong dòng: $a^2+b^2=c^2$.
   - **Kết luận:** giữ nguyên cấu trúc toán.

\[\begin{array}{c|c}x & y\\\hline 1 & 2\\3 & 4\end{array}\]

> Một trích dẫn có **chữ đậm**, `inline code` và [liên kết](https://example.com).

---

| Khái niệm | Công thức | Ghi chú |
| :--- | :---: | ---: |
| Đạo hàm | $f'(x)$ | 1 |
| Tích phân | $\int_a^b f(x)dx$ | 2 |

```python
def square(x):
    # Keep the original source when copying.
    return x ** 2
```

```unknown-language
$$do_not_render_as_math$$
<script>window.__unsafe = true</script>
```

- [x] Đã hiểu ví dụ
- [ ] Đọc thêm

<script>window.__unsafe = true</script>

[unsafe link](javascript:alert(1))

\[\boxed{\frac{1+\frac{1}{2}}{\sqrt{x^2+1}}}\]

$$\sum_{i=1}^{n} i = \frac{n(n+1)}{2}$$

\\[ \boxed{ T_qS= \left\\{ \gamma'(0): \begin{array}{l} \gamma:(-\varepsilon,\varepsilon)\to S,\\\ \gamma\text{ is }C^1,\quad \gamma(0)=q \end{array} \right\\} } \\]

\\[ -0.5\<t<0.5 \\]

\\[ \int_a^b f(x)\\,dx=\lim\_{n\to\infty}\sum\_{i=1}^n f(x_i^\*)\Delta x \\]
"""


def main():
    fixture = WebApiTests(); fixture.setUp()
    fixture.service.busy = True
    fixture.service.store.add_message(fixture.cid, 'user', 'You', 'Giải thích khái niệm')
    mid = fixture.service.store.add_message(fixture.cid, 'bot', 'ChatGPT', ANSWER,
        response_conversation_url='https://chatgpt.com/g/project/c/source-conversation',
        response_turn_id='source-turn', response_message_id='source-response', response_position=2)
    fixture.service.research.store.import_answer(fixture.cid, mid)
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
            context = browser.new_context(viewport={'width':1440,'height':1000})
            # Exercise the button without replacing the user's system clipboard.
            context.add_init_script("Object.defineProperty(navigator, 'clipboard', {value: {writeText: async text => {window.__copiedCode = text}}})")
            page = context.new_page()
            tab_events = []
            def scan_tabs(route):
                tab_events.append('scan')
                route.fulfill(json=[])
            page.route('**/api/chrome-tabs', scan_tabs)
            errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{port}')
            page.locator('nav button').nth(1).click()
            md = page.locator('.message.bot .response-markdown')
            source = page.locator('.message.bot .response-source')
            source.locator('summary').click()
            expect(source.locator('a')).to_have_attribute('href', 'https://chatgpt.com/g/project/c/source-conversation')
            expect(source).to_contain_text('Câu trả lời thứ 2')
            expect(source).not_to_contain_text('Message ID')
            expect(source).not_to_contain_text('Turn ID')
            source.get_by_role('button', name='Cập nhật nguồn', exact=True).click()
            source.get_by_label('Link cuộc hội thoại ChatGPT', exact=True).fill('https://evil.test/c/not-chatgpt')
            source.get_by_role('button', name='Lưu nguồn', exact=True).click()
            expect(source.get_by_role('alert')).to_contain_text('Nhập link cuộc hội thoại ChatGPT')
            source.get_by_label('Link cuộc hội thoại ChatGPT', exact=True).fill('https://chatgpt.com/g/project/c/source-conversation')
            expect(source.locator('input')).to_have_count(2)
            source.get_by_label('Số thứ tự câu trả lời', exact=True).fill('4')
            source.get_by_role('button', name='Lưu nguồn', exact=True).click()
            expect(source.locator('form')).to_have_count(0)
            expect(source).to_contain_text('Câu trả lời thứ 4')
            source.locator('summary').click()
            expect(md.locator('.katex-display')).to_have_count(8)
            assert md.locator('.katex:not(.katex-display)').count() >= 10
            expect(md.locator('.katex-error')).to_have_count(0)
            expect(md.locator('.katex [style*="color"]')).to_have_count(0)
            expect(md.locator('annotation').filter(has_text='-0.5<t<0.5')).to_have_count(1)
            assert fixture.service.store.messages(fixture.cid)[-1].content == ANSWER
            assert md.locator('.katex .mop').filter(has_text='lim').count() > 0
            assert md.locator('.katex .msupsub').count() > 0
            expect(md.locator('ol > li > ul > li')).to_have_count(2)
            expect(md.locator('.response-table-scroll table')).to_have_count(1)
            assert md.locator('code .hljs-keyword').count() > 0
            expect(md.locator('.response-code-block')).to_have_count(2)
            expect(md.locator('.response-code-block').nth(1)).to_contain_text('$$do_not_render_as_math$$')
            assert not page.evaluate('window.__unsafe || false')
            assert not md.locator('a[href^="javascript:"]').count()
            code = md.locator('.response-code-block').first
            code.get_by_role('button', name='Copy code', exact=True).click()
            expected = 'def square(x):\n    # Keep the original source when copying.\n    return x ** 2\n'
            expect(code).to_contain_text('Copied!')
            copied = page.evaluate('window.__copiedCode')
            assert copied == expected, repr(copied)
            metrics = md.evaluate('el => {const s=getComputedStyle(el);return [s.fontSize,s.lineHeight,s.color,s.maxWidth]}')
            assert metrics == ['16px','26px','rgb(13, 13, 13)','768px'], metrics
            assert md.locator('h2').evaluate('el=>getComputedStyle(el).fontSize') == '18px'
            assert md.locator('h3').evaluate('el=>getComputedStyle(el).fontSize') == '16px'
            page.locator('.chat-scroll').evaluate('el=>el.scrollTop=0')
            page.screenshot(path='cache/markdown-chat.png')
            page.locator('nav button').nth(2).click()
            branch = page.locator('.research-answer .response-markdown')
            branch_source = page.locator('.research-reader .response-source')
            branch_source.locator('summary').click()
            expect(branch_source).to_contain_text('Câu trả lời thứ 4')
            expect(branch_source.locator('a')).to_have_attribute('href', 'https://chatgpt.com/g/project/c/source-conversation')
            branch_source.get_by_role('button', name='Cập nhật nguồn', exact=True).click()
            branch_source.get_by_label('Số thứ tự câu trả lời', exact=True).fill('5')
            branch_source.get_by_role('button', name='Lưu nguồn', exact=True).click()
            expect(branch_source.locator('form')).to_have_count(0)
            assert fixture.service.store.messages(fixture.cid)[-1].response_position == 5
            branch_source.locator('summary').click()
            expect(branch.locator('.katex-display')).to_have_count(8)
            expect(branch.locator('.katex-error')).to_have_count(0)
            expect(branch.locator('.katex [style*="color"]')).to_have_count(0)
            assert branch.evaluate('el=>getComputedStyle(el).fontSize') == '16px'
            assert branch.evaluate('el=>getComputedStyle(el).lineHeight') == '26px'
            assert branch.locator('h2').evaluate('el=>getComputedStyle(el).fontSize') == '18px'
            page.screenshot(path='cache/markdown-research.png')
            # Selection remains usable for creating children after custom rendering.
            branch.locator('p').first.evaluate('el=>{const r=document.createRange();r.selectNodeContents(el);const s=getSelection();s.removeAllRanges();s.addRange(r)}')
            branch.locator('p').first.click(button='right')
            expect(page.get_by_role('menuitem', name='Tạo nhánh từ đoạn đã chọn')).to_be_visible()
            page.get_by_role('menuitem', name='Đánh dấu đã đọc tới đây', exact=True).click()
            expect(page.get_by_role('button', name='Bỏ dấu', exact=True)).to_be_visible()
            page.wait_for_function("CSS.highlights.get('rtc-reading-mark')?.size === 1")
            root = fixture.service.research.store.list(fixture.cid)[0]
            saved_mark = root['reading_mark']
            assert saved_mark and 'Ý tưởng chính' in saved_mark['text']
            assert page.evaluate("[...CSS.highlights.get('rtc-reading-mark')][0].toString()") == saved_mark['text']
            assert root['answer'] == ANSWER
            child = fixture.service.research.children(fixture.cid, root['id'],
                {'questions': ['Child mark test'], 'config': {'selected_tab': 'fake-tab'}})[0]
            fixture.service.research.store.update(fixture.cid, child['id'], status='done', answer='A different answer')
            page.get_by_label('Tìm node', exact=True).fill('Child mark test')
            page.locator('.research-search-results button').first.click()
            expect(branch).to_have_text('A different answer')
            assert not page.evaluate("CSS.highlights.has('rtc-reading-mark')")
            # Old answers can be filled in partially.
            branch_source.locator('summary').click()
            branch_source.get_by_role('button', name='Cập nhật nguồn', exact=True).click()
            branch_source.get_by_label('Link cuộc hội thoại ChatGPT', exact=True).fill('https://chatgpt.com/c/manual-child')
            branch_source.get_by_role('button', name='Lưu nguồn', exact=True).click()
            expect(branch_source.locator('form')).to_have_count(0)
            expect(branch_source.locator('a')).to_have_attribute('href', 'https://chatgpt.com/c/manual-child')
            assert fixture.service.research.store.get(fixture.cid, child['id'])['response_position'] is None
            assert fixture.service.store.messages(fixture.cid)[-1].response_position == 5
            page.get_by_role('button', name='Tạo nhánh con', exact=True).click()
            missing_source_dialog = page.locator('.branch-composer')
            expect(missing_source_dialog.get_by_role('button', name='Tạo nhánh ChatGPT', exact=True)).to_be_disabled()
            expect(missing_source_dialog).to_contain_text('Hãy bổ sung link và số thứ tự câu trả lời')
            missing_source_dialog.get_by_role('button', name='Đóng tạo nhánh', exact=True).click()
            page.locator('.research-breadcrumb').get_by_role('button', name='Gốc', exact=True).click()
            page.wait_for_function("CSS.highlights.get('rtc-reading-mark')?.size === 1")
            assert page.evaluate("[...CSS.highlights.get('rtc-reading-mark')][0].toString()") == saved_mark['text']
            page.reload()
            page.locator('nav button').nth(2).click()
            page.wait_for_function("CSS.highlights.get('rtc-reading-mark')?.size === 1")
            branch_source.locator('summary').click()
            expect(branch_source).to_contain_text('Câu trả lời thứ 5')
            branch_source.locator('summary').click()
            # The toolbar pencil replaces the previous mark; clear removes it from SQLite.
            branch.locator('li').first.evaluate('el=>{const r=document.createRange();r.selectNodeContents(el);const s=getSelection();s.removeAllRanges();s.addRange(r)}')
            page.get_by_role('button', name='Đánh dấu', exact=True).click()
            expect(page.locator('.research-notice')).to_contain_text('Đã tô vàng')
            page.wait_for_function("CSS.highlights.get('rtc-reading-mark')?.size === 1 && [...CSS.highlights.get('rtc-reading-mark')][0].toString().includes('công thức khối')")
            page.get_by_role('button', name='Tới dấu', exact=True).click()
            page.get_by_role('button', name='Bỏ dấu', exact=True).click()
            expect(page.get_by_role('button', name='Bỏ dấu', exact=True)).to_have_count(0)
            assert not page.evaluate("CSS.highlights.has('rtc-reading-mark')")
            assert fixture.service.research.store.get(fixture.cid, root['id'])['reading_mark'] is None
            page.set_viewport_size({'width':390,'height':844})
            page.get_by_role('button', name='Mở rộng câu trả lời', exact=True).click()
            expect(branch).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert branch.evaluate('el=>el.scrollWidth <= el.clientWidth + 1')
            # Theme setting is global, durable in SQLite, and covers controls and responses.
            page.set_viewport_size({'width':1440,'height':1000})
            page.get_by_role('button', name='Settings', exact=True).click()
            page.get_by_label('Chế độ hiển thị', exact=True).select_option('dark')
            page.get_by_role('button', name='Save settings', exact=True).click()
            expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
            assert fixture.service.store.web_theme() == 'dark'
            assert branch.evaluate('el=>getComputedStyle(el).color') == 'rgb(236, 236, 236)'
            expect(branch.locator('.katex-error')).to_have_count(0)
            page.get_by_role('button', name='Hiện đồ thị', exact=True).click()
            page.screenshot(path='cache/theme-dark-research.png')
            for selector in ('main', '.sidebar', '.research-reader'):
                color = page.locator(selector).evaluate('el=>getComputedStyle(el).backgroundColor')
                assert color in ('rgb(25, 30, 27)', 'rgb(34, 42, 37)'), (selector, color)
            page.get_by_role('button', name='Tạo nhánh con', exact=True).click()
            dialog = page.locator('.branch-composer')
            assert dialog.evaluate('el=>getComputedStyle(el).backgroundColor') == 'rgb(25, 30, 27)'
            assert dialog.locator('textarea').evaluate('el=>getComputedStyle(el).color') == 'rgb(230, 238, 232)'
            # Creating the external branch selects its returned tab even before a scan finds it.
            branch_requests = []
            def create_branch(route):
                branch_requests.append(route.request.url)
                tab_events.append('branch')
                if len(branch_requests) == 1:
                    route.fulfill(status=400, json={'detail': 'Branch unavailable'})
                    return
                route.fulfill(json={'key': 'fresh-branch-tab', 'title': 'Branch from parent', 'url': 'https://chatgpt.com/c/fresh-branch'})
            page.route('**/api/conversations/*/research/*/chatgpt-branch', create_branch)
            dialog.get_by_label('New ChatGPT chat', exact=True).check()
            dialog.get_by_label('Câu hỏi nhánh', exact=True).fill('Keep this draft question')
            dialog.get_by_role('button', name='Tạo nhánh ChatGPT', exact=True).click()
            expect(dialog.get_by_role('alert')).to_contain_text('Branch unavailable')
            expect(dialog.get_by_label('research ChatGPT tab')).to_have_value('')
            expect(dialog.get_by_role('button', name='Xếp hàng 1 nhánh', exact=True)).to_be_disabled()
            dialog.get_by_role('button', name='Tạo nhánh ChatGPT', exact=True).click()
            expect(dialog.get_by_label('research ChatGPT tab')).to_have_value('fresh-branch-tab')
            assert tab_events[-2:] == ['branch', 'scan']
            expect(dialog.get_by_label('New ChatGPT chat', exact=True)).not_to_be_checked()
            expect(dialog.get_by_label('Câu hỏi nhánh', exact=True)).to_have_value('Keep this draft question')
            expect(dialog).to_contain_text('Đã mở nhánh ChatGPT trong tab mới')
            assert len(branch_requests) == 2 and all(root['id'] in url for url in branch_requests)
            assert fixture.service.research.store.get(fixture.cid, root['id'])['response_position'] == 5
            dialog.get_by_label('Auto screenshot', exact=True).uncheck()
            dialog.get_by_label('Đính kèm transcript', exact=True).uncheck()
            with page.expect_response('**/research/*/children') as queued_response:
                dialog.get_by_role('button', name='Xếp hàng 1 nhánh', exact=True).click()
            assert queued_response.value.status == 200, queued_response.value.text()
            expect(dialog).to_have_count(0)
            queued = next(n for n in fixture.service.research.store.list(fixture.cid) if n['question'] == 'Keep this draft question')
            assert queued['config']['selected_tab'] == 'fresh-branch-tab'
            assert queued['config']['new_chat'] is False
            page.locator('nav button').nth(1).click()
            assert md.evaluate('el=>getComputedStyle(el).color') == 'rgb(236, 236, 236)'
            assert page.locator('.message.bot').evaluate('el=>getComputedStyle(el).backgroundColor') == 'rgba(0, 0, 0, 0)'
            page.screenshot(path='cache/theme-dark-chat.png')
            page.reload()
            expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
            page.get_by_role('button', name='Settings', exact=True).click()
            expect(page.get_by_label('Chế độ hiển thị', exact=True)).to_have_value('dark')
            page.get_by_label('Chế độ hiển thị', exact=True).select_option('light')
            page.get_by_role('button', name='Close settings', exact=True).click()
            expect(page.locator('html')).to_have_attribute('data-theme', 'dark')
            page.get_by_role('button', name='Settings', exact=True).click()
            page.get_by_label('Chế độ hiển thị', exact=True).select_option('light')
            page.get_by_role('button', name='Save settings', exact=True).click()
            expect(page.locator('html')).to_have_attribute('data-theme', 'light')
            page.locator('nav button').nth(1).click()
            assert md.evaluate('el=>getComputedStyle(el).color') == 'rgb(13, 13, 13)'
            assert fixture.service.store.web_theme() == 'light'
            assert not errors, errors
            print('Markdown browser checks passed: display/inline math, arrays, lists, tables, highlighting, copy, safety, branch selection, mobile.')
            browser.close()
    finally:
        server.should_exit = True; thread.join(timeout=10)
        fixture.tearDown()


if __name__ == '__main__':
    main()

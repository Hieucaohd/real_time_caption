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
    mid = fixture.service.store.add_message(fixture.cid, 'bot', 'ChatGPT', ANSWER)
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
            errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{port}')
            page.locator('nav button').nth(1).click()
            md = page.locator('.message.bot .response-markdown')
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
            page.keyboard.press('Escape')
            page.set_viewport_size({'width':390,'height':844})
            page.get_by_role('button', name='Mở rộng câu trả lời', exact=True).click()
            expect(branch).to_be_visible()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert branch.evaluate('el=>el.scrollWidth <= el.clientWidth + 1')
            assert not errors, errors
            print('Markdown browser checks passed: display/inline math, arrays, lists, tables, highlighting, copy, safety, branch selection, mobile.')
            browser.close()
    finally:
        server.should_exit = True; thread.join(timeout=10)
        fixture.tearDown()


if __name__ == '__main__':
    main()

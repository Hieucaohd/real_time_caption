"""Parallel reading, realtime order, scroll and saved settings; no provider calls."""
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from test_web_api import WebApiTests
from caption.settings import Settings
from caption.translation import Config
from web_api.server import create_app


def main():
    fixture = WebApiTests(); fixture.setUp()
    settings = Settings.load()
    settings.translation_engine = 'chrome'
    settings.translation_partial = True
    settings.save()
    store = fixture.service.translations
    config = Config.from_settings(settings)
    def line(index, text):
        return store.line(fixture.cid, f'reading:{index}', 'now', text)
    first, second = line(1, 'First source sentence.'), line(2, 'Second source sentence.')
    a, b = store.begin(first, config), store.begin(second, config)
    store.finish(b, 'done', 'Second translated sentence.')
    server = uvicorn.Server(uvicorn.Config(create_app(fixture.service), host='127.0.0.1', port=0, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline: raise AssertionError('Server did not start')
        time.sleep(.05)
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width': 1440, 'height': 900})
            errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}')
            page.locator('nav button').nth(4).click()
            page.get_by_role('button', name='Đọc song song', exact=True).click()
            left, right = page.get_by_label('Đoạn nguyên văn', exact=True), page.get_by_label('Đoạn bản dịch', exact=True)
            expect(left.locator('p')).to_have_text('First source sentence. Second source sentence.')
            expect(right.locator('p')).to_have_text('Second translated sentence.')
            store.finish(a, 'done', 'First translated sentence.')
            expect(right.locator('p')).to_have_text('First translated sentence. Second translated sentence.')
            batch = [line(3, 'Third.'), line(4, 'Fourth.')]
            for attempt in store.begin_batch(batch, config):
                store.finish(attempt, 'done', 'One translated batch.')
            expect(right.locator('p')).to_have_text('First translated sentence. Second translated sentence. One translated batch.')
            shared_scroll = page.get_by_label('Cuộn nguyên văn và bản dịch', exact=True)
            expect(page.locator('.translation-reading .scroll')).to_have_count(1)
            source_first = left.locator(f'[data-line-id="{first["id"]}"]')
            target_first = right.locator(f'[data-line-id="{first["id"]}"]')
            source_first.hover()
            expect(target_first).to_have_class('translation-sentence is-highlighted')
            expect(right.locator('.is-highlighted')).to_have_count(1)
            right.locator(f'[data-line-id="{second["id"]}"]').hover()
            expect(left.locator(f'[data-line-id="{second["id"]}"]')).to_have_class('translation-sentence is-highlighted')
            expect(source_first).to_have_class('translation-sentence')
            left.locator(f'[data-line-id="{batch[1]["id"]}"]').hover()
            expect(right.locator(f'[data-line-id="{batch[0]["id"]}"]')).to_have_class('translation-sentence is-highlighted')
            right.locator(f'[data-line-id="{batch[0]["id"]}"]').hover()
            expect(left.locator('.is-highlighted')).to_have_count(2)
            page.locator('.translation-reading-head').hover()
            expect(page.locator('.translation-reading .is-highlighted')).to_have_count(0)
            target_first.focus()
            expect(source_first).to_have_class('translation-sentence is-highlighted')
            target_first.evaluate('e => e.blur()')
            for i in range(5, 80):
                item = line(i, f'Source sentence {i} with enough text to test scrolling.')
                store.finish(store.begin(item, config), 'done', f'Translated sentence {i} with enough text to test scrolling.')
            expect(left).to_contain_text('Source sentence 79')
            page.wait_for_function("(() => { const e = document.querySelector('.translation-reading-scroll'); return e.scrollHeight - e.clientHeight - e.scrollTop < 3; })()")
            page.get_by_role('checkbox', name='Tự cuộn xuống cuối', exact=True).uncheck()
            shared_scroll.evaluate('e => e.scrollTop = 0')
            before = [left.bounding_box()['y'], right.bounding_box()['y']]
            shared_scroll.evaluate('e => e.scrollTop = 50')
            after = [left.bounding_box()['y'], right.bounding_box()['y']]
            assert all(abs(start - end - 50) < 2 for start, end in zip(before, after))
            tail = line(80, 'Realtime tail.')
            store.finish(store.begin(tail, config), 'done', 'Realtime translation.')
            expect(right).to_contain_text('Realtime translation.')
            assert abs(shared_scroll.evaluate('e => e.scrollTop') - 50) < 2
            assert left.evaluate('e => e.scrollTop') == right.evaluate('e => e.scrollTop') == 0
            with fixture.service.lock:
                fixture.service.capture.update(active=True, conversation_id=fixture.cid, partial='Still speaking')
                fixture.service.translation_preview = 'Temporary translation'
            expect(right.get_by_label('Bản dịch tạm', exact=True)).to_have_text('Temporary translation')
            with fixture.service.lock:
                fixture.service.capture.update(active=False, partial='')
                fixture.service.translation_preview = ''
            expect(right.get_by_label('Bản dịch tạm', exact=True)).to_have_count(0)
            page.get_by_role('button', name='Lưu cài đặt', exact=True).click()
            expect(page.locator('.toast')).to_contain_text('Đã lưu cài đặt dịch')
            saved = Settings.load()
            assert saved.translation_view == 'parallel' and not saved.translation_auto_scroll
            assert saved.translation_partial and saved.translation_engine == 'chrome'
            page.reload(); page.locator('nav button').nth(4).click()
            expect(left).to_be_visible()
            expect(page.get_by_role('checkbox', name='Tự cuộn xuống cuối', exact=True)).not_to_be_checked()
            page.evaluate("document.documentElement.dataset.theme = 'dark'")
            page.screenshot(path='cache/translation-reading-dark.png')
            page.set_viewport_size({'width': 760, 'height': 900})
            expect(left).to_be_visible(); expect(right).to_be_visible()
            assert left.bounding_box()['x'] < right.bounding_box()['x']
            page.get_by_role('button', name='Theo từng câu', exact=True).click()
            expect(page.locator('.translation-grid')).to_be_visible()
            assert not errors, errors
            browser.close()
            print('Parallel reading: realtime source order, batches, live preview, scroll on/off, saved settings/reload, dark/narrow layout passed.')
    finally:
        server.should_exit = True; thread.join(10); fixture.tearDown()


if __name__ == '__main__':
    main()

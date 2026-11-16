"""Download/import a complete conversation through Chrome using temporary data only."""
from pathlib import Path
import sys
import threading
import time
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from test_conversation_archive import ConversationArchiveTests
from web_api.server import create_app


def main():
    fixture = ConversationArchiveTests(); fixture.setUp()
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
            page = browser.new_page(viewport={'width':1440, 'height':1000}, accept_downloads=True)
            errors = []; page.on('pageerror', lambda e: errors.append(str(e)))
            page.route('**/api/chrome-tabs', lambda route: route.fulfill(json=[]))
            page.goto(f'http://127.0.0.1:{port}')
            expect(page.get_by_role('button', name='Export hội thoại', exact=True)).to_be_enabled()
            with page.expect_download() as download:
                page.get_by_role('button', name='Export hội thoại', exact=True).click()
            package = fixture.root / download.value.suggested_filename
            download.value.save_as(package)
            assert package.name.endswith('.rtc-conversation.zip')
            with zipfile.ZipFile(package) as archive:
                assert 'manifest.json' in archive.namelist()
                assert 'files/attachments/ảnh.png' in archive.namelist()
            # Exercise the actual Import button and native file-picker hook.
            with page.expect_file_chooser() as chooser:
                page.get_by_role('button', name='Import hội thoại', exact=True).click()
            chooser.value.set_files(str(package))
            expect(page.locator('.conversation-list .conversation')).to_have_count(2)
            expect(page.get_by_text('Đã import bản sao hội thoại: Shared lesson', exact=True)).to_be_visible()
            new_cid = page.evaluate("localStorage.getItem('rtc-conversation')")
            assert new_cid != fixture.cid
            expect(page.locator('.transcript')).to_contain_text('Second part')
            page.locator('nav button').nth(1).click()
            expect(page.locator('.chat-scroll')).to_contain_text('Alpha concept')
            expect(page.locator('.attachments .attachment-card')).to_have_count(1)
            page.locator('nav button').nth(2).click()
            page.wait_for_function("CSS.highlights.get('rtc-branch-marks')?.size === 1")
            expect(page.locator('.research-answer')).to_contain_text('Alpha concept')
            page.get_by_label('Hiện node đã ẩn (2)', exact=True).check()
            page.get_by_label('Tìm node', exact=True).fill('Deeper')
            page.locator('.research-search-results button').first.click()
            expect(page.locator('.research-detail')).to_contain_text('Đã nhập từ thiết bị khác')
            assert not fixture.service.research.active
            # Invalid uploads keep the current selection and existing data intact.
            page.get_by_label('Chọn file hội thoại để import', exact=True).set_input_files({
                'name':'broken.zip', 'mimeType':'application/zip', 'buffer':b'broken'})
            expect(page.get_by_text('Không import được gói hội thoại:', exact=False)).to_be_visible()
            expect(page.locator('.conversation-list .conversation')).to_have_count(2)
            assert page.evaluate("localStorage.getItem('rtc-conversation')") == new_cid
            # Reload proves both the new conversation and its research metadata are durable.
            page.reload()
            expect(page.locator('.conversation-list .conversation')).to_have_count(2)
            assert len(fixture.research.list(new_cid)) == 3
            assert len(fixture.store.messages(fixture.cid)) == 4
            page.evaluate("document.documentElement.dataset.theme = 'dark'")
            page.wait_for_function("getComputedStyle(document.querySelector('.conversation-transfer button')).backgroundColor === 'rgb(34, 42, 37)'")
            page.screenshot(path='cache/conversation-transfer.png')
            page.set_viewport_size({'width':390, 'height':844})
            expect(page.get_by_role('button', name='Export hội thoại', exact=True)).to_be_in_viewport()
            expect(page.get_by_role('button', name='Import hội thoại', exact=True)).to_be_in_viewport()
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            assert not errors, errors
            browser.close()
            print('Conversation transfer browser checks passed: ZIP download, file-picker import, selected copy, chat/marks/hidden nodes, unfinished jobs, errors, reload and dark controls.')
    finally:
        server.should_exit = True; thread.join(timeout=10); fixture.tearDown()


if __name__ == '__main__': main()

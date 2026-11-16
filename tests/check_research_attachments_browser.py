"""Preview/download node snapshots after cache loss, reload, and portable import."""
from pathlib import Path
import re
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from PIL import Image
from playwright.sync_api import sync_playwright, expect
from test_web_api import WebApiTests
from web_api.server import create_app


def main():
    fixture = WebApiTests(); fixture.setUp()
    fixture.service.busy = True
    store = fixture.service.research.store
    folder = fixture.service.store.get(fixture.cid).folder
    photo, note = folder / 'diagram.png', folder / 'note.txt'
    Image.new('RGB', (1600, 900), '#237c62').save(photo)
    note.write_bytes(b'Saved question context')
    fixture.service.store.add_message(fixture.cid, 'user', 'You', 'Root question', files=(photo, note))
    mid = fixture.service.store.add_message(fixture.cid, 'bot', 'ChatGPT', 'Root answer')
    root = store.import_answer(fixture.cid, mid)
    child = fixture.service.research.children(fixture.cid, root['id'], {'questions':['Child with files'],
        'files':['diagram.png', 'note.txt'], 'config':{'selected_tab':'fake'}})[0]
    store.update(fixture.cid, child['id'], status='done', answer='Child answer with $x^2$.')
    empty = fixture.service.research.children(fixture.cid, root['id'], {'questions':['Child without files'],
        'config':{'selected_tab':'fake'}})[0]
    store.update(fixture.cid, empty['id'], status='done', answer='No attachments here.')
    package = fixture.client.get(f'/api/conversations/{fixture.cid}/export')
    assert package.status_code == 200
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
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            page.route('**/api/chrome-tabs', lambda route: route.fulfill(json=[]))
            page.goto(f'http://127.0.0.1:{port}')
            page.locator('nav button').nth(2).click()
            attachments = page.locator('.research-attachments')
            expect(attachments).to_contain_text('ĐÍNH KÈM KHI HỎI (2)')
            expect(attachments.locator('img')).to_have_count(1)
            page.wait_for_function("document.querySelector('.research-attachments img')?.naturalWidth === 1600")
            def select(title):
                page.get_by_label('Tìm node', exact=True).fill(title)
                page.locator('.research-search-results button').first.click()
                page.get_by_label('Tìm node', exact=True).fill('')
            # Delete all disposable files; the child's SQL snapshot is still authoritative.
            request = store.get(fixture.cid, child['id'], private=True)['request']
            photo.unlink(); note.unlink()
            for value in request['attachments']:
                path = Path(value); assert path.resolve().is_relative_to(fixture.root.resolve()); path.unlink()
            select('Child with files')
            expect(attachments).to_contain_text('ĐÍNH KÈM KHI HỎI (2)')
            page.wait_for_function("document.querySelector('.research-attachments img')?.naturalWidth === 1600")
            attachments.get_by_role('button', name=re.compile('^Xem ảnh ')).click()
            viewer = page.get_by_role('dialog', name='Xem ảnh đính kèm', exact=True)
            expect(viewer).to_be_visible()
            page.wait_for_function("document.querySelector('.image-viewer img')?.naturalWidth === 1600")
            viewer.get_by_role('button', name='Kích thước gốc', exact=True).click()
            assert viewer.locator('img').evaluate('el => el.width') == 1600
            page.keyboard.press('Escape')
            expect(viewer).to_have_count(0)
            with page.expect_download() as download:
                attachments.get_by_role('link', name=re.compile('note.txt')).click()
            assert Path(download.value.path()).read_bytes() == b'Saved question context'
            select('Child without files')
            expect(attachments).to_have_count(0)
            select('Child with files')
            page.reload(); page.locator('nav button').nth(2).click()
            expect(attachments).to_contain_text('ĐÍNH KÈM KHI HỎI (2)')
            page.wait_for_function("document.querySelector('.research-attachments img')?.naturalWidth === 1600")
            # Import the previously exported conversation: URLs must use the new node and conversation IDs.
            imported = fixture.client.post('/api/conversations/import', headers=fixture.headers,
                files={'file':('conversation.zip', package.content, 'application/zip')})
            assert imported.status_code == 200, imported.text
            cid = imported.json()['id']
            page.evaluate("id => localStorage.setItem('rtc-conversation', id)", cid)
            page.reload(); page.locator('nav button').nth(2).click()
            expect(attachments).to_contain_text('ĐÍNH KÈM KHI HỎI (2)')  # Imported root's Chat files.
            select('Child with files')
            page.wait_for_function("document.querySelector('.research-attachments img')?.naturalWidth === 1600")
            assert f'/conversations/{cid}/research/' in attachments.locator('img').get_attribute('src')
            page.screenshot(path='cache/research-node-attachments.png')
            assert not errors, errors
            browser.close()
            print('Node attachments browser checks passed: Chat root files, frozen images and download after cache loss, full-size viewer, node switching, reload and imported snapshots.')
    finally:
        server.should_exit = True; thread.join(timeout=10); fixture.tearDown()


if __name__ == '__main__': main()

"""Real Win32 + isolated Chrome integration; ChatGPT calls are all mocked."""
import ctypes
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from test_realtime_chat import RealtimeChatTests
from web_api.server import create_app


def main():
    fixture = RealtimeChatTests(); fixture.setUp()
    server = uvicorn.Server(uvicorn.Config(create_app(fixture.service), host='127.0.0.1', port=0, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError('Test server did not start')
        time.sleep(.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0)); chrome_port = listener.getsockname()[1]
    try:
        with tempfile.TemporaryDirectory() as profile, sync_playwright() as p, patch('caption.chatgpt.CDP_URL', f'http://127.0.0.1:{chrome_port}'):
            context = p.chromium.launch_persistent_context(profile, channel='chrome', headless=False,
                args=[f'--remote-debugging-port={chrome_port}', '--no-first-run'], viewport={'width':1100,'height':760})
            errors = []
            context.on('page', lambda page: page.on('pageerror', lambda error: errors.append(str(error))))
            page = context.pages[0]
            page.goto(f'http://127.0.0.1:{port}')
            page.get_by_role('button', name='Chat realtime', exact=True).click()
            with context.expect_page() as opened:
                page.get_by_role('button', name='Popup Chat realtime', exact=True).click()
            popup_window = opened.value
            popup = popup_window.frame_locator('iframe')
            expect(popup.locator('.realtime-popup')).to_be_visible(timeout=20000)
            expect(popup.get_by_label('Độ trong suốt popup', exact=True)).to_be_enabled(timeout=20000)
            expect(popup.locator('.chat-settings')).to_have_count(0)
            expect(popup.get_by_role('button', name='Lưu cài đặt', exact=True)).to_have_count(0)
            expect(popup.get_by_role('button', name='Popup Chat realtime', exact=True)).to_have_count(0)
            windows = fixture.service.realtime_popups
            hwnd = windows.windows[fixture.cid]['hwnd']
            native = windows.native
            deadline = time.monotonic() + 3
            while not native.api.GetWindowLongPtrW(hwnd, -20) & 8 and time.monotonic() < deadline:
                popup_window.wait_for_timeout(100)
            assert native.api.GetWindowLongPtrW(hwnd, -20) & 8, 'Window is not topmost'
            slider = popup.get_by_label('Độ trong suốt popup', exact=True)
            slider.fill('35'); slider.dispatch_event('input')
            expect(popup.locator('.rt-opacity')).to_contain_text('35%')
            popup_window.wait_for_timeout(700)
            assert windows.opacity() == 65
            from ctypes import wintypes as w
            get_alpha = native.api.GetLayeredWindowAttributes
            get_alpha.argtypes = [w.HWND, ctypes.POINTER(w.DWORD), ctypes.POINTER(w.BYTE), ctypes.POINTER(w.DWORD)]
            get_alpha.restype = w.BOOL
            color, alpha, flags = w.DWORD(), w.BYTE(), w.DWORD()
            assert get_alpha(hwnd, ctypes.byref(color), ctypes.byref(alpha), ctypes.byref(flags))
            assert alpha.value == round(255 * .65), alpha.value
            # Switching to another window must not remove the always-on-top style.
            page.bring_to_front()
            assert native.api.GetWindowLongPtrW(hwnd, -20) & 8
            popup_window.bring_to_front()
            popup.get_by_role('button', name='Start', exact=True).click()
            expect(popup.get_by_role('button', name='Explain', exact=True)).to_be_visible(timeout=10000)
            expect(popup.locator('.rt-summary')).to_have_count(1)
            font = popup.get_by_label('Cỡ chữ popup', exact=True)
            font.fill('24'); font.dispatch_event('input')
            expect(popup.locator('.rt-summary .response-markdown')).to_have_css('font-size', '24px')
            expect(popup.get_by_role('button', name='Explain', exact=True)).to_have_css('font-size', '24px')
            expect(popup.get_by_label('Câu hỏi Chat realtime', exact=True)).to_have_css('font-size', '24px')
            popup.get_by_role('button', name='Explain', exact=True).click()
            expect(popup.locator('.chat-scroll .message.bot')).to_contain_text('Answer to the question')
            expect(popup.locator('.chat-scroll .response-markdown').last).to_have_css('font-size', '24px')
            assert windows.font_size() == 24
            assert windows.opacity() == 65
            popup.get_by_label('Câu hỏi Chat realtime', exact=True).fill('Question from popup')
            popup.get_by_role('button', name='Send', exact=True).click()
            expect(popup.locator('.chat-scroll .message.bot')).to_have_count(2)
            expect(popup.locator('.chat-scroll .message.bot').last).to_contain_text('Answer to the question')
            expect(page.locator('.realtime-chat .chat-scroll .message.bot')).to_have_count(2)
            assert fixture.rt.state(fixture.cid)['running']
            page.get_by_role('button', name='Popup Chat realtime', exact=True).click()
            assert len(context.pages) == 2, 'Open created a duplicate popup'
            slider.fill('0'); slider.dispatch_event('input'); popup_window.wait_for_timeout(400)
            popup_window.screenshot(path='cache/realtime-popup.png')
            popup.locator("html").evaluate("root => root.dataset.theme='dark'")
            popup_window.wait_for_timeout(250)
            popup_window.screenshot(path='cache/realtime-popup-dark.png')
            with popup_window.expect_event('close'):
                popup.get_by_role('button', name='Đóng popup Chat realtime', exact=True).click()
            assert fixture.rt.state(fixture.cid)['running'], 'Closing popup stopped realtime'
            with context.expect_page() as reopened:
                page.get_by_role('button', name='Popup Chat realtime', exact=True).click()
            restored = reopened.value.frame_locator('iframe')
            expect(restored.get_by_label('Cỡ chữ popup', exact=True)).to_have_value('24')
            expect(restored.locator('.rt-summary .response-markdown')).to_have_css('font-size', '24px')
            with reopened.value.expect_event('close'):
                restored.get_by_role('button', name='Đóng popup Chat realtime', exact=True).click()
            page.locator('.realtime-chat').get_by_role('button', name='Stop', exact=True).click()
            expect(page.locator('.realtime-chat').get_by_role('button', name='Start', exact=True)).to_be_enabled()
            assert not errors, errors
            context.close()
            print('Popup passed: real Windows topmost/opacity, no settings, actions, chat, summary, shared history, reuse and close without stopping.')
    finally:
        server.should_exit = True; thread.join(timeout=10); fixture.tearDown()


if __name__ == '__main__':
    main()

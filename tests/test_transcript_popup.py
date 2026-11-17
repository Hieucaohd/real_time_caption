import io
import os
import unittest
from unittest.mock import patch

import test_web_api as fixtures
from caption.settings import Settings


class PopupProcess:
    def __init__(self):
        self.stdin = io.BytesIO()
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        self.returncode = 0
        return 0


class PopupApiTests(unittest.TestCase):
    setUp = fixtures.WebApiTests.setUp
    tearDown = fixtures.WebApiTests.tearDown
    post = fixtures.WebApiTests.post

    def source(self, value):
        return self.client.patch('/api/settings', headers=self.headers, json={'web_capture_source': value})

    def test_popup_is_chatgpt_only_and_toggle_state_tracks_close(self):
        with patch('web_api.server.subprocess.Popen', side_effect=lambda *a, **k: PopupProcess()) as spawn:
            self.assertEqual(self.client.post('/api/overlay').status_code, 403)
            self.assertEqual(self.post('/api/overlay').status_code, 400)
            spawn.assert_not_called()
            self.source('chatgpt')
            self.assertTrue(self.post('/api/overlay').json()['visible'])
            self.assertTrue(self.client.get('/api/bootstrap').json()['overlay_visible'])
            process = self.service.overlay_process
            self.assertFalse(self.post('/api/overlay').json()['visible'])
            self.assertTrue(process.stdin.closed)
            self.assertFalse(self.client.get('/api/bootstrap').json()['overlay_visible'])
            self.assertTrue(self.post('/api/overlay').json()['visible'])
            self.assertEqual(spawn.call_count, 2)
            self.service.overlay_process.returncode = 0  # User closes the native popup.
            self.assertFalse(self.client.get('/api/bootstrap').json()['overlay_visible'])

    def test_switching_back_to_windows_closes_popup(self):
        with patch('web_api.server.subprocess.Popen', return_value=PopupProcess()):
            self.source('chatgpt')
            self.post('/api/overlay')
            process = self.service.overlay_process
            self.assertEqual(self.source('windows').status_code, 200)
            self.assertTrue(process.stdin.closed)
            self.assertFalse(self.service.state()['overlay_visible'])


@unittest.skipUnless(os.name == 'nt', 'Windows overlay test')
class NativePopupTests(unittest.TestCase):
    def test_popup_remains_topmost_without_taking_focus_from_another_window(self):
        import ctypes
        import tkinter as tk
        from caption.overlay import CaptionOverlay, _user32, GWL_EXSTYLE, WS_EX_NOACTIVATE
        root = tk.Tk(); root.withdraw()
        previous = _user32.GetForegroundWindow()
        other = None
        try:
            overlay = CaptionOverlay(root, Settings(overlay_width=480, overlay_x=20, overlay_y=20), clear_after=None)
            overlay.show(); overlay.add_final('Saved transcript'); overlay.set_partial('Live words')
            other = tk.Toplevel(root); other.title('Popup verification'); other.geometry('480x120+20+20')
            root.update(); other.lift(); other.focus_force(); root.update()
            foreground = _user32.GetForegroundWindow()
            hwnd = overlay._hwnd()
            with patch('caption.overlay.time.monotonic', return_value=overlay._last_update + 60):
                overlay.tick()
            root.update()
            style = _user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            self.assertTrue(style & 0x8, 'WS_EX_TOPMOST must remain set')
            self.assertTrue(style & WS_EX_NOACTIVATE)
            self.assertEqual(_user32.GetForegroundWindow(), foreground, 'Raising popup must not steal keyboard focus')
            self.assertTrue(overlay.win.winfo_viewable())
            self.assertEqual(overlay._committed, 'Saved transcript')
            self.assertEqual(overlay._partial, 'Live words')
        finally:
            # Restore the previous app only if focus is still on our test window.
            if other and other.winfo_exists() and _user32.GetForegroundWindow() == int(other.wm_frame(), 16):
                _user32.SetForegroundWindow(ctypes.c_void_p(previous))
            root.destroy()
            # Collect Tk menu/variable cycles on the thread that owns Tcl, before
            # the API tests start their worker threads.
            del overlay, other, root
            import gc
            gc.collect()


if __name__ == '__main__':
    unittest.main()

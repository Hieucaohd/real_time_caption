"""The web app uses the same topmost, draggable Windows overlay as desktop."""
import json
import queue
import sys
import threading
import time
import tkinter as tk
import urllib.request
from dataclasses import asdict

from caption.overlay import CaptionOverlay
from caption.settings import Settings


def main(url=None, parent_pid=None):
    url = url or sys.argv[1]
    root = tk.Tk()
    root.withdraw()
    settings = Settings.load()
    overlay = CaptionOverlay(root, settings)
    overlay.on_hidden = root.destroy
    overlay.show()
    events, stop = queue.Queue(maxsize=2), threading.Event()

    def poll():
        while not stop.wait(.3):
            if parent_pid:
                from caption.shared_access import process_alive
                if not process_alive(parent_pid):
                    stop.set()
                    break
            try:
                with urllib.request.urlopen(url.rstrip('/') + '/api/capture/state', timeout=2) as response:
                    data = json.load(response)
                if not events.full():
                    events.put(data)
            except OSError:
                stop.set()

    threading.Thread(target=poll, daemon=True).start()
    def wait_close():
        sys.stdin.read()
        stop.set()
    if sys.stdin is not None:
        threading.Thread(target=wait_close, daemon=True).start()
    last, partial, checked = -1, '', 0
    def tick():
        nonlocal last, partial, checked
        if stop.is_set():
            root.destroy()
            return
        try:
            while True:
                state = events.get_nowait()
                if state['final_revision'] != last:
                    last = state['final_revision']
                    if state['last_final']:
                        overlay.add_final(state['last_final'])
                if state['capture']['partial'] != partial:
                    partial = state['capture']['partial']
                    overlay.set_partial(partial)
        except queue.Empty:
            pass
        if time.monotonic() - checked >= 1:
            checked = time.monotonic()
            # Merge locally dragged/resized overlay changes, then apply web settings.
            if asdict(settings) != getattr(settings, '_baseline', {}):
                settings.save()
            latest = Settings.load()
            changed = False
            for key, value in asdict(latest).items():
                if key.startswith('overlay_') and getattr(settings, key) != value:
                    changed = True
                setattr(settings, key, value)
            settings._baseline = asdict(latest)
            if changed:
                overlay.font.configure(size=settings.overlay_font_size)
                overlay.transparent_var.set(settings.overlay_transparent)
                overlay._apply_mode()
                overlay._apply_geometry()
        overlay.tick()
        root.after(100, tick)
    tick()
    try:
        root.mainloop()
    finally:
        stop.set()
        settings.save()


if __name__ == '__main__':
    main()

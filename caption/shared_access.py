"""Small OS-backed locks shared by the desktop and local web processes."""
from __future__ import annotations

import os
import time
from pathlib import Path


class BusyError(OSError):
    pass


class ProcessLock:
    def __init__(self, path: Path, timeout: float = 0):
        self.path, self.timeout, self.stream = Path(path), timeout, None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open('a+b')
        if not self.path.stat().st_size:
            stream.write(b'0')
            stream.flush()
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                stream.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.stream = stream
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    stream.close()
                    raise BusyError('Another desktop/web operation is using this resource. Pause it and try again.') from None
                time.sleep(0.05)

    def release(self):
        if self.stream:
            self.stream.close()  # The OS also releases the lock if the process crashes.
            self.stream = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *_):
        self.release()


def capture_lock(db_path):
    return ProcessLock(Path(db_path).parent / 'cache' / 'capture.lock')


def process_alive(pid):
    if not pid:
        return False
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() == 5
        code = wintypes.DWORD()
        try:
            return bool(kernel.GetExitCodeProcess(wintypes.HANDLE(handle), ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(wintypes.HANDLE(handle))
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True

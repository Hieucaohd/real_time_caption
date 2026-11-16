"""Control Live Captions through its accessible controls, never screen coordinates."""
from contextlib import contextmanager
import ctypes
import logging
import os
from pathlib import Path
import subprocess
import time

from .shared_access import ProcessLock

WINDOW_CLASS = 'LiveCaptionsDesktopWindow'
log = logging.getLogger(__name__)
# Read-only window/language fingerprint invalidates this cache after an external
# language change or Windows restart. AppSessionId is absent on some builds.
_language_cache = None


def window_session():
    """Identify the running window without activating it or opening any menu."""
    if os.name != 'nt':
        return None
    user32 = ctypes.windll.user32
    user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    handle = user32.FindWindowW(WINDOW_CLASS, None)
    if not handle:
        return None
    process_id = ctypes.c_ulong()
    user32.GetWindowThreadProcessId(handle, ctypes.byref(process_id))
    return (handle, process_id.value) if process_id.value else None


def language_fingerprint():
    try:
        import winreg
        session = window_session()
        if not session:
            return None
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Microsoft\LiveCaptions\UI') as key:
            language = winreg.QueryValueEx(key, 'CaptionLanguage')[0]
        return session, language
    except (ImportError, OSError):
        return None


class CaptionsSettingsError(ValueError):
    pass


class WindowsControls:
    def __init__(self, auto):
        self.auto = auto
        self.window = auto.WindowControl(searchDepth=1, ClassName=WINDOW_CLASS)
        self.selection = None

    def control(self, kind, identity, timeout=3):
        control = getattr(self.window, kind)(AutomationId=identity)
        if not control.Exists(timeout):
            raise CaptionsSettingsError(f'Không tìm thấy điều khiển Live Captions: {identity}. '
                                        'Hãy hoàn tất thiết lập trong cửa sổ Windows rồi thử lại.')
        return control

    def exists(self, kind, identity):
        return getattr(self.window, kind)(AutomationId=identity).Exists(.15)

    def open(self):
        if not self.window.Exists(.2):
            subprocess.Popen([os.path.join(os.environ.get('SystemRoot', r'C:\Windows'), 'System32', 'LiveCaptions.exe')])
        if not self.window.Exists(15):
            raise CaptionsSettingsError('Không mở được Windows Live Captions. Hãy thử Win + Ctrl + L.')
        # XAML flyouts dismiss immediately when their owning window is inactive.
        # Activate once for the whole operation; restore the caller in finally.
        self.window.SetActive(waitTime=.2)

    def invoke(self, kind, identity):
        self.control(kind, identity).GetInvokePattern().Invoke()
        time.sleep(.25)  # XAML menus animate asynchronously after Invoke returns.

    def menu(self):
        if not self.exists('MenuItemControl', 'PreferencesButton'):
            self.control('ButtonControl', 'SettingsButton').SetFocus()
            self.invoke('ButtonControl', 'SettingsButton')
        self.control('MenuItemControl', 'PreferencesButton', timeout=5)

    def dismiss_menu(self):
        if self.exists('MenuItemControl', 'PreferencesButton'):
            pref = self.control('MenuItemControl', 'PreferencesButton')
            pref.GetExpandCollapsePattern().Collapse()
            # Some Windows builds dismiss the whole flyout on Collapse. Clicking
            # Settings in that case would open it again instead of cleaning up.
            if self.exists('MenuItemControl', 'PreferencesButton'):
                self.invoke('ButtonControl', 'SettingsButton')

    def microphone(self, desired=None):
        self.menu()
        pref = self.control('MenuItemControl', 'PreferencesButton')
        pref.GetExpandCollapsePattern().Expand()
        mic = self.control('MenuItemControl', 'MicrophoneMenuFlyoutItem')
        pattern = mic.GetTogglePattern()
        current = pattern.ToggleState
        if current not in (0, 1):
            raise CaptionsSettingsError('Không đọc được trạng thái micro của Live Captions.')
        if desired is not None and bool(current) != desired:
            pattern.Toggle()
            # TogglePattern normally keeps the menu alive; read it in place.
            time.sleep(.25)
            if not self.exists('MenuItemControl', 'MicrophoneMenuFlyoutItem'):
                self.menu()
                self.control('MenuItemControl', 'PreferencesButton').GetExpandCollapsePattern().Expand()
            current = self.control('MenuItemControl', 'MicrophoneMenuFlyoutItem').GetTogglePattern().ToggleState
            if current != int(desired):
                raise CaptionsSettingsError('Windows chưa áp dụng tùy chọn micro. Hãy kiểm tra quyền truy cập micro.')
        try:
            self.dismiss_menu()
        except Exception:
            # The toggle was read back successfully above. A disappearing flyout
            # during cleanup must not turn that verified result into a failure.
            log.debug('Live Captions menu already changed during cleanup', exc_info=True)
        return bool(current)

    def language_picker(self):
        if not self.exists('ComboBoxControl', 'SpeechModelDropDown'):
            self.menu()
            self.invoke('MenuItemControl', 'ChangeLanguageMenuFlyoutItem')
        return self.control('ComboBoxControl', 'SpeechModelDropDown')

    @staticmethod
    def items(combo):
        return [item for item in combo.GetChildren() if item.ControlTypeName == 'ListItemControl']

    def languages(self, refresh=False):
        fingerprint = language_fingerprint()
        if not refresh and fingerprint and _language_cache and _language_cache[0] == fingerprint:
            if not self.exists('ComboBoxControl', 'SpeechModelDropDown'):
                return _language_cache[1], list(_language_cache[2])
        combo = self.language_picker()
        current = next((item.Name for item in self.items(combo) if item.GetSelectionItemPattern().IsSelected), '')
        combo.GetExpandCollapsePattern().Expand()
        time.sleep(.25)
        names = list(dict.fromkeys(item.Name for item in self.items(combo)))
        combo.GetExpandCollapsePattern().Collapse()
        if not current or not names:
            raise CaptionsSettingsError('Không đọc được danh sách ngôn ngữ Live Captions.')
        self.selection = (current, names)
        return current, names

    def select_language(self, name):
        combo = self.language_picker()
        combo.GetExpandCollapsePattern().Expand()
        time.sleep(.25)
        items = self.items(combo)
        item = next((item for item in items if item.Name == name), None)
        if item is None:
            combo.GetExpandCollapsePattern().Collapse()
            raise CaptionsSettingsError('Ngôn ngữ đã chọn không có trên Windows này. Hãy tải lại danh sách.')
        item.GetSelectionItemPattern().Select()
        if not item.GetSelectionItemPattern().IsSelected:
            raise CaptionsSettingsError('Windows chưa chọn ngôn ngữ được yêu cầu.')
        self.selection = (name, list(dict.fromkeys(item.Name for item in items)))
        combo.GetExpandCollapsePattern().Collapse()

    def continue_language(self):
        global _language_cache
        if not self.exists('ComboBoxControl', 'SpeechModelDropDown'):
            return self.caption_ready()
        self.invoke('ButtonControl', 'ContinueButton')
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            # Ready/caption controls only reappear after the chosen model is ready.
            if self.caption_ready():
                fingerprint = language_fingerprint()
                if self.selection and fingerprint:
                    _language_cache = (fingerprint, *self.selection)
                time.sleep(.5)
                return True
            time.sleep(.2)
        return False

    def caption_ready(self):
        return (not self.exists('ComboBoxControl', 'SpeechModelDropDown') and
                (self.exists('TextControl', 'ReadyToCaptionTextBlock') or
                 self.exists('PaneControl', 'CaptionsScrollViewer')))


@contextmanager
def windows_controls(db_path):
    if os.name != 'nt':
        raise CaptionsSettingsError('Tính năng này cần Windows 11 có Live Captions.')
    import uiautomation as auto
    from comtypes import COMError
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    user32.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    previous = user32.GetForegroundWindow()
    with ProcessLock(Path(db_path).parent / 'cache' / 'livecaptions-settings.lock', timeout=2):
        with auto.UIAutomationInitializerInThread():
            controls = WindowsControls(auto)
            try:
                controls.open()
                yield controls
            except (LookupError, OSError, COMError, AttributeError) as exc:
                raise CaptionsSettingsError('Không điều khiển được Live Captions. Hãy kiểm tra cửa sổ Windows rồi thử lại.') from exc
            finally:
                try:
                    controls.dismiss_menu()
                except Exception:
                    pass
                if previous:
                    user32.SetForegroundWindow(previous)


def inspect(controls):
    microphone = controls.microphone()
    language, languages = controls.languages(refresh=True)
    ready = controls.continue_language()
    return dict(microphone=microphone, language=language, languages=languages,
                ready=ready, status='applied' if ready else 'setup_required')


def apply(controls, preferences):
    current, languages = controls.languages()
    target = preferences['language'] or current
    if target not in languages:
        controls.continue_language()  # Leave the previous language running.
        raise CaptionsSettingsError('Ngôn ngữ đã lưu không có trên Windows này. Hãy tải lại danh sách và chọn lại.')
    if target != current:
        controls.select_language(target)
    if not controls.continue_language():
        return dict(language=target, languages=languages, microphone=None, ready=False, status='setup_required')
    # SelectionItem confirms the choice before Continue; readiness confirms the
    # transition. Reopening Change language here resets captioning a second time.
    microphone = controls.microphone(preferences['microphone'])
    return dict(language=target, languages=languages, microphone=microphone, ready=True, status='applied')


def read_windows(store):
    with windows_controls(store.db_path) as controls:
        return inspect(controls)


def apply_windows(store, preferences, *, save=False):
    with windows_controls(store.db_path) as controls:
        result = apply(controls, preferences)
        if save:
            store.save_livecaptions_preferences(preferences)
        return result

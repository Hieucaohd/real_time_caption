"""Control Live Captions through its accessible controls, never screen coordinates."""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import subprocess
import time

from .shared_access import ProcessLock

WINDOW_CLASS = 'LiveCaptionsDesktopWindow'


class CaptionsSettingsError(ValueError):
    pass


class WindowsControls:
    def __init__(self, auto):
        self.auto = auto
        self.window = auto.WindowControl(searchDepth=1, ClassName=WINDOW_CLASS)

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

    def invoke(self, kind, identity):
        self.control(kind, identity).GetInvokePattern().Invoke()
        time.sleep(.25)  # XAML menus animate asynchronously after Invoke returns.

    def menu(self):
        for _ in range(3):
            if self.exists('MenuItemControl', 'PreferencesButton'):
                return
            self.invoke('ButtonControl', 'SettingsButton')
            if self.window.MenuItemControl(AutomationId='PreferencesButton').Exists(1):
                return
        self.control('MenuItemControl', 'PreferencesButton')

    def dismiss_menu(self):
        if self.exists('MenuItemControl', 'PreferencesButton'):
            pref = self.control('MenuItemControl', 'PreferencesButton')
            pref.GetExpandCollapsePattern().Collapse()
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
            time.sleep(.3)
            # Reopen the menu if toggling dismissed it, then verify actual state.
            self.menu()
            self.control('MenuItemControl', 'PreferencesButton').GetExpandCollapsePattern().Expand()
            current = self.control('MenuItemControl', 'MicrophoneMenuFlyoutItem').GetTogglePattern().ToggleState
            if current != int(desired):
                raise CaptionsSettingsError('Windows chưa áp dụng tùy chọn micro. Hãy kiểm tra quyền truy cập micro.')
        self.dismiss_menu()
        return bool(current)

    def language_picker(self):
        if not self.exists('ComboBoxControl', 'SpeechModelDropDown'):
            self.menu()
            self.invoke('MenuItemControl', 'ChangeLanguageMenuFlyoutItem')
        return self.control('ComboBoxControl', 'SpeechModelDropDown')

    @staticmethod
    def items(combo):
        return [item for item in combo.GetChildren() if item.ControlTypeName == 'ListItemControl']

    def languages(self):
        combo = self.language_picker()
        current = next((item.Name for item in self.items(combo) if item.GetSelectionItemPattern().IsSelected), '')
        combo.GetExpandCollapsePattern().Expand()
        time.sleep(.25)
        names = list(dict.fromkeys(item.Name for item in self.items(combo)))
        combo.GetExpandCollapsePattern().Collapse()
        if not current or not names:
            raise CaptionsSettingsError('Không đọc được danh sách ngôn ngữ Live Captions.')
        return current, names

    def select_language(self, name):
        combo = self.language_picker()
        combo.GetExpandCollapsePattern().Expand()
        time.sleep(.25)
        item = next((item for item in self.items(combo) if item.Name == name), None)
        if item is None:
            combo.GetExpandCollapsePattern().Collapse()
            raise CaptionsSettingsError('Ngôn ngữ đã chọn không có trên Windows này. Hãy tải lại danh sách.')
        item.GetSelectionItemPattern().Select()
        combo.GetExpandCollapsePattern().Collapse()

    def continue_language(self):
        self.invoke('ButtonControl', 'ContinueButton')
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            # Ready/caption controls only reappear after the chosen model is ready.
            if (not self.exists('ComboBoxControl', 'SpeechModelDropDown') and
                    (self.exists('TextControl', 'ReadyToCaptionTextBlock') or
                     self.exists('PaneControl', 'CaptionsScrollViewer'))):
                time.sleep(.5)
                return True
            time.sleep(.2)
        return False


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
    language, languages = controls.languages()
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
    # Read the selected language back from Windows; do not trust a successful click alone.
    actual, languages = controls.languages()
    ready = controls.continue_language()
    if actual != target or not ready:
        raise CaptionsSettingsError('Chưa xác nhận được ngôn ngữ Live Captions đã áp dụng.')
    microphone = controls.microphone(preferences['microphone'])
    return dict(language=actual, languages=languages, microphone=microphone, ready=True, status='applied')


def read_windows(store):
    with windows_controls(store.db_path) as controls:
        return inspect(controls)


def apply_windows(store, preferences, *, save=False):
    with windows_controls(store.db_path) as controls:
        result = apply(controls, preferences)
        if save:
            store.save_livecaptions_preferences(preferences)
        return result


def apply_saved(store):
    preferences = store.livecaptions_preferences()
    if preferences['microphone'] is None and not preferences['language']:
        return
    result = apply_windows(store, preferences)
    if not result['ready']:
        raise CaptionsSettingsError('Đã chọn ngôn ngữ; hãy hoàn tất tải gói ngôn ngữ/thiết lập trong Windows Live Captions, rồi bấm Start / Continue lại.')

from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from caption import google_translate as web
from caption import translation
from caption.settings import Settings
from caption.translation_tab import FIELDS, NOTES


URL = "https://translate.google.com/?sl=en&tl=vi&op=translate&hl=en"


class Clock:
    def __init__(self):
        self.now = 0

    def monotonic(self):
        return self.now

    def pause(self, cancel, seconds=0.15):
        self.now += seconds
        web._check_cancel(cancel)


def tab(url=URL, *, pinned=False, focused=False, visible=False):
    page = Mock(url=url)
    page.evaluate.return_value = {"pinned": pinned, "focused": focused, "visible": visible}
    return page


class GoogleTranslateTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.clock_patch = patch.object(web.time, "monotonic", self.clock.monotonic)
        self.pause_patch = patch.object(web, "_pause", self.clock.pause)
        self.clock_patch.start()
        self.pause_patch.start()
        self.addCleanup(self.clock_patch.stop)
        self.addCleanup(self.pause_patch.stop)

    def page(self):
        page = Mock(url=URL)
        source, copy = Mock(), Mock()
        value = {"text": "old input"}
        source.fill.side_effect = lambda text, **kwargs: value.update(text=text)
        source.input_value.side_effect = lambda **kwargs: value["text"]
        copy.count.return_value = 1
        copy.is_enabled.return_value = True
        visible = patch.object(web, "_visible", side_effect=lambda p, selector: SimpleNamespace(first=source if selector == web.SOURCE else copy))
        visible.start()
        self.addCleanup(visible.stop)
        return page, source, copy

    def test_engine_has_separate_ui_and_needs_no_api_key(self):
        settings = Settings(translation_engine="google_web")
        config = translation.Config.from_settings(settings)
        config.validate()
        self.assertEqual(config.model, "web")
        self.assertIn("google_web", FIELDS)
        self.assertIn("google_web", NOTES)
        event = threading.Event()
        with patch.object(web, "translate", return_value="translated") as translate, patch.object(translation, "_post") as api:
            self.assertEqual(translation.Translator().translate(" Hello ", config, event), "translated")
        translate.assert_called_once_with("Hello", "en", "vi", event)
        api.assert_not_called()
        self.assertNotEqual(config.fingerprint, translation.Config(engine="google").fingerprint)

    def test_existing_pinned_translate_tab_wins_and_chatgpt_is_untouched(self):
        chatgpt = tab("https://chatgpt.com/", focused=True)
        focused = tab(focused=True, visible=True)
        pinned = tab(pinned=True)
        malicious = tab("https://translate.google.com.attacker.test/", pinned=True)
        browser = SimpleNamespace(contexts=[SimpleNamespace(pages=[chatgpt, focused, pinned, malicious])])
        self.assertIs(web._choose_tab(browser), pinned)
        pinned.evaluate.assert_called_with("name => window.name = name", web.TAB_NAME)
        chatgpt.evaluate.assert_not_called()
        malicious.evaluate.assert_not_called()
        self.assertEqual(focused.evaluate.call_count, 1)  # Scan only, never rename.

    def test_first_scan_prefers_focused_tab_and_missing_tab_does_not_create_one(self):
        first, focused = tab(), tab(focused=True)
        browser = Mock(contexts=[SimpleNamespace(pages=[first, focused])])
        self.assertIs(web._choose_tab(browser), focused)
        browser.contexts = [SimpleNamespace(pages=[tab("https://chatgpt.com/")])]
        with self.assertRaises(translation.TranslationError):
            web._choose_tab(browser)
        browser.new_page.assert_not_called()
        browser.close.assert_not_called()

    def test_source_and_result_are_reset_before_reading_a_fresh_translation(self):
        page, source, _ = self.page()
        results = iter(["old result", "", "partial", "full translation"])
        latest = {"value": ""}

        def read(_page):
            latest["value"] = next(results, latest["value"])
            return latest["value"]

        with patch.object(web, "_read_output", side_effect=read), patch.object(web, "_copy_result", return_value="full translation") as copy:
            result = web._translate_page(page, "new question", "en", "vi", None)
        self.assertEqual(result, "full translation")
        self.assertEqual(source.fill.call_args_list, [call("", timeout=1500), call("new question", timeout=1500)])
        copy.assert_called_once_with(page, "full translation", None)
        page.goto.assert_not_called()

    def test_identical_successive_answers_are_still_accepted_after_empty_baseline(self):
        page, _, _ = self.page()
        with patch.object(web, "_read_output", side_effect=lambda p: "" if self.clock.now < 0.15 else "Same answer"), patch.object(web, "_copy_result", return_value="Same answer"):
            self.assertEqual(web._translate_page(page, "same source", "en", "vi", None), "Same answer")

    def test_language_change_uses_clean_url_without_source_text(self):
        page, _, _ = self.page()
        page.url = "https://translate.google.com/?sl=auto&tl=fr&text=old"
        page.goto.side_effect = lambda url, **kwargs: setattr(page, "url", url)
        with patch.object(web, "_read_output", side_effect=lambda p: "" if self.clock.now < 0.15 else "Answer"), patch.object(web, "_copy_result", return_value="Answer"):
            web._translate_page(page, "private source", "en", "vi", None)
        target_url = page.goto.call_args.args[0]
        self.assertIn("sl=en&tl=vi", target_url)
        self.assertNotIn("text=", target_url)
        self.assertNotIn("private", target_url)
        page.evaluate.assert_called_with("name => window.name = name", web.TAB_NAME)

    def test_stuck_old_result_is_reloaded_not_returned(self):
        page, source, _ = self.page()
        reloaded = {"value": False}

        def reload(url, **kwargs):
            reloaded["value"] = True
            source.input_value.side_effect = None
            source.input_value.return_value = ""

        page.goto.side_effect = reload
        with patch.object(web, "_read_output", return_value="stale"), patch.object(web, "_copy_result") as copy:
            with self.assertRaisesRegex(translation.TranslationError, "previous result"):
                web._translate_page(page, "new", "en", "vi", None)
        self.assertTrue(reloaded["value"])
        copy.assert_not_called()
        source.fill.assert_called_once_with("", timeout=1500)

    def test_timeout_is_not_saved_as_a_translation(self):
        page, _, _ = self.page()
        with patch.object(web, "_read_output", return_value=""), patch.object(web, "_copy_result") as copy:
            with self.assertRaises(translation.TranslationError):
                web._translate_page(page, "new", "en", "vi", None, timeout_s=1)
        copy.assert_not_called()

    def test_user_edit_or_navigation_aborts_instead_of_returning_wrong_result(self):
        page, source, _ = self.page()
        source.input_value.side_effect = lambda **kwargs: "user edited this"
        with patch.object(web, "_read_output", return_value=""), patch.object(web, "_copy_result") as copy:
            with self.assertRaisesRegex(translation.TranslationError, "Source text changed"):
                web._translate_page(page, "new", "en", "vi", None)
        copy.assert_not_called()
        page.url = "https://example.com/"
        source.fill.reset_mock()
        with self.assertRaises(translation.TranslationError):
            web._translate_page(page, "new", "en", "vi", None)
        source.fill.assert_not_called()

    def test_stop_during_wait_aborts_before_copying(self):
        page, _, _ = self.page()
        event = threading.Event()

        def cancel(_cancel, seconds=0.15):
            event.set()
            web._check_cancel(event)

        with patch.object(web, "_read_output", return_value=""), patch.object(web, "_pause", cancel), patch.object(web, "_copy_result") as copy:
            with self.assertRaisesRegex(translation.TranslationError, "cancelled"):
                web._translate_page(page, "new", "en", "vi", event)
        copy.assert_not_called()

    def test_copy_captures_complete_plain_text_and_always_restores_hooks(self):
        page = Mock()
        page.evaluate.side_effect = lambda script: ["Line one\nLine two"] if script == web._COPY_READ else True
        self.assertEqual(web._copy_result(page, "Line one Line two", None), "Line one\nLine two")
        page.evaluate.assert_called_with(web._COPY_REMOVE)
        page.locator.return_value.filter.return_value.first.click.assert_called_once()

    def test_copy_is_not_clicked_if_clipboard_cannot_be_protected(self):
        page = Mock()
        page.evaluate.return_value = False
        self.assertEqual(web._copy_result(page, "Verified translation", None), "Verified translation")
        page.locator.assert_not_called()
        page.evaluate.assert_called_with(web._COPY_REMOVE)

    def test_javascript_clipboard_hook_captures_without_writing_and_restores_original(self):
        import pythonmonkey

        script = """() => {
            let writes = 0;
            const original = () => { writes++; };
            const navigator = {clipboard: {writeText: original, write: original}};
            const window = {};
            let listener;
            const document = {
                addEventListener: (name, callback) => { listener = callback; },
                removeEventListener: (name, callback) => { if (listener === callback) listener = null; }
            };
            const queueMicrotask = callback => callback();
            const install = INSTALL;
            const read = READ;
            const remove = REMOVE;
            const installed = install();
            navigator.clipboard.writeText('Complete translation');
            const captured = read()[0];
            let prevented = false;
            listener({preventDefault: () => { prevented = true; }, clipboardData: {getData: () => 'Legacy copy'}});
            remove();
            return installed && captured === 'Complete translation' && prevented && writes === 0
                && navigator.clipboard.writeText === original && navigator.clipboard.write === original
                && !listener && !window.__rtcTranslateClipboard;
        }"""
        script = script.replace("INSTALL", web._COPY_INSTALL).replace("READ", web._COPY_READ).replace("REMOVE", web._COPY_REMOVE)
        self.assertTrue(pythonmonkey.eval(script)())

    def test_wrong_clipboard_text_falls_back_to_verified_output(self):
        page = Mock()
        page.evaluate.side_effect = lambda script: ["unrelated clipboard text"] if script == web._COPY_READ else True
        self.assertEqual(web._copy_result(page, "Actual target", None), "Actual target")
        page.evaluate.assert_called_with(web._COPY_REMOVE)

    def test_multiple_target_segments_preserve_repeated_text(self):
        page = Mock()
        page.locator.return_value.filter.return_value.all_inner_texts.return_value = ["Yes.", "Yes.", "Next sentence."]
        self.assertEqual(web._read_output(page), "Yes. Yes. Next sentence.")
        page.locator.return_value.filter.assert_called_once_with(visible=True)

    def test_all_chunks_reuse_one_connection_without_closing_browser(self):
        browser = Mock()
        playwright = Mock()
        playwright.chromium.connect_over_cdp.return_value = browser
        manager = Mock()
        manager.__enter__ = Mock(return_value=playwright)
        manager.__exit__ = Mock(return_value=False)
        text = " ".join(str(i) for i in range(1000))
        page = Mock()
        with patch("playwright.sync_api.sync_playwright", return_value=manager), patch.object(web, "_choose_tab", return_value=page), patch.object(web, "_translate_page", side_effect=lambda p, chunk, *args: chunk) as translate:
            self.assertEqual(web.translate(text, "en", "vi"), text)
        self.assertGreater(translate.call_count, 1)
        playwright.chromium.connect_over_cdp.assert_called_once_with(web.CDP_URL, timeout=10_000)
        browser.close.assert_not_called()
        browser.new_page.assert_not_called()
        self.assertTrue(all(c.args[0] is page for c in translate.call_args_list))

    def test_cancel_before_connection_and_while_waiting_for_lock(self):
        event = threading.Event()
        event.set()
        with patch("playwright.sync_api.sync_playwright") as launch:
            with self.assertRaises(translation.TranslationError):
                web.translate("Hello", "en", "vi", event)
        launch.assert_not_called()
        event.clear()
        lock = Mock()

        def unavailable(**kwargs):
            event.set()
            return False

        lock.acquire.side_effect = unavailable
        with patch.object(web, "_TAB_LOCK", lock):
            with self.assertRaises(translation.TranslationError):
                web.translate("Hello", "en", "vi", event)
        lock.release.assert_not_called()

    def test_connection_error_is_sanitized_and_lock_is_released(self):
        playwright = Mock()
        playwright.chromium.connect_over_cdp.side_effect = RuntimeError("private transcript in URL")
        manager = Mock()
        manager.__enter__ = Mock(return_value=playwright)
        manager.__exit__ = Mock(return_value=False)
        with patch("playwright.sync_api.sync_playwright", return_value=manager):
            with self.assertRaises(translation.TranslationError) as error:
                web.translate("Hello", "en", "vi")
        self.assertNotIn("private", str(error.exception))
        self.assertIn("launch_chrome.bat", str(error.exception))
        self.assertTrue(web._TAB_LOCK.acquire(blocking=False))
        web._TAB_LOCK.release()


if __name__ == "__main__":
    unittest.main()

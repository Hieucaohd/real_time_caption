import threading
import unittest
from unittest.mock import Mock, patch

from caption import chrome_translate as chrome
from caption.settings import Settings
from caption.translation import Config, Translator, TranslationError


class ChromeTranslationTests(unittest.TestCase):
    def test_client_reuses_connection_and_disconnects_without_closing_browser(self):
        driver = Mock()
        browser = driver.chromium.connect_over_cdp.return_value
        browser.is_connected.return_value = True
        with patch('playwright.sync_api.sync_playwright') as playwright, \
                patch.object(chrome, '_choose_tab'), patch.object(chrome, '_translate_page', return_value='translated'):
            playwright.return_value.start.return_value = driver
            client = chrome.Client()
            self.assertEqual(client.translate('one', 'en', 'vi'), 'translated')
            self.assertEqual(client.translate('two', 'en', 'vi'), 'translated')
            driver.chromium.connect_over_cdp.assert_called_once()
            client.close()
            driver.stop.assert_called_once()
            browser.close.assert_not_called()

    def test_engine_needs_no_key_and_has_distinct_cache(self):
        config = Config.from_settings(Settings(translation_engine="chrome"))
        config.validate()
        self.assertNotEqual(config.fingerprint, Config(engine="google").fingerprint)
        with patch.object(chrome, "translate", return_value="Xin chào") as translate:
            self.assertEqual(Translator().translate("Hello", config), "Xin chào")
        translate.assert_called_once_with("Hello", "en", "vi", None)

    def test_tab_selection_only_local_app_and_prefers_prepared_pair(self):
        remote = Mock(url="https://chatgpt.com")
        other = Mock(url="http://localhost:9999")
        other.evaluate.return_value = None
        unready = Mock(url="http://localhost:8765")
        unready.evaluate.return_value = {"ready": False, "focused": True}
        ready = Mock(url="http://127.0.0.1:8765")
        ready.evaluate.return_value = {"ready": True, "focused": False}
        browser = Mock(contexts=[Mock(pages=[remote, other, unready, ready])])
        self.assertIs(chrome._choose_tab(browser, "ja", "vi"), ready)
        remote.evaluate.assert_not_called()

    def test_result_error_timeout_and_cancel_all_release_browser_job(self):
        for state in ({"status": "error", "code": "setup"}, None, {"status": "done", "text": ""}):
            page = Mock()
            page.evaluate.side_effect = [None, state, None]
            with self.assertRaises(TranslationError):
                chrome._translate_page(page, "hello", "en", "vi")
            self.assertIn("release", page.evaluate.call_args.args[0])
        page = Mock()
        with self.assertRaises(TranslationError):
            chrome._translate_page(page, "hello", "en", "vi", timeout_s=0)
        self.assertIn("release", page.evaluate.call_args.args[0])
        cancel = threading.Event()
        def evaluate(script, arg):
            if "start" in script:
                cancel.set()
        page.evaluate.side_effect = evaluate
        with self.assertRaises(TranslationError):
            chrome._translate_page(page, "hello", "en", "vi", cancel)
        self.assertIn("release", page.evaluate.call_args.args[0])


if __name__ == "__main__":
    unittest.main()

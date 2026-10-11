from __future__ import annotations

import json
import queue
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from caption import conversations, settings, translation
from caption.translation_store import TranslationStore
from caption.translation_worker import TranslationWorker
from caption.translation_tab import TranslationPanel


class TranslationAdapterTests(unittest.TestCase):
    def test_azure_sends_text_and_region_without_key_in_url(self):
        config = translation.Config(key="secret", region="eastasia")
        with patch.object(translation, "_post", return_value=[{"translations": [{"text": "Xin chào"}]}]) as post:
            result = translation.Translator().translate("Hello", config)
        self.assertEqual(result, "Xin chào")
        url, body, headers = post.call_args.args
        self.assertNotIn("secret", url)
        self.assertIn("from=en&to=vi", url)
        self.assertEqual(body, [{"Text": "Hello"}])
        self.assertEqual(headers["Ocp-Apim-Subscription-Region"], "eastasia")

    def test_google_basic_v2_uses_plain_text_and_header_key(self):
        config = translation.Config(engine="google", key="secret")
        with patch.object(translation, "_post", return_value={"data": {"translations": [{"translatedText": "A &amp; B"}]}}) as post:
            result = translation.Translator().translate("A & B", config)
        self.assertEqual(result, "A & B")
        url, body, headers = post.call_args.args
        self.assertNotIn("secret", url)
        self.assertEqual(body["format"], "text")
        self.assertEqual(headers["X-Goog-Api-Key"], "secret")

    def test_azure_maps_chinese_to_its_supported_language_code(self):
        config = translation.Config(key="key", source="zh")
        with patch.object(translation, "_post", return_value=[{"translations": [{"text": "Hello"}]}]) as post:
            translation.Translator().translate("test", config)
        self.assertIn("from=zh-Hans", post.call_args.args[0])

    def test_gemini_fills_prompt_without_history_or_thinking_text(self):
        config = translation.Config(engine="gemini", key="secret", model="gemini-2.5-flash-lite", prompt="{source} → {target}: {text}")
        response = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "private thought", "thought": True}, {"text": "Xin chào"}]}}]}
        with patch.object(translation, "_post", return_value=response) as post:
            self.assertEqual(translation.Translator().translate("Hello", config), "Xin chào")
        url, body, headers = post.call_args.args
        self.assertNotIn("secret", url)
        self.assertEqual(len(body["contents"]), 1)
        self.assertEqual(body["contents"][0]["parts"][0]["text"], "English → Vietnamese: Hello")

    def test_gemini_truncated_or_blocked_response_is_an_error(self):
        config = translation.Config(engine="gemini", key="key", model="flash", prompt="{text}")
        with patch.object(translation, "_post", return_value={"candidates": [{"finishReason": "MAX_TOKENS"}]}):
            with self.assertRaises(translation.TranslationError):
                translation.Translator().translate("Hello", config)

    def test_http_errors_do_not_expose_key_or_response_body(self):
        error = urllib.error.HTTPError("https://server?key=secret", 429, "secret error body", {}, None)
        with patch.object(translation.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(translation.TranslationError) as raised:
                translation._post("https://server", {}, {"Key": "secret"})
        self.assertIn("429", str(raised.exception))
        self.assertNotIn("secret", str(raised.exception))

    def test_cancellation_and_invalid_configuration_send_no_request(self):
        event = threading.Event()
        event.set()
        with patch.object(translation, "_post") as post:
            with self.assertRaises(translation.TranslationError):
                translation.Translator().translate("Hello", translation.Config(key="key"), event)
            with self.assertRaises(translation.TranslationError):
                translation.Translator().translate("Hello", translation.Config())
            with self.assertRaises(translation.TranslationError):
                translation.Config(engine="vinai", source="vi", target="en").validate()
        post.assert_not_called()

    def test_large_text_is_split_without_losing_words(self):
        text = " ".join(str(i) for i in range(1000))
        translator = translation.Translator()
        with patch.object(translator, "_one", side_effect=lambda value, config: value) as one:
            self.assertEqual(translator.translate(text, translation.Config(key="key")), text)
        self.assertGreater(one.call_count, 1)

    def test_offline_loader_never_downloads_and_sets_correct_decoder_language(self):
        torch, transformers = MagicMock(), MagicMock()
        tensor = Mock(shape=(1, 8))
        tensor.to.return_value = tensor
        tokenizer = MagicMock()
        tokenizer.return_value = {"input_ids": tensor}
        tokenizer.lang_code_to_id = {"vi_VN": 99}
        tokenizer.batch_decode.return_value = ["Xin chào"]
        model = MagicMock()
        model.to.return_value = model.eval.return_value = model
        model.generate.return_value = [[99, 10, 2]]
        transformers.AutoTokenizer.from_pretrained.return_value = tokenizer
        transformers.AutoModelForSeq2SeqLM.from_pretrained.return_value = model
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "config.json").write_text("{}")
            with patch.object(translation, "OFFLINE_DIR", root), patch.dict("sys.modules", {"torch": torch, "transformers": transformers}):
                result = translation.Translator().translate("Hello", translation.Config(engine="vinai"))
        self.assertEqual(result, "Xin chào")
        self.assertTrue(transformers.AutoTokenizer.from_pretrained.call_args.kwargs["local_files_only"])
        self.assertTrue(transformers.AutoModelForSeq2SeqLM.from_pretrained.call_args.kwargs["local_files_only"])
        self.assertEqual(model.generate.call_args.kwargs["decoder_start_token_id"], 99)

    def test_download_requires_optional_libraries_before_network(self):
        with patch.object(translation.importlib.util, "find_spec", return_value=None), patch("huggingface_hub.snapshot_download") as download:
            with self.assertRaises(translation.TranslationError):
                translation.download_offline()
        download.assert_not_called()

    def test_config_fingerprint_excludes_keys_but_includes_prompt(self):
        self.assertEqual(translation.Config(key="one").fingerprint, translation.Config(key="two").fingerprint)
        self.assertNotEqual(translation.Config(prompt="one").fingerprint, translation.Config(prompt="two").fingerprint)


class TranslationPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.patch = patch.object(conversations, "CONVERSATIONS_DIR", self.root / "conversations")
        self.patch.start()
        self.conversations = conversations.ConversationStore(self.root / "test.sqlite3")
        self.item = self.conversations.create("Test")
        self.store = TranslationStore(self.conversations.db_path)
        self.workers = []

    def tearDown(self):
        for worker in self.workers:
            worker.close()
            worker._thread.join(2)
        self.patch.stop()
        self.temp.cleanup()

    def worker(self, translator=None, capacity=256):
        worker = TranslationWorker(self.store, translator or SimpleNamespace(translate=Mock(return_value="Xin chào")), capacity)
        self.workers.append(worker)
        return worker

    def line(self, origin="test:1", text="Hello"):
        return self.store.line(self.item.id, origin, "2026-10-11 10:00:00", text)

    def test_partial_final_chunks_merge_and_sync_remains_idempotent_after_resume(self):
        transcript = conversations.ConversationTranscript(self.item, self.conversations)
        transcript.begin_segment()
        self.assertEqual(transcript.write("Hello", False), [])
        captured = transcript.write("world", True)
        self.assertEqual(captured[0][2], "Hello world")
        self.store.line(self.item.id, *captured[0])
        self.store.sync_transcript(transcript)
        self.store.sync_transcript(transcript)
        self.assertEqual(len(self.store.lines(self.item.id)), 1)
        transcript.begin_segment()
        tail = transcript.write("Another day", False)
        self.assertEqual(tail, [])
        captured = transcript.close()
        self.store.line(self.item.id, *captured[0])
        self.store.sync_transcript(transcript)
        self.assertEqual(len(self.store.lines(self.item.id)), 2)

    def test_split_mid_utterance_preserves_both_physical_lines(self):
        transcript = conversations.ConversationTranscript(self.item, self.conversations)
        transcript.begin_segment()
        transcript.write("First fragment", False)
        with patch.object(conversations, "PART_LIMIT", 1):
            captured = transcript.write("Second fragment", True)
        self.assertEqual(len(captured), 2)
        for record in captured:
            self.store.line(self.item.id, *record)
        self.store.sync_transcript(transcript)
        self.assertEqual([line["source_text"] for line in self.store.lines(self.item.id)], ["First fragment", "Second fragment"])

    def test_retry_and_new_engine_keep_all_previous_answers(self):
        line = self.line()
        worker = self.worker()
        config = translation.Config(key="key")
        self.assertTrue(worker.submit(line, config))
        self.assertEqual(worker.results.get(timeout=2)[1], "done")
        self.assertFalse(worker.submit(line, config))
        self.assertTrue(worker.submit(line, config, force=True))
        worker.results.get(timeout=2)
        self.assertTrue(worker.submit(line, translation.Config(engine="google", key="key")))
        worker.results.get(timeout=2)
        history = self.store.history(line["id"])
        self.assertEqual([r["engine"] for r in history], ["azure", "azure", "google"])
        self.assertTrue(all(r["translated_text"] == "Xin chào" for r in history))
        self.assertEqual(len(TranslationStore(self.conversations.db_path).history(line["id"])), 3)

    def test_queue_deduplicates_and_stop_does_not_send_queued_requests(self):
        entered, release = threading.Event(), threading.Event()

        def translate(text, config, cancel):
            entered.set()
            release.wait(2)
            return "translated"

        adapter = SimpleNamespace(translate=Mock(side_effect=translate))
        worker = self.worker(adapter, capacity=1)
        line, other = self.line(), self.line("test:2", "Other")
        config = translation.Config(key="key")
        try:
            self.assertTrue(worker.submit(line, config))
            self.assertTrue(entered.wait(2))
            self.assertFalse(worker.submit(line, config, force=True))
            self.assertTrue(worker.submit(other, config))
            with self.assertRaises(translation.TranslationError):
                worker.submit(self.line("test:3"), config)
            worker.cancel_all()
        finally:
            release.set()
        outcomes = [worker.results.get(timeout=2)[1] for _ in range(2)]
        self.assertEqual(outcomes, ["cancelled", "cancelled"])
        adapter.translate.assert_called_once()
        self.assertEqual(self.store.history(other["id"])[0]["status"], "cancelled")

    def test_error_does_not_kill_worker_or_trigger_automatic_retry(self):
        adapter = SimpleNamespace(translate=Mock(side_effect=[translation.TranslationError("quota"), "ok"]))
        worker = self.worker(adapter)
        line = self.line()
        config = translation.Config(key="key")
        worker.submit(line, config)
        self.assertEqual(worker.results.get(timeout=2)[1:], ("error", "quota"))
        self.assertEqual(adapter.translate.call_count, 1)
        worker.submit(line, config, force=True)
        self.assertEqual(worker.results.get(timeout=2)[1:], ("done", "ok"))

    def test_preview_is_not_saved_to_sqlite(self):
        worker = self.worker()
        worker.preview(self.item.id, "partial", translation.Config(key="key"), 5)
        task, status, text = worker.results.get(timeout=2)
        self.assertTrue(task.preview)
        self.assertEqual(task.revision, 5)
        self.assertEqual(self.store.lines(self.item.id), [])

    def test_queued_requests_keep_their_engine_and_source_snapshot(self):
        entered, release = threading.Event(), threading.Event()

        def translate(text, config, cancel):
            entered.set()
            release.wait(2)
            return config.engine + ": " + text

        adapter = SimpleNamespace(translate=Mock(side_effect=translate))
        worker = self.worker(adapter)
        first, second = self.line(), self.line("test:2", "Original text")
        azure = translation.Config(key="azure-key")
        google = translation.Config(engine="google", key="google-key")
        try:
            worker.submit(first, azure)
            self.assertTrue(entered.wait(2))
            worker.submit(second, google)
            second["source_text"] = "Edited after queueing"
        finally:
            release.set()
        outcomes = [worker.results.get(timeout=2) for _ in range(2)]
        self.assertEqual([outcome[2] for outcome in outcomes], ["azure: Hello", "google: Original text"])
        self.assertEqual(self.store.history(second["id"])[0]["engine"], "google")

    def test_error_pauses_and_cancels_remaining_queue_without_sending_it(self):
        entered, release = threading.Event(), threading.Event()

        def fail(text, config, cancel):
            entered.set()
            release.wait(2)
            raise translation.TranslationError("quota")

        adapter = SimpleNamespace(translate=Mock(side_effect=fail))
        worker = self.worker(adapter)
        first, second = self.line(), self.line("test:2", "Other")
        config = translation.Config(key="key")
        try:
            worker.submit(first, config)
            self.assertTrue(entered.wait(2))
            worker.submit(second, config)
        finally:
            release.set()
        outcomes = [worker.results.get(timeout=2)[1] for _ in range(2)]
        self.assertEqual(outcomes, ["cancelled", "error"])
        adapter.translate.assert_called_once()
        self.assertEqual(self.store.history(second["id"])[0]["status"], "cancelled")
        with self.assertRaises(translation.TranslationError):
            worker.submit(second, config)

    def test_missing_lines_are_scoped_to_engine_source_and_success(self):
        first, second = self.line(), self.line("test:2", "Other")
        azure = translation.Config(key="key")
        google = translation.Config(engine="google", key="key")
        attempt = self.store.begin(first, azure)
        self.store.finish(attempt, "done", "Success")
        attempt = self.store.begin(second, azure)
        self.store.finish(attempt, "error", error="quota")
        self.assertEqual([line["id"] for line in self.store.missing(self.item.id, azure)], [second["id"]])
        self.assertEqual(len(self.store.missing(self.item.id, google)), 2)
        self.store.line(self.item.id, "test:1", first["stamp"], "Updated source")
        self.assertEqual(len(self.store.missing(self.item.id, azure)), 2)

    def test_recover_interrupted_request_and_distinct_repeated_source_lines(self):
        first = self.line()
        second = self.line("test:2")
        attempt = self.store.begin(first, translation.Config(key="key"))
        self.store.finish(attempt, "working")
        self.store.recover()
        self.assertEqual(self.store.history(first["id"])[0]["status"], "cancelled")
        self.assertNotEqual(first["id"], second["id"])


class TranslationPanelTests(unittest.TestCase):
    def test_continuous_partial_updates_use_latest_text_without_resetting_timer(self):
        panel = object.__new__(TranslationPanel)
        panel.enabled_var = Mock(get=lambda: True)
        panel.partial_var = Mock(get=lambda: True)
        panel._get_conversation_id = Mock(return_value="conversation")
        panel._revision = 3
        panel._preview_after = None
        panel._closed = False
        panel.after = Mock(return_value="timer")
        panel.config = Mock(return_value=translation.Config(key="key"))
        panel.worker, panel.preview_label = Mock(), Mock()
        transcript = SimpleNamespace(conversation_id="conversation", pending_text="Already committed")
        panel.on_partial(transcript, "first")
        panel.on_partial(transcript, "second")
        panel.after.assert_called_once()
        panel.after.call_args.args[1]()
        panel.worker.preview.assert_called_once_with("conversation", "Already committed second", panel.config(), 3)

    def test_stale_preview_from_previous_line_or_conversation_is_ignored(self):
        panel = object.__new__(TranslationPanel)
        panel._closed = False
        panel._revision = 10
        panel._get_conversation_id = Mock(return_value="new-conversation")
        panel._results = queue.Queue()
        panel.worker = SimpleNamespace(results=queue.Queue())
        panel.preview_label = Mock()
        panel.after = Mock()
        task = SimpleNamespace(preview=True, revision=9, line={"conversation_id": "old-conversation"})
        panel.worker.results.put((task, "done", "stale"))
        panel._drain()
        panel.preview_label.configure.assert_not_called()

    def test_defaults_do_not_enable_translation_and_settings_roundtrip(self):
        original = settings.Settings()
        self.assertFalse(original.translation_enabled)
        self.assertFalse(original.translation_partial)
        original.translation_engine = "gemini"
        original.translation_gemini_key = "test-key"
        original.translation_azure_key = "other-key"
        with tempfile.TemporaryDirectory() as folder, patch.object(settings, "SETTINGS_PATH", Path(folder) / "settings.json"):
            original.save()
            restored = settings.Settings.load()
        self.assertEqual(restored.translation_gemini_key, "test-key")
        self.assertEqual(restored.translation_azure_key, "other-key")


if __name__ == "__main__":
    unittest.main()

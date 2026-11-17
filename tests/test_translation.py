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
    def test_reading_keeps_source_order_and_success_during_retry(self):
        config = translation.Config(engine='chrome')
        first, second = self.line('one', 'First'), self.line('two', 'Second')
        a, b = self.store.begin(first, config), self.store.begin(second, config)
        self.store.finish(b, 'done', 'Second translated')
        self.assertEqual([s['text'] for s in self.store.reading(self.item.id, config)['segments']], ['Second translated'])
        self.assertEqual(self.store.reading(self.item.id, config)['segments'][0]['line_ids'], [second['id']])
        self.store.finish(a, 'done', 'First translated')
        self.store.finish(self.store.begin(first, config), 'error', error='Retry failed')
        self.assertEqual([s['text'] for s in self.store.reading(self.item.id, config)['segments']], ['First translated', 'Second translated'])
        self.assertEqual(self.store.reading(self.item.id, translation.Config(engine='chrome', target='ja'))['segments'], [])
        self.line('one', 'Corrected source')
        self.assertEqual(self.store.reading(self.item.id, config)['translated_lines'], 1)

    def test_reading_deduplicates_batches_but_preserves_repeated_passages(self):
        config = translation.Config(engine='chrome')
        for group in range(2):
            lines = [self.line(f'{group}:{index}', text) for index, text in enumerate(['Hello', 'World'])]
            for attempt in self.store.begin_batch(lines, config):
                self.store.finish(attempt, 'done', 'Hello world translated')
        result = self.store.reading(self.item.id, config)
        self.assertEqual(result['translated_lines'], 4)
        self.assertEqual([s['text'] for s in result['segments']], ['Hello world translated'] * 2)
        self.assertEqual([s['line_ids'] for s in result['segments']], [[1, 2], [3, 4]])
        with self.store.connect() as db:
            db.execute("UPDATE line_translations SET batch_id='',created_at='same-second'")
        self.assertEqual(self.store.reading(self.item.id, config), result)

    def test_reading_only_links_completed_batch_members(self):
        config = translation.Config(engine='chrome')
        lines = [self.line(f'partial:{i}', f'Sentence {i}') for i in range(3)]
        attempts = self.store.begin_batch(lines, config)
        self.store.finish(attempts[0], 'error', error='Interrupted')
        self.store.finish(attempts[2], 'done', 'Translated tail')
        reading = self.store.reading(self.item.id, config)
        self.assertEqual(reading['translated_lines'], 1)
        self.assertEqual(reading['segments'][0]['line_ids'], [lines[2]['id']])

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
            worker.join(2)
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
        transcript.close()

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
        worker.preview(self.item.id, "partial", translation.Config(engine="chrome"), 5)
        task, status, text = worker.results.get(timeout=2)
        self.assertTrue(task.preview)
        self.assertEqual(task.revision, 5)
        self.assertEqual(self.store.lines(self.item.id), [])

    def test_preview_rejects_cloud_engines_without_sending(self):
        adapter = SimpleNamespace(translate=Mock(return_value='unused'))
        worker = self.worker(adapter)
        with self.assertRaises(translation.TranslationError):
            worker.preview(self.item.id, 'draft', translation.Config(key='key'), 1)
        adapter.translate.assert_not_called()

    def test_two_chrome_finals_and_preview_run_together_with_bounded_backlog(self):
        first, second, release = threading.Event(), threading.Event(), threading.Event()
        def translate(text, config, cancel):
            if text == 'First final':
                first.set(); release.wait(3)
            if text == 'Second final':
                second.set(); release.wait(3)
            return text
        adapter = SimpleNamespace(translate=Mock(side_effect=translate))
        worker = self.worker(adapter)
        config = translation.Config(engine='chrome')
        try:
            line = self.line('1', 'First final')
            worker.submit(line, config)
            self.assertTrue(first.wait(2))
            worker.submit(self.line('2', 'Second final'), config)
            self.assertTrue(second.wait(2))
            worker.submit(self.line('3', 'Third final'), config)
            self.assertFalse(worker.submit(line, config))
            with worker._condition:
                worker.preview(self.item.id, 'Old draft', config, 1)
                worker.preview(self.item.id, 'Latest draft', config, 1)
            preview = worker.results.get(timeout=2)
            self.assertEqual(preview[1:], ('done', 'Latest draft'))
            self.assertTrue(preview[0].preview)
            self.assertEqual(adapter.translate.call_count, 3)  # Third final still queued.
        finally:
            release.set()
        outcomes = [worker.results.get(timeout=3) for _ in range(3)]
        self.assertCountEqual([result[2] for result in outcomes], ['First final', 'Second final', 'Third final'])
        self.assertEqual(len(self.store.lines(self.item.id)), 3)
        for line in self.store.lines(self.item.id):
            self.assertEqual(self.store.history(line['id'])[0]['translated_text'], line['source_text'])

    def test_parallel_finals_complete_out_of_order_without_mixing_database_rows(self):
        entered, release = threading.Event(), threading.Event()
        def translate(text, config, cancel):
            if text == 'Slow':
                entered.set(); release.wait(3)
            return 'Translated ' + text
        worker = self.worker(SimpleNamespace(translate=Mock(side_effect=translate)))
        config = translation.Config(engine='chrome')
        slow, fast = self.line('slow', 'Slow'), self.line('fast', 'Fast')
        try:
            worker.submit(slow, config)
            self.assertTrue(entered.wait(2))
            worker.submit(fast, config)
            task, status, result = worker.results.get(timeout=2)
            self.assertEqual((task.line['id'], status, result), (fast['id'], 'done', 'Translated Fast'))
            self.assertEqual(self.store.history(slow['id'])[0]['status'], 'working')
        finally:
            release.set()
        self.assertEqual(worker.results.get(timeout=2)[2], 'Translated Slow')
        self.assertEqual(self.store.history(slow['id'])[0]['translated_text'], 'Translated Slow')
        self.assertEqual(self.store.history(fast['id'])[0]['translated_text'], 'Translated Fast')

    def test_stop_cancels_all_active_lanes_and_queued_work(self):
        active = {text: threading.Event() for text in ('One', 'Two', 'Preview')}
        release = threading.Event()
        cancels = []
        def translate(text, config, cancel):
            cancels.append(cancel); active[text].set(); release.wait(3)
            return 'Late result'
        worker = self.worker(SimpleNamespace(translate=Mock(side_effect=translate)))
        config = translation.Config(engine='chrome')
        try:
            worker.submit(self.line('1', 'One'), config)
            worker.submit(self.line('2', 'Two'), config)
            worker.preview(self.item.id, 'Preview', config, 1)
            for event in active.values(): self.assertTrue(event.wait(2))
            queued = self.line('3', 'Queued')
            worker.submit(queued, config)
            worker.cancel_all()
            self.assertTrue(all(cancel.is_set() for cancel in cancels))
            self.assertEqual(self.store.history(queued['id'])[0]['status'], 'cancelled')
        finally:
            release.set()
        for _ in range(4): self.assertEqual(worker.results.get(timeout=2)[1], 'cancelled')
        self.assertTrue(all(self.store.history(line['id'])[0]['translated_text'] == ''
                            for line in self.store.lines(self.item.id)))

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

    def test_chrome_error_does_not_cancel_other_active_or_queued_lines(self):
        entered, release = threading.Event(), threading.Event()
        def translate(text, config, cancel):
            if text == 'Slow':
                entered.set()
                release.wait(3)
                self.assertFalse(cancel.is_set())
            if text == 'Fail':
                raise translation.TranslationError('Tab unavailable')
            return text
        worker = self.worker(SimpleNamespace(translate=translate))
        config = translation.Config(engine='chrome')
        slow, fail, following = [self.line('isolation:' + text, text) for text in ('Slow', 'Fail', 'Following')]
        try:
            worker.submit(slow, config)
            self.assertTrue(entered.wait(2))
            worker.submit(fail, config)
            self.assertEqual(worker.results.get(timeout=2)[1], 'error')
            worker.submit(following, config)
            self.assertEqual(worker.results.get(timeout=2)[1:], ('done', 'Following'))
        finally:
            release.set()
        self.assertEqual(worker.results.get(timeout=2)[1:], ('done', 'Slow'))

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
        # A running desktop/web process owns this request, so startup preserves it.
        self.store.recover()
        self.assertEqual(self.store.history(first["id"])[0]["status"], "working")
        with patch('caption.translation_store.process_alive', return_value=False):
            self.store.recover()
        self.assertEqual(self.store.history(first["id"])[0]["status"], "cancelled")
        self.assertNotEqual(first["id"], second["id"])

    def test_batch_sends_once_and_history_has_shared_source_not_fake_line_alignment(self):
        adapter = SimpleNamespace(translate=Mock(return_value="Shared translated paragraph"))
        worker = self.worker(adapter)
        lines = [self.line("test:1", "First."), self.line("test:2", "Second.")]
        config = translation.Config(key="key")
        self.assertTrue(worker.submit_batch(lines, config))
        task, status, _ = worker.results.get(timeout=2)
        self.assertEqual(status, "done")
        adapter.translate.assert_called_once_with("First.\nSecond.", config, task.cancel)
        self.assertEqual(task.members, tuple(lines))
        for line in lines:
            row = self.store.history(line["id"])[0]
            self.assertEqual(row["batch_size"], 2)
            self.assertEqual(row["source_text"], line["source_text"])
            self.assertEqual(row["batch_source"], "First.\nSecond.")
            self.assertEqual(row["translated_text"], "Shared translated paragraph")
        self.assertEqual(self.store.missing(self.item.id, config), [])
        self.assertFalse(worker.submit_batch(lines, config))
        self.assertTrue(worker.submit(lines[0], config, force=True))
        worker.results.get(timeout=2)
        self.assertEqual(self.store.history(lines[0]["id"])[-1]["batch_size"], 1)
        self.assertEqual(len(self.store.history(lines[1]["id"])), 1)

    def test_batch_cancellation_marks_every_member_and_does_not_send_queued_group(self):
        entered, release = threading.Event(), threading.Event()

        def translate(text, config, cancel):
            entered.set()
            release.wait(2)
            return "discarded response"

        adapter = SimpleNamespace(translate=Mock(side_effect=translate))
        worker = self.worker(adapter)
        lines = [self.line(f"test:{i}", f"Sentence {i}") for i in range(4)]
        config = translation.Config(key="key")
        try:
            worker.submit_batch(lines[:2], config)
            self.assertTrue(entered.wait(2))
            worker.submit_batch(lines[2:], config)
            self.assertFalse(worker.submit(lines[1], config, force=True))
            worker.cancel_all()
        finally:
            release.set()
        self.assertEqual([worker.results.get(timeout=2)[1] for _ in range(2)], ["cancelled", "cancelled"])
        adapter.translate.assert_called_once()
        for line in lines:
            row = self.store.history(line["id"])[0]
            self.assertEqual(row["status"], "cancelled")
            self.assertEqual(row["translated_text"], "")
        self.assertEqual(len(self.store.missing(self.item.id, config)), 4)

    def test_batch_failure_marks_all_members_and_can_be_retried(self):
        adapter = SimpleNamespace(translate=Mock(side_effect=[translation.TranslationError("quota"), "ok"]))
        worker = self.worker(adapter)
        lines = [self.line(), self.line("test:2", "Other")]
        config = translation.Config(key="key")
        worker.submit_batch(lines, config)
        self.assertEqual(worker.results.get(timeout=2)[1], "error")
        self.assertEqual([self.store.history(line["id"])[0]["status"] for line in lines], ["error", "error"])
        self.assertTrue(worker.submit_batch(lines, config, force=True))
        self.assertEqual(worker.results.get(timeout=2)[1], "done")

    def test_batch_rejects_mixed_conversations_and_deduplicates_member_ids(self):
        worker = self.worker()
        line = self.line()
        config = translation.Config(key="key")
        with self.assertRaises(translation.TranslationError):
            worker.submit_batch([line, dict(line, conversation_id="another")], config)
        self.assertEqual(self.store.history(line["id"]), [])
        worker.submit_batch([line, line], config)
        task, _, _ = worker.results.get(timeout=2)
        self.assertEqual(len(task.members), 1)
        self.assertEqual(len(self.store.history(line["id"])), 1)

    def test_migration_preserves_legacy_single_line_history(self):
        line = self.line()
        attempt = self.store.begin(line, translation.Config(key="key"))
        self.store.finish(attempt, "done", "Existing answer")
        with self.store.connect() as db:
            db.execute("ALTER TABLE line_translations DROP COLUMN batch_size")
            db.execute("ALTER TABLE line_translations DROP COLUMN batch_source")
        reopened = TranslationStore(self.conversations.db_path)
        history = reopened.history(line["id"])
        self.assertEqual(history[0]["translated_text"], "Existing answer")
        self.assertEqual(history[0]["batch_size"], 1)
        self.assertEqual(history[0]["batch_source"], "")


class TranslationPanelTests(unittest.TestCase):
    def test_continuous_partial_updates_use_latest_text_without_resetting_timer(self):
        panel = object.__new__(TranslationPanel)
        panel.enabled_var = Mock(get=lambda: True)
        panel.partial_var = Mock(get=lambda: True)
        panel.engine = Mock(return_value='chrome')
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
        panel._google_dirty = threading.Event()
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
        self.assertEqual(original.translation_batch_size, 1)
        self.assertEqual(original.translation_batch_timeout, 5.0)
        original.translation_batch_size = 3
        original.translation_batch_timeout = 2.5
        original.translation_engine = "gemini"
        original.translation_gemini_key = "test-key"
        original.translation_azure_key = "other-key"
        with tempfile.TemporaryDirectory() as folder, patch.object(settings, "SETTINGS_PATH", Path(folder) / "settings.json"):
            original.save()
            restored = settings.Settings.load()
        self.assertEqual(restored.translation_gemini_key, "test-key")
        self.assertEqual(restored.translation_azure_key, "other-key")
        self.assertEqual(restored.translation_batch_size, 3)
        self.assertEqual(restored.translation_batch_timeout, 2.5)


if __name__ == "__main__":
    unittest.main()

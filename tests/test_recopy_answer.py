import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_web_api as fixtures
from caption import chatgpt, chatgpt_recopy, paths


URL = 'https://chatgpt.com/c/recopy-test'
SOURCE = dict(response_conversation_url=URL, response_position=1, response_turn_id='old', response_message_id='a1')
RESULT = dict(content='## Recovered\n\n**Actual answer** $x^2$', response_tab_title='Recovered tab',
              response_conversation_url=URL, response_position=2, response_turn_id='turn1', response_message_id='a2')


class RecopyApiTests(unittest.TestCase):
    tearDown = fixtures.WebApiTests.tearDown
    post = fixtures.WebApiTests.post
    wait_job = fixtures.WebApiTests.wait_job

    def setUp(self):
        fixtures.WebApiTests.setUp(self)
        self.service.busy = True
        self.mid = self.service.store.add_message(self.cid, 'bot', 'ChatGPT', 'Original answer', **SOURCE)
        self.root_node = self.service.research.store.import_answer(self.cid, self.mid)
        self.child = self.service.research.children(self.cid, self.root_node['id'],
            {'questions': ['Explain'], 'config': {'selected_tab': 'tab', 'screenshot': False}})[0]
        self.service.research.store.update(self.cid, self.child['id'], status='error', **SOURCE)
        self.chat_url = f'/api/conversations/{self.cid}/answers/{self.mid}/recopy'
        self.child_url = f'/api/conversations/{self.cid}/research/{self.child["id"]}/recopy'

    def test_chat_recopy_updates_same_message_and_linked_root_in_database(self):
        with patch('web_api.recopy.recopy_response', return_value=RESULT) as copy, patch('caption.chatgpt.ask') as ask:
            response = self.post(self.chat_url)
            self.assertEqual(response.status_code, 200, response.text)
            copy.assert_called_once_with(URL, 1, '')
            ask.assert_not_called()
        messages = self.service.store.messages(self.cid)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].content, RESULT['content'])
        self.assertEqual(messages[0].response_position, 2)
        self.assertEqual(self.service.research.store.get(self.cid, self.root_node['id'])['answer'], RESULT['content'])
        self.assertEqual(self.service.research.store.get(self.cid, self.child['id'])['question'], 'Explain')
        history = (self.service.conversation(self.cid).folder / 'chat_history.md').read_text(encoding='utf-8')
        self.assertIn('**Actual answer**', history)

    def test_failed_empty_child_can_be_recovered_without_resending_question(self):
        with patch('web_api.recopy.recopy_response', return_value=RESULT), patch('caption.chatgpt.ask') as ask:
            response = self.post(self.child_url)
            self.assertEqual(response.status_code, 200, response.text)
            ask.assert_not_called()
        child = self.service.research.store.get(self.cid, self.child['id'])
        self.assertEqual(child['status'], 'done')
        self.assertEqual(child['answer'], RESULT['content'])
        self.assertEqual(child['response_message_id'], 'a2')
        self.assertEqual(self.service.store.messages(self.cid)[0].content, 'Original answer')

    def test_empty_failed_or_stale_copy_never_overwrites_saved_content(self):
        for result in ({**RESULT, 'content': '  '}, chatgpt.ChatGPTError('Chrome offline')):
            with patch('web_api.recopy.recopy_response', side_effect=result if isinstance(result, Exception) else None,
                       return_value=result):
                response = self.post(self.chat_url)
                self.assertGreaterEqual(response.status_code, 400)
            self.assertEqual(self.service.store.messages(self.cid)[0].content, 'Original answer')
        def changed(*args):
            self.service.store.update_message_content(self.cid, self.mid, 'Newer edit')
            return RESULT
        with patch('web_api.recopy.recopy_response', side_effect=changed):
            self.assertEqual(self.post(self.chat_url).status_code, 400)
        self.assertEqual(self.service.store.messages(self.cid)[0].content, 'Newer edit')

    def test_requires_client_and_ownership_and_rejects_active_node(self):
        self.service.research.store.update(self.cid, self.child['id'], status='working')
        other = self.service.store.create('Test', 'Other')
        with patch('web_api.recopy.recopy_response') as copy:
            self.assertEqual(self.client.post(self.chat_url).status_code, 403)
            self.assertEqual(self.post(self.child_url).status_code, 400)
            self.assertEqual(self.post(self.chat_url.replace(self.cid, other.id)).status_code, 404)
            copy.assert_not_called()

    def test_copy_failure_keeps_a_placeholder_with_source_and_recovers_existing_job(self):
        self.service.busy = False
        with patch('caption.chatgpt.ask', side_effect=chatgpt.ChatGPTCopyError('Empty copy', SOURCE, 'Original tab')) as ask:
            created = self.post(f'/api/conversations/{self.cid}/jobs', {'message': 'Question', 'selected_tab': 'tab'}).json()
            job = self.wait_job(created['id'])
            self.assertEqual(job['status'], 'error')
            message = next(m for m in self.service.store.messages(self.cid) if m.id == job['answer_id'])
            self.assertEqual(message.content, '')
            self.assertEqual(message.response_conversation_url, URL)
            with patch('web_api.recopy.recopy_response', return_value=RESULT):
                response = self.post(f'/api/conversations/{self.cid}/answers/{message.id}/recopy')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.service.jobs[created['id']]['status'], 'done')
            ask.assert_called_once()

    def test_child_copy_failure_keeps_source_for_recovery_and_manual_source_edit(self):
        self.service.busy = False
        base = f'/api/conversations/{self.cid}/research/{self.child["id"]}'
        with patch('caption.chatgpt.ask', side_effect=chatgpt.ChatGPTCopyError('Empty child copy', SOURCE, 'Child tab')):
            self.assertEqual(self.post(base + '/retry').status_code, 200)
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                child = self.service.research.store.get(self.cid, self.child['id'])
                if child['status'] == 'error' and child['detail'] == 'Empty child copy':
                    break
                time.sleep(.02)
            self.assertEqual(child['response_conversation_url'], URL)
            self.assertEqual(child['response_tab_title'], 'Child tab')
            response = self.client.put(base + '/source', headers=self.headers,
                json={'response_conversation_url': URL, 'response_position': 2})
            self.assertEqual(response.status_code, 200, response.text)


def snapshot():
    mapping = {'root': {'parent': None, 'message': None}}
    parent = 'root'
    for key, role in [('u1', 'user'), ('a1', 'assistant'), ('a2', 'assistant'), ('u2', 'user'), ('a3', 'assistant')]:
        mapping[key] = {'parent': parent, 'message': {'id': key, 'author': {'role': role}, 'channel': 'final',
                        'recipient': 'all', 'content': {'content_type': 'text'}}}
        parent = key
    return {'mapping': mapping, 'current_node': parent}


class RecopyBrowserTests(unittest.TestCase):
    def test_latest_response_stays_within_same_user_question(self):
        self.assertEqual(chatgpt_recopy.latest_answer_for_question(snapshot(), 1), 'a2')
        self.assertEqual(chatgpt_recopy.latest_answer_for_question(snapshot(), 3), 'a3')
        with self.assertRaises(ValueError):
            chatgpt_recopy.latest_answer_for_question(snapshot(), 99)

    def test_real_copy_button_reads_latest_response_and_closes_only_helper_tab(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory() as folder, patch.object(paths, 'CACHE_DIR', Path(folder)), sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            context = browser.new_context()
            posted = []
            def route(request):
                if request.request.method == 'POST':
                    posted.append(request.request.url)
                if '/backend-api/conversation/' in request.request.url:
                    request.fulfill(json=snapshot())
                else:
                    request.fulfill(content_type='text/html', body='''<title>Recovered tab</title>
                      <form data-chatgpt-composer><div data-composer-markdown contenteditable="true">Do not edit</div></form>
                      <section data-turn-key="turn1">
                        <div data-message-author-role="assistant" data-message-id="a1">Empty earlier response</div>
                        <div class="turn-action-controls"><button aria-label="Copy" onclick="navigator.clipboard.writeText('')">Copy</button></div>
                        <div data-message-author-role="assistant" data-message-id="a2">Actual answer</div>
                        <div class="turn-action-controls"><button aria-label="Copy" onclick="navigator.clipboard.writeText('## Actual answer')">Copy</button></div>
                      </section>
                      <section data-turn-key="turn2"><div data-message-author-role="assistant" data-message-id="a3">Unrelated later question</div>
                        <div class="turn-action-controls"><button aria-label="Copy" onclick="navigator.clipboard.writeText('WRONG')">Copy</button></div>
                      </section>''')
            context.route('https://chatgpt.com/**', route)
            original = context.new_page(); original.goto(URL)
            with patch.object(chatgpt, '_open_background_tab', side_effect=lambda _: context.new_page()):
                result = chatgpt_recopy._recopy_in_browser(browser, URL, 1)
            self.assertEqual(result['content'], '## Actual answer')
            self.assertEqual(result['response_message_id'], 'a2')
            self.assertEqual(result['response_position'], 2)
            self.assertEqual(context.pages, [original])
            self.assertEqual(posted, [])
            self.assertEqual(original.locator('[contenteditable]').inner_text(), 'Do not edit')
            browser.close()

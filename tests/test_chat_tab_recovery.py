import unittest
from unittest.mock import patch

import test_web_api as fixtures
from caption import chatgpt
from caption.settings import Settings


def tab(key, title):
    return chatgpt.ChromeChatGPTTab(key=key, title=title, label=title, url='https://chatgpt.com/', focused=False)


class ChatTabRecoveryTests(unittest.TestCase):
    setUp = fixtures.WebApiTests.setUp
    tearDown = fixtures.WebApiTests.tearDown
    post = fixtures.WebApiTests.post
    wait_job = fixtures.WebApiTests.wait_job

    def send(self):
        return self.post(f'/api/conversations/{self.cid}/jobs', {'message': 'My question', 'selected_tab': 'closed'}).json()

    def test_missing_tab_scans_once_saves_first_and_waits_for_manual_retry(self):
        settings = Settings.load()
        settings.chatgpt_selected_tab = 'closed'
        settings.chat_max_lines = 42
        settings.save()
        with patch('web_api.service.chatgpt.ask', side_effect=[chatgpt.ChatGPTTabClosed('Closed'), 'Answer']) as ask, \
                patch('web_api.service.chatgpt.list_chrome_chatgpt_tabs', return_value=[tab('first', 'First'), tab('second', 'Second')]) as scan:
            job = self.wait_job(self.send()['id'])
            self.assertEqual(job['status'], 'error')
            self.assertIn('hiện tại đang chọn tab này: First', job['error'])
            self.assertEqual(job['tab_recovery']['selected_tab'], 'first')
            self.assertEqual(Settings.load().chatgpt_selected_tab, 'first')
            self.assertEqual(Settings.load().chatgpt_selected_tab_title, 'First')
            self.assertEqual(Settings.load().chat_max_lines, 42)
            ask.assert_called_once()
            scan.assert_called_once()
            self.assertEqual(len(self.service.jobs), 1)
            retry = self.post('/api/jobs/' + job['id'] + '/retry').json()
            self.assertEqual(self.wait_job(retry['id'])['status'], 'done')
            self.assertEqual(ask.call_args.kwargs['selected_tab'], 'first')
            self.assertEqual([m.role for m in self.service.store.messages(self.cid)], ['user', 'bot'])

    def test_empty_scan_does_not_create_a_tab_or_send_to_an_implicit_tab(self):
        with patch('web_api.service.chatgpt.ask', side_effect=chatgpt.ChatGPTTabClosed('Closed')) as ask, \
                patch('web_api.service.chatgpt.list_chrome_chatgpt_tabs', return_value=[]), \
                patch('web_api.service.chatgpt.open_new_chatgpt_tab') as create:
            job = self.wait_job(self.send()['id'])
            self.assertEqual(job['tab_recovery']['tabs'], [])
            self.assertEqual(Settings.load().chatgpt_selected_tab, '')
            self.assertIn('không tìm thấy tab ChatGPT', job['error'])
            self.assertEqual(self.post('/api/jobs/' + job['id'] + '/retry').status_code, 400)
            ask.assert_called_once()
            create.assert_not_called()

    def test_scan_failure_preserves_selection_and_later_explicit_retry_can_choose_another(self):
        settings = Settings.load(); settings.chatgpt_selected_tab = 'closed'; settings.save()
        with patch('web_api.service.chatgpt.ask', side_effect=[chatgpt.ChatGPTTabClosed('Closed'), 'Answer']) as ask, \
                patch('web_api.service.chatgpt.list_chrome_chatgpt_tabs', side_effect=RuntimeError('Chrome offline')):
            job = self.wait_job(self.send()['id'])
            self.assertIn('Không quét lại được', job['error'])
            self.assertEqual(Settings.load().chatgpt_selected_tab, 'closed')
            ask.assert_called_once()
            retry = self.post('/api/jobs/' + job['id'] + '/retry', {'selected_tab': 'manually-chosen'}).json()
            self.assertEqual(self.wait_job(retry['id'])['status'], 'done')
            self.assertEqual(ask.call_args.kwargs['selected_tab'], 'manually-chosen')

    def test_unrelated_chat_error_does_not_rescan(self):
        with patch('web_api.service.chatgpt.ask', side_effect=chatgpt.ChatGPTError('Not logged in')), \
                patch('web_api.service.chatgpt.list_chrome_chatgpt_tabs') as scan:
            job = self.wait_job(self.send()['id'])
            self.assertEqual(job['error'], 'Not logged in')
            self.assertNotIn('tab_recovery', job)
            scan.assert_not_called()

    def test_scan_excludes_saved_realtime_lanes_and_reserved_tabs(self):
        from web_api.realtime_chat_store import RealtimeChatStore
        self.service.realtime_chat.store.save(self.cid, {
            'selected_tab': 'rt-actions', 'summary_tab': 'rt-summary'})
        # Reopening the store must retain the reservation even when realtime is stopped.
        reopened = RealtimeChatStore(self.service.store)
        self.assertEqual(reopened.tab_keys(), {'rt-actions', 'rt-summary'})
        with patch('web_api.service.chatgpt.list_chrome_chatgpt_tabs', return_value=[
            tab('rt-actions', 'ChatGPT'), tab('rt-summary', 'ChatGPT'),
            tab(chatgpt.VOCAB_TAB, 'New words'),
            tab(chatgpt.DICTATION_TAB_PREFIX + 'mic', 'ChatGPT'),
            tab('ordinary', 'ChatGPT'),
        ]):
            response = self.client.get('/api/chrome-tabs')
            self.assertEqual(response.status_code, 200)
            self.assertEqual([t['key'] for t in response.json()], ['ordinary'])

    def test_recovery_skips_realtime_lanes_without_retrying_question(self):
        self.service.realtime_chat.store.save(self.cid, {
            'selected_tab': 'rt-actions', 'summary_tab': 'rt-summary'})
        with patch('web_api.service.chatgpt.ask', side_effect=chatgpt.ChatGPTTabClosed('Closed')) as ask, \
                patch('web_api.service.chatgpt.list_chrome_chatgpt_tabs', return_value=[
                    tab('rt-actions', 'Actions'), tab('rt-summary', 'Summary'), tab('ordinary', 'Ordinary')]):
            job = self.wait_job(self.send()['id'])
            self.assertEqual(job['tab_recovery']['selected_tab'], 'ordinary')
            self.assertEqual(Settings.load().chatgpt_selected_tab, 'ordinary')
            ask.assert_called_once()

    def test_only_realtime_tabs_returns_empty_without_changing_their_ids(self):
        config = {'selected_tab': 'rt-actions', 'summary_tab': 'rt-summary'}
        self.service.realtime_chat.store.save(self.cid, config)
        with patch('web_api.service.chatgpt.list_chrome_chatgpt_tabs', return_value=[
            tab('rt-actions', 'Actions'), tab('rt-summary', 'Summary')]):
            self.assertEqual(self.client.get('/api/chrome-tabs').json(), [])
        self.assertEqual(self.service.realtime_chat.store.settings(self.cid, {})[0], config)

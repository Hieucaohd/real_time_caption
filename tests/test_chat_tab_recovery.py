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

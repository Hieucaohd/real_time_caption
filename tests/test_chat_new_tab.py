"""New Chat mode must never send to or reset the dropdown's selected tab."""
import unittest
from unittest.mock import patch

import test_web_api as fixtures
from test_chat_tab_recovery import tab
from caption import chatgpt
from caption.settings import Settings


class NewChatTabTests(unittest.TestCase):
    setUp = fixtures.WebApiTests.setUp
    tearDown = fixtures.WebApiTests.tearDown
    post = fixtures.WebApiTests.post
    wait_job = fixtures.WebApiTests.wait_job

    def send(self, **changes):
        payload = dict(kind='chat', message='Question', new_chat=True,
                       selected_tab='old-dropdown', attach_transcript=False, screenshot=False)
        payload.update(changes)
        return self.post(f'/api/conversations/{self.cid}/jobs', payload).json()

    def test_new_mode_ignores_dropdown_and_saved_selection(self):
        settings = Settings.load()
        settings.chatgpt_selected_tab = 'saved-old'
        settings.save()
        with patch('web_api.service.chatgpt.open_new_chatgpt_tab', return_value=tab('new-tab', 'New')) as open_tab, \
                patch('web_api.service.chatgpt.ask', return_value='Answer') as ask:
            job = self.wait_job(self.send()['id'])
        self.assertEqual(job['status'], 'done')
        open_tab.assert_called_once()
        self.assertEqual(ask.call_args.kwargs['selected_tab'], 'new-tab')
        self.assertFalse(ask.call_args.kwargs['new_chat'])
        self.assertEqual(Settings.load().chatgpt_selected_tab, 'saved-old')

    def test_empty_selection_still_allows_new_chat(self):
        settings = Settings.load()
        settings.chatgpt_selected_tab = ''
        settings.save()
        with patch('web_api.service.chatgpt.open_new_chatgpt_tab', return_value=tab('new-tab', 'New')), \
                patch('web_api.service.chatgpt.ask', return_value='Answer'):
            self.assertEqual(self.wait_job(self.send(selected_tab='')['id'])['status'], 'done')

    def test_retry_reuses_created_tab_without_resetting_it(self):
        with patch('web_api.service.chatgpt.open_new_chatgpt_tab', return_value=tab('new-tab', 'New')) as open_tab, \
                patch('web_api.service.chatgpt.ask', side_effect=[chatgpt.ChatGPTError('Upload failed'), 'Answer']) as ask:
            job = self.wait_job(self.send()['id'])
            self.assertEqual(job['status'], 'error')
            retry = self.post('/api/jobs/' + job['id'] + '/retry').json()
            self.assertEqual(self.wait_job(retry['id'])['status'], 'done')
        open_tab.assert_called_once()
        self.assertEqual([call.kwargs['selected_tab'] for call in ask.call_args_list], ['new-tab', 'new-tab'])
        self.assertTrue(all(not call.kwargs['new_chat'] for call in ask.call_args_list))

    def test_closed_new_tab_does_not_fall_back_to_dropdown(self):
        with patch('web_api.service.chatgpt.open_new_chatgpt_tab', side_effect=[tab('new-1', 'New'), tab('new-2', 'New')]) as open_tab, \
                patch('web_api.service.chatgpt.list_chrome_chatgpt_tabs') as scan, \
                patch('web_api.service.chatgpt.ask', side_effect=[chatgpt.ChatGPTTabClosed('Closed'), 'Answer']) as ask:
            job = self.wait_job(self.send()['id'])
            self.assertEqual(open_tab.call_count, 1)
            retry = self.post('/api/jobs/' + job['id'] + '/retry').json()
            self.assertEqual(self.wait_job(retry['id'])['status'], 'done')
        scan.assert_not_called()
        self.assertEqual(ask.call_args.kwargs['selected_tab'], 'new-2')

    def test_unchecked_mode_preserves_selected_tab(self):
        with patch('web_api.service.chatgpt.open_new_chatgpt_tab') as open_tab, \
                patch('web_api.service.chatgpt.ask', return_value='Answer') as ask:
            self.assertEqual(self.wait_job(self.send(new_chat=False)['id'])['status'], 'done')
        open_tab.assert_not_called()
        self.assertEqual(ask.call_args.kwargs['selected_tab'], 'old-dropdown')

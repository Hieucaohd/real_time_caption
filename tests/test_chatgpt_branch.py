import unittest
from unittest.mock import Mock, MagicMock, patch

from caption import chatgpt, chatgpt_branch
from caption.chatgpt_source import response_message_at_position
from test_chatgpt_source import conversation_mapping


class BranchResolutionTests(unittest.TestCase):
    def test_resolves_only_active_path_and_matches_saved_response_number(self):
        mapping = conversation_mapping()
        mapping['alternate'] = {'parent': 'u3', 'message': {**mapping['a3']['message'], 'id': 'alternate'}}
        for index in range(1, 5):
            self.assertEqual(response_message_at_position({'mapping': mapping, 'current_node': 'a4'}, index), f'a{index}')
        self.assertEqual(response_message_at_position({'mapping': mapping, 'current_node': 'alternate'}, 3), 'alternate')
        with self.assertRaises(ValueError):
            response_message_at_position({'mapping': mapping, 'current_node': 'alternate'}, 4)

    def test_rejects_unknown_incomplete_or_out_of_range_sources(self):
        snapshot = {'mapping': conversation_mapping(), 'current_node': 'a4'}
        for position in (None, 0, -1, True, '2', 2.5, 5):
            with self.assertRaises(ValueError):
                response_message_at_position(snapshot, position)
        for broken in (None, {}, {'mapping': snapshot['mapping']}, {'mapping': {}, 'current_node': 'a4'}):
            with self.assertRaises(ValueError):
                response_message_at_position(broken, 1)
        del snapshot['mapping']['u2']
        with self.assertRaises(ValueError):
            response_message_at_position(snapshot, 1)


class BranchBrowserTests(unittest.TestCase):
    def setUp(self):
        self.browser = Mock(contexts=[Mock()])
        self.page = MagicMock()
        self.page.url = 'https://chatgpt.com/c/new-branch'
        self.page.title.return_value = 'Branch: parent answer'
        self.page.evaluate.return_value = {'mapping': conversation_mapping(), 'current_node': 'a4'}
        response = self.page.expect_response.return_value.__enter__.return_value.value
        response.ok = True
        response.json.return_value = {'conversation': {'conversation_id': 'new-branch'}}
        self.open = patch.object(chatgpt, '_open_background_tab', return_value=self.page).start()
        self.visible = patch.object(chatgpt, '_visible').start()
        self.addCleanup(patch.stopall)

    def test_opens_native_branch_from_full_link_and_ordinal_in_fresh_tab(self):
        source = 'https://chatgpt.com/g/g-p-project/c/source?keep=yes#fragment'
        result = chatgpt_branch._branch_in_browser(self.browser, source, 2)
        self.assertEqual(self.page.goto.call_args_list[0].args[0], source)
        self.assertEqual(self.page.goto.call_args_list[1].args[0], 'https://chatgpt.com/branch/source/a2')
        self.assertEqual(result['url'], 'https://chatgpt.com/c/new-branch')
        self.assertTrue(result['key'].startswith(chatgpt.CHAT_TAB_PREFIX))
        self.page.bring_to_front.assert_called_once()
        self.page.close.assert_not_called()
        self.open.assert_called_once_with(self.browser)

    def test_bad_source_does_not_open_tab_or_guess_response(self):
        for url in ('', 'https://evil.test/c/source', 'https://chatgpt.com/', 'https://user:pass@chatgpt.com/c/source'):
            with self.assertRaises(ValueError):
                chatgpt_branch._branch_in_browser(self.browser, url, 1)
        self.open.assert_not_called()
        with self.assertRaises(ValueError):
            chatgpt_branch._branch_in_browser(self.browser, 'https://chatgpt.com/c/source', 50)
        self.assertEqual(self.page.goto.call_count, 1)
        self.page.close.assert_called_once()

    def test_uncertain_result_keeps_tab_and_never_retries_creation(self):
        self.page.wait_for_function.side_effect = TimeoutError('Not ready')
        with self.assertRaises(chatgpt.ChatGPTError):
            chatgpt_branch._branch_in_browser(self.browser, 'https://chatgpt.com/c/source', 2)
        self.assertEqual(self.page.goto.call_count, 2)
        self.page.close.assert_not_called()

    def test_rejected_creation_cannot_be_mistaken_for_successful_navigation(self):
        response = self.page.expect_response.return_value.__enter__.return_value.value
        response.ok = False
        with self.assertRaises(chatgpt.ChatGPTError):
            chatgpt_branch._branch_in_browser(self.browser, 'https://chatgpt.com/c/source', 2)
        self.page.bring_to_front.assert_not_called()
        self.page.close.assert_not_called()



class NativeBranchRouteTests(unittest.TestCase):
    def test_native_route_redirect_and_tab_selection_in_real_browser(self):
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            context = browser.new_context()
            requests = []
            composer = '<title>Branch from response</title><form><div id="prompt-textarea" contenteditable="true"></div></form>'
            def serve(route):
                path = route.request.url.removeprefix('https://chatgpt.com')
                if path == '/backend-api/conversation/source':
                    route.fulfill(json={'mapping': conversation_mapping(), 'current_node': 'a4'})
                elif path == '/backend-api/conversation/new_branch':
                    requests.append(route.request.post_data_json)
                    route.fulfill(json={'conversation': {'conversation_id': 'new-branch'}})
                elif path == '/branch/source/a2':
                    route.fulfill(content_type='text/html', body='''<script>
                        fetch('/backend-api/conversation/new_branch', {method:'POST',headers:{'Content-Type':'application/json'},
                          body:JSON.stringify({conversation_id:'source',message_id:'a2'})})
                        .then(r=>r.json()).then(r=>location.href='/g/project/c/'+r.conversation.conversation_id);
                    </script>''')
                elif path in ('/g/project/c/source', '/g/project/c/new-branch'):
                    route.fulfill(content_type='text/html', body=composer)
                else:
                    route.abort()
            context.route('**/*', serve)
            original = context.new_page()
            original.goto('https://chatgpt.com/g/project/c/source')
            # CDP background tab creation is separately exercised by the app; use a fresh test tab here.
            with patch.object(chatgpt, '_open_background_tab', side_effect=lambda _: context.new_page()):
                result = chatgpt_branch._branch_in_browser(browser, original.url, 2)
            self.assertEqual(requests, [{'conversation_id': 'source', 'message_id': 'a2'}])
            self.assertEqual(original.url, 'https://chatgpt.com/g/project/c/source')
            self.assertEqual(result['url'], 'https://chatgpt.com/g/project/c/new-branch')
            selected, created = chatgpt._selected_chatgpt_tab(browser, result['key'])
            self.assertFalse(created)
            self.assertEqual(selected.url, result['url'])
            self.assertEqual(len(context.pages), 2)
            browser.close()

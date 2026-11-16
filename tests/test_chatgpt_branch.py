import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from caption import chatgpt, chatgpt_branch, chatgpt_navigation
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


class TabComparisonTests(unittest.TestCase):
    def test_ignores_source_existing_and_unrelated_new_tabs(self):
        original, helper, unrelated, branch = object(), object(), object(), object()
        before = {'old': {'page': original, 'url': 'https://chatgpt.com/c/source'}}
        before_click = {**before, 'helper': {'page': helper, 'url': 'https://chatgpt.com/c/source'}}
        after = {**before_click, 'noise': {'page': unrelated, 'url': 'https://chatgpt.com/c/other'},
                 'branch': {'page': branch, 'url': 'https://chatgpt.com/g/project/c/created'}}
        self.assertIs(chatgpt_branch._new_branch_tab(before, before_click, after, helper, 'created'), branch)
        after.pop('branch')
        after['old']['url'] = 'https://chatgpt.com/c/created'
        self.assertIsNone(chatgpt_branch._new_branch_tab(before, before_click, after, helper, 'created'))
        after['helper']['url'] = 'https://chatgpt.com/c/created'
        self.assertIs(chatgpt_branch._new_branch_tab(before, before_click, after, helper, 'created'), helper)

    def test_duplicate_matching_new_tabs_are_not_guessed(self):
        after = {str(i): {'page': object(), 'url': 'https://chatgpt.com/c/created'} for i in range(2)}
        self.assertIsNone(chatgpt_branch._new_branch_tab({}, {}, after, object(), 'created'))

    def test_local_route_must_belong_to_confirmed_response_page(self):
        helper, other = object(), object()
        before_click = {'helper': {'page': helper, 'url': 'https://chatgpt.com/c/source'}}
        after = {'helper': {'page': helper, 'url': 'https://chatgpt.com/g/project/c/local-chatgpt%3Adraft-id'},
                 'other': {'page': other, 'url': 'https://chatgpt.com/c/local-chatgpt%3Aunrelated'}}
        self.assertIs(chatgpt_branch._new_branch_tab({}, before_click, after, helper, 'server-id', helper), helper)
        self.assertIs(chatgpt_branch._new_branch_tab({}, before_click, after, helper, None, helper), helper)
        self.assertIsNone(chatgpt_branch._new_branch_tab({}, before_click, after, helper, None))
        # An existing tab can never be promoted to the newly created branch.
        self.assertIsNone(chatgpt_branch._new_branch_tab(before_click, before_click, after, helper, None, helper))


SOURCE_HTML = '''<title>Source</title><form><div id="prompt-textarea" contenteditable="true"></div></form>
<div id="scroll" style="height:300px;overflow:auto;position:relative" data-app-action-timeline-scroll>
<div style="height:2100px;position:relative" id="contents"></div></div>
<script>
window.branchClicks=0;
const host=document.querySelector('#scroll'),contents=document.querySelector('#contents');
function render(){
  const id=host.scrollTop>500?'a4':'a2';
  contents.innerHTML=`<section data-turn-key="${id}" style="position:absolute;top:${id==='a4'?1800:0}px">
    <div data-message-author-role="assistant" data-message-id="${id}">Answer ${id}</div>
    <button aria-label="More actions" onclick="openMenu('${id}')">...</button></section>`;
}
host.addEventListener('scroll',render);host.scrollTop=1800;render();
function openMenu(id){
  document.querySelector('#menu')?.remove();
  const menu=document.createElement('div');menu.id='menu';menu.setAttribute('role','menu');
  if(window.directMenu){menu.innerHTML='<button role="menuitem" id="final">Branch in new chat</button>';}
  else {menu.innerHTML='<button role="menuitem" id="parent">Branch conversation</button><button role="menuitem" id="final" hidden>Branch in new chat</button>';}
  document.body.append(menu);
  if(!window.directMenu){document.querySelector('#parent').onmouseenter=()=>document.querySelector('#final').hidden=false;}
  document.querySelector('#final').onclick=()=>{
    window.branchClicks++;
    if(window.sameTab){branch(id);return;}
    window.open('/c/unrelated','_blank');
    window.open('/native-ui-branch?message='+id,'_blank');
  };
}
async function branch(id){const r=await fetch('/backend-api/conversation/new_branch',{
  method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({conversation_id:'source',message_id:id})});
  if(r.ok){const data=await r.json();
    const destination=window.localBranch?'local-chatgpt%3Adraft-id':data.conversation.conversation_id;
    if(window.discardBranchBody){location.href='/g/project/c/'+destination;}
    else {history.pushState({},'', '/g/project/c/'+destination);document.title='Branch';}
  }}
</script>'''


class NativeBranchUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.requests, self.visits = [], []
        self.reject = self.same_tab = self.direct_menu = False
        self.local_branch = self.discard_body = self.source_id_response = False
        self.context.route('**/*', self.serve)
        self.original = self.context.new_page()
        self.original.goto('https://chatgpt.com/g/project/c/source')
        self.open_patch = patch.object(chatgpt, '_open_background_tab', side_effect=lambda _: self.context.new_page())
        self.open_patch.start()

    def tearDown(self):
        self.open_patch.stop()
        self.context.close()

    def serve(self, route):
        path = route.request.url.removeprefix('https://chatgpt.com')
        if path == '/backend-api/conversation/source':
            route.fulfill(json={'mapping': conversation_mapping(), 'current_node': 'a4'})
        elif path == '/backend-api/conversation/new_branch':
            self.requests.append(route.request.post_data_json)
            route.fulfill(status=403 if self.reject else 200, json={'conversation': {'conversation_id': 'source' if self.source_id_response else 'created'}})
        elif path.startswith('/native-ui-branch?'):
            self.visits.append(path)
            route.fulfill(content_type='text/html', body=SOURCE_HTML + '<script>branch(new URL(location).searchParams.get("message"));</script>')
        elif path.split('?')[0] == '/g/project/c/source':
            route.fulfill(content_type='text/html', body=SOURCE_HTML + f'<script>window.sameTab={str(self.same_tab).lower()};window.directMenu={str(self.direct_menu).lower()};window.localBranch={str(self.local_branch).lower()};window.discardBranchBody={str(self.discard_body).lower()};</script>')
        elif path in ('/', '/g/project/c/created', '/g/project/c/local-chatgpt%3Adraft-id', '/c/unrelated'):
            self.visits.append(path)
            route.fulfill(content_type='text/html', body='<title>Duplicate title</title><form><div id="prompt-textarea" contenteditable="true"></div></form>')
        else:
            route.abort()

    def test_scroll_click_submenu_and_compare_popup_tabs(self):
        result = chatgpt_branch._branch_in_browser(self.browser, self.original.url, 2)
        self.assertEqual(self.requests, [{'conversation_id': 'source', 'message_id': 'a2'}])
        self.assertEqual(self.original.url, 'https://chatgpt.com/g/project/c/source')
        self.assertEqual(result['url'], 'https://chatgpt.com/g/project/c/created')
        selected, created = chatgpt._selected_chatgpt_tab(self.browser, result['key'])
        self.assertFalse(created)
        self.assertEqual(selected.url, result['url'])
        self.assertEqual(len(self.context.pages), 4)  # Original, helper, unrelated popup, real branch.
        helper = next(p for p in self.context.pages if p != self.original and p.url == self.original.url)
        self.assertEqual(helper.evaluate('window.branchClicks'), 1)
        self.assertIn('/native-ui-branch?message=a2', self.visits)

    def test_direct_menu_same_tab_navigation_is_still_new_relative_to_original_tabs(self):
        self.same_tab = self.direct_menu = True
        result = chatgpt_branch._branch_in_browser(self.browser, self.original.url, 2)
        self.assertEqual(result['url'], 'https://chatgpt.com/g/project/c/created')
        self.assertEqual(len(self.context.pages), 2)
        self.assertEqual(len(self.requests), 1)

    def test_rejected_click_is_not_success_and_is_never_retried(self):
        self.reject = True
        with self.assertRaises(chatgpt.ChatGPTError):
            chatgpt_branch._branch_in_browser(self.browser, self.original.url, 2)
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(self.original.url, 'https://chatgpt.com/g/project/c/source')
        self.assertGreater(len(self.context.pages), 1)  # Keep tabs available for inspection.

    def test_native_local_branch_is_selected_without_waiting_for_server_url(self):
        self.same_tab = self.local_branch = True
        result = chatgpt_branch._branch_in_browser(self.browser, self.original.url, 2)
        self.assertEqual(result['url'], 'https://chatgpt.com/g/project/c/local-chatgpt%3Adraft-id')
        self.assertEqual(len(self.requests), 1)
        selected, _ = chatgpt._selected_chatgpt_tab(self.browser, result['key'])
        self.assertEqual(selected.url, result['url'])

    def test_local_branch_survives_lost_response_body_and_source_server_id(self):
        self.same_tab = self.local_branch = self.source_id_response = True
        self.discard_body = True
        result = chatgpt_branch._branch_in_browser(self.browser, self.original.url, 2)
        self.assertIn('/c/local-chatgpt%3A', result['url'])
        self.assertEqual(len(self.requests), 1)

    def test_invalid_ordinal_does_not_click_any_response(self):
        with self.assertRaises(ValueError):
            chatgpt_branch._branch_in_browser(self.browser, self.original.url, 99)
        self.assertEqual(self.requests, [])
        self.assertEqual(len(self.context.pages), 1)

    def test_open_fresh_chatgpt_tab_keeps_original_and_is_selectable(self):
        tab = chatgpt._open_new_chatgpt_tab(self.browser)
        self.assertEqual(tab.url, 'https://chatgpt.com/')
        self.assertEqual(self.original.url, 'https://chatgpt.com/g/project/c/source')
        selected, created = chatgpt._selected_chatgpt_tab(self.browser, tab.key)
        self.assertFalse(created)
        self.assertEqual(selected.url, tab.url)
        self.assertEqual(len(self.context.pages), 2)
        self.assertEqual(self.requests, [])

    def test_open_saved_response_scrolls_virtual_history_without_branching(self):
        url = 'https://chatgpt.com/g/project/c/source?keep=yes#saved'
        before_scroll = self.original.locator('#scroll').evaluate('el => el.scrollTop')
        result = chatgpt_navigation._open_response_in_browser(self.browser, url, 2)
        self.assertEqual(result['url'], url)
        self.assertEqual(result['response_position'], 2)
        opened, created = chatgpt._selected_chatgpt_tab(self.browser, result['key'])
        self.assertFalse(created)
        self.assertNotEqual(opened, self.original)
        self.assertEqual(len(self.context.pages), 2)
        self.assertEqual(opened.evaluate('window.branchClicks'), 0)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.original.locator('#scroll').evaluate('el => el.scrollTop'), before_scroll)
        self.assertTrue(opened.locator('[data-message-id="a2"]').evaluate('''el => {
            const a=el.getBoundingClientRect(), b=document.querySelector('#scroll').getBoundingClientRect();
            return a.top >= b.top && a.bottom <= b.bottom;
        }'''))

    def test_open_response_reports_missing_ordinal_and_keeps_new_tab(self):
        with self.assertRaises(chatgpt.ChatGPTError):
            chatgpt_navigation._open_response_in_browser(self.browser, self.original.url, 99)
        self.assertEqual(len(self.context.pages), 2)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.original.url, 'https://chatgpt.com/g/project/c/source')

    def test_open_response_validates_source_before_opening_a_tab(self):
        for url, position in [('https://example.org/c/source', 2), (self.original.url, 0), ('', None)]:
            with self.assertRaises(ValueError):
                chatgpt_navigation._open_response_in_browser(self.browser, url, position)
        self.assertEqual(len(self.context.pages), 1)

    def test_request_lock_is_per_real_target_and_released_after_failure(self):
        from caption import paths
        from caption.shared_access import BusyError
        first = chatgpt.CHAT_TAB_PREFIX + 'first'
        self.original.evaluate('key => window.name = key', first)
        second = chatgpt._open_new_chatgpt_tab(self.browser)
        with tempfile.TemporaryDirectory() as folder, patch.object(paths, 'CACHE_DIR', Path(folder)):
            with self.assertRaisesRegex(RuntimeError, 'failed request'):
                with chatgpt._request_page(self.browser, first, False, ''):
                    # A different tab is available while the first tab waits for its response.
                    with chatgpt._request_page(self.browser, second.key, False, '') as (other, created):
                        self.assertFalse(created)
                        self.assertNotEqual(other, self.original)
                    with self.assertRaises(BusyError):
                        with chatgpt._request_page(self.browser, first, False, ''):
                            self.fail('Same tab must not be shared')
                    # Current-tab and selected-tab modes must contend on the same CDP ID.
                    with patch.object(chatgpt, '_current_chatgpt_tab', return_value=(self.original, False)):
                        with self.assertRaises(BusyError):
                            with chatgpt._request_page(self.browser, None, True, ''):
                                self.fail('Alias bypassed target reservation')
                    raise RuntimeError('failed request')
            with chatgpt._request_page(self.browser, first, False, '') as (reopened, _):
                self.assertEqual(reopened, self.original)

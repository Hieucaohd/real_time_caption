"""Real Chrome gesture regression checks, using only a temporary database.

Run after npm --prefix web run build:
    .venv/Scripts/python.exe -X utf8 tests/check_research_graph_browser.py
"""
import json
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from test_web_api import WebApiTests
from web_api.server import create_app


def main():
    fixture = WebApiTests(); fixture.setUp()
    fixture.service.busy = True  # Never send anything to real ChatGPT during UI tests.
    store = fixture.service.research.store
    fixture.service.store.add_message(fixture.cid, 'user', 'You', 'Explore calculus')
    mid = fixture.service.store.add_message(fixture.cid, 'bot', 'ChatGPT', 'Limits, derivatives and integrals.')
    root = store.import_answer(fixture.cid, mid)
    children = fixture.service.research.children(fixture.cid, root['id'], {'questions':['Limits','Derivatives'], 'config':{'selected_tab':'test'}})
    a, b = [n['id'] for n in children]
    for child in children: store.update(fixture.cid, child['id'], status='done', answer='A complete explanation.')
    c = fixture.service.research.children(fixture.cid, a, {'questions':['A deeper example'], 'config':{'selected_tab':'test'}})[0]['id']
    store.update(fixture.cid, c, status='done', answer='A concrete example.')
    store.update_response_source(fixture.cid, {'response_conversation_url':'https://chatgpt.com/c/source', 'response_position':2}, nid=root['id'])
    server = uvicorn.Server(uvicorn.Config(create_app(fixture.service), host='127.0.0.1', port=0, log_level='error'))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    deadline=time.monotonic()+10
    while not server.started:
        if time.monotonic()>deadline: raise RuntimeError('Test server did not start')
        time.sleep(.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel='chrome', headless=True)
            page = browser.new_page(viewport={'width':1600,'height':1050})
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            opened=[]
            def open_response(route):
                opened.append(route.request.url)
                if len(opened) == 2:
                    route.fulfill(status=400, json={'detail':'Response unavailable'})
                else:
                    route.fulfill(json={'key':'opened-tab', 'response_position':2})
            page.route('**/research/*/open-chatgpt', open_response)
            page.goto(f'http://127.0.0.1:{port}')
            page.locator('nav button').nth(2).click()
            canvas=page.locator('.graph-canvas')
            expect(page.locator('.research-node')).to_have_count(4)
            def node(nid):return page.locator(f'[data-node-id="{nid}"]')
            def xy(nid):return node(nid).evaluate('el=>({x:+el.dataset.x,y:+el.dataset.y})')
            def camera():return json.loads(page.locator('.graph-world').get_attribute('data-camera'))
            def center(nid):
                r=node(nid).bounding_box();return r['x']+r['width']/2,r['y']+r['height']/2
            def saved():expect(page.locator('.graph-save.saved')).to_be_visible(timeout=5000)
            def drag(start,dx,dy,button='left'):
                page.mouse.move(*start);page.mouse.down(button=button)
                page.mouse.move(start[0]+dx,start[1]+dy,steps=12);page.mouse.up(button=button)
                page.wait_for_timeout(60)
            def close(actual,expected,tolerance=.02):assert abs(actual-expected)<=tolerance,(actual,expected)
            def fit():page.get_by_title('Vừa khung',exact=True).click()
            saved()

            # The graph icon opens its own source without moving/selecting the node.
            expect(node(a).get_by_role('button',name='Mở trên ChatGPT: Limits',exact=True)).to_be_disabled()
            before_open=xy(root['id'])
            node(root['id']).get_by_role('button',name='Mở trên ChatGPT: Explore calculus',exact=True).click()
            expect(page.locator('.research-notice')).to_contain_text('câu trả lời thứ 2')
            assert xy(root['id']) == before_open
            assert len(opened) == 1 and root['id'] in opened[0]
            page.get_by_role('button',name='Mở trên ChatGPT',exact=True).click()
            expect(page.get_by_text('Response unavailable',exact=True)).to_be_visible()
            expect(page.get_by_role('button',name='Mở trên ChatGPT',exact=True)).to_be_enabled()
            assert len(opened) == 2

            # Unbounded panning with each supported mouse/keyboard gesture.
            for button,hold in [('left',False),('middle',False),('right',False),('left',True)]:
                fit();before=camera();positions=xy(root['id']);r=canvas.bounding_box()
                start=center(root['id']) if button!='left' or hold else (r['x']+25,r['y']+r['height']-25)
                if hold:canvas.focus();page.keyboard.down('Space')
                drag(start,95,45,button)
                if hold:page.keyboard.up('Space')
                after=camera();close(after['x']-before['x'],95);close(after['y']-before['y'],45)
                assert xy(root['id'])==positions

            # Mouse-wheel zoom is anchored to the pointer, not the upper-left corner.
            fit();r=canvas.bounding_box();mouse=(round(r['x']+200),round(r['y']+180))
            before=camera();world=((mouse[0]-r['x']-before['x'])/before['zoom'],(mouse[1]-r['y']-before['y'])/before['zoom'])
            page.mouse.move(*mouse);page.mouse.wheel(0,-180);page.wait_for_timeout(150)
            after=camera();assert after['zoom']>before['zoom']
            close((mouse[0]-r['x']-after['x'])/after['zoom'],world[0]);close((mouse[1]-r['y']-after['y'])/after['zoom'],world[1])

            # A node moves in world coordinates, with exact undo and redo.
            fit();page.get_by_label('Bám lưới',exact=True).uncheck();before=xy(a);z=camera()['zoom']
            drag(center(a),65,35);after=xy(a)
            close(after['x']-before['x'],65/z,1.5);close(after['y']-before['y'],35/z,1.5)
            page.keyboard.press('Control+z');assert xy(a)==before
            page.keyboard.press('Control+Shift+z');assert xy(a)==after

            # Escape cancels a drag, including one that leaves the canvas.
            fit();before=xy(a);start=center(a);page.mouse.move(*start);page.mouse.down()
            page.mouse.move(start[0]+30,start[1]+70,steps=8);page.keyboard.press('Escape');page.mouse.up();assert xy(a)==before

            # Ctrl-selection and group movement preserve relative distances.
            fit();node(a).locator('.research-node').click()
            page.keyboard.down('Control');node(b).locator('.research-node').click();page.keyboard.up('Control')
            expect(page.locator('.research-node.picked')).to_have_count(2)
            before_a,before_b,before_c=xy(a),xy(b),xy(c);drag(center(a),35,-20)
            close(xy(a)['x']-before_a['x'],xy(b)['x']-before_b['x']);close(xy(a)['y']-before_a['y'],xy(b)['y']-before_b['y']);assert xy(c)==before_c

            # A collapsed descendant follows its parent when subtree movement is on.
            fit();node(a).locator('.research-node').click();node(a).locator('.research-collapse').click()
            expect(node(c)).to_have_count(0);saved();before_c=store.graph_layout(fixture.cid,root['id'])['positions'][c]
            page.get_by_label('Kéo cả nhánh',exact=True).check();before_a=xy(a);drag(center(a),40,15);saved()
            after_c=store.graph_layout(fixture.cid,root['id'])['positions'][c]
            close(after_c['x']-before_c['x'],xy(a)['x']-before_a['x']);close(after_c['y']-before_c['y'],xy(a)['y']-before_a['y'])
            page.get_by_label('Kéo cả nhánh',exact=True).uncheck();node(a).locator('.research-collapse').click()

            # Rubber-band selection and node positions survive normal status polling.
            before_arrange={nid:xy(nid) for nid in (root['id'],a,b,c)}
            page.get_by_role('button',name='Sắp xếp lại',exact=True).click();saved()
            arranged={nid:xy(nid) for nid in before_arrange}
            assert arranged != before_arrange
            assert arranged[root['id']]['x'] < arranged[a]['x'] < arranged[c]['x']
            assert arranged[a]['x'] == arranged[b]['x']
            assert arranged[b]['y'] - arranged[a]['y'] >= 78
            assert store.graph_layout(fixture.cid,root['id'])['positions'] == arranged
            page.get_by_title('Hoàn tác bố cục (Ctrl+Z)',exact=True).click()
            assert {nid:xy(nid) for nid in before_arrange} == before_arrange
            page.get_by_title('Làm lại (Ctrl+Shift+Z)',exact=True).click()
            assert {nid:xy(nid) for nid in arranged} == arranged
            fit()
            r=canvas.bounding_box();page.mouse.click(r['x']+15,r['y']+r['height']-20)
            ra,rb=node(a).bounding_box(),node(b).bounding_box();start=(min(ra['x'],rb['x'])-8,min(ra['y'],rb['y'])-8);end=(max(ra['x']+ra['width'],rb['x']+rb['width'])+8,max(ra['y']+ra['height'],rb['y']+rb['height'])+8)
            page.keyboard.down('Shift');drag(start,end[0]-start[0],end[1]-start[1]);page.keyboard.up('Shift')
            expect(page.locator('.research-node.picked')).to_have_count(2)
            drag(center(a),25,20);before=xy(a);page.wait_for_timeout(1200);assert xy(a)==before

            # New child creation must not rearrange already positioned nodes.
            new=fixture.service.research.children(fixture.cid,b,{'questions':['New child'],'config':{'selected_tab':'test'}})[0]
            store.update(fixture.cid,new['id'],status='done',answer='New answer')
            expect(node(new['id'])).to_have_count(1);assert xy(a)==before

            # Failed autosaves retain local changes and can be retried.
            def fail_patch(route):
                if route.request.method=='PATCH':route.abort()
                else:route.continue_()
            saved();page.route('**/layout',fail_patch);fit();drag(center(root['id']),15,10)
            expect(page.locator('.graph-save.error')).to_be_visible()
            failed_position=xy(root['id']);page.unroute('**/layout',fail_patch)
            page.locator('.graph-save.error button').click();saved()
            assert store.graph_layout(fixture.cid,root['id'])['positions'][root['id']]==failed_position

            # Read-back after reload must come from SQLite, even without localStorage.
            node(a).locator('.research-collapse').click();page.get_by_label('Lăn để zoom',exact=True).uncheck();saved()
            persisted=store.graph_layout(fixture.cid,root['id'])
            page.evaluate('localStorage.clear()');page.reload();page.locator('nav button').nth(2).click()
            expect(node(root['id'])).to_be_visible();saved()
            for nid,p0 in persisted['positions'].items():
                if nid not in (c,):assert xy(nid)==p0
            assert camera()==persisted['camera'];expect(node(c)).to_have_count(0)
            expect(page.get_by_label('Lăn để zoom',exact=True)).not_to_be_checked()
            page.screenshot(path='cache/research-graph-interactions.png',full_page=True)
            assert not errors,errors
            print('Graph browser checks: pan, pointer zoom, drag, undo/redo, cancel, groups, subtree, selection, save retry and SQLite reload passed.')
            browser.close()
    finally:
        server.should_exit=True;thread.join(6);fixture.tearDown()


if __name__=='__main__':main()

"""Repeatable synthetic load + browser interaction benchmark; never uses user data."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import statistics
import sys
import threading
import time
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from playwright.sync_api import sync_playwright, expect
from test_web_api import WebApiTests
from caption.settings import Settings
from caption.translation import Config
from web_api.server import create_app


def summary(values):
    values = sorted(values)
    return {'count': len(values), 'median_ms': round(statistics.median(values), 2),
            'p95_ms': round(values[min(len(values)-1, int(len(values)*.95))], 2),
            'max_ms': round(max(values), 2)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--label', default='current')
    parser.add_argument('--lines', type=int, default=1000)
    args = parser.parse_args()
    fixture = WebApiTests(); fixture.setUp()
    settings = Settings.load(); settings.translation_engine = 'google_web'; settings.save()
    config = Config.from_settings(settings)
    store = fixture.service.translations
    transcript = fixture.service.transcript_for(fixture.cid)
    texts = [f'Sentence {i}: This is a synthetic transcript for measuring translation performance and browser responsiveness.' for i in range(args.lines)]
    (transcript.folder / 'transcript.txt').write_text('\n'.join('[2026-01-01 00:00:00] '+t for t in texts)+'\n', encoding='utf-8')
    store.sync_transcript(transcript)
    lines = store.lines(fixture.cid)
    for index in range(3):
        start, end = index * (args.lines//4), (index+1) * (args.lines//4)
        store.append_google(fixture.cid, config, ' '.join(texts[start:end]),
            ' '.join('Translated '+t for t in texts[start:end]), str(index), lines[start-1]['origin'] if start else '')
    original = store.connect
    writes = [0]
    @contextmanager
    def tracked():
        with original() as db:
            def trace(sql):
                if sql.startswith('INSERT INTO translation_lines'): writes[0] += 1
            db.set_trace_callback(trace)
            yield db
    store.connect = tracked
    sync_times = []
    for _ in range(10):
        start=time.perf_counter(); store.sync_transcript(transcript)
        sync_times.append((time.perf_counter()-start)*1000)
    report = {'lines':args.lines, 'unchanged_sync':summary(sync_times), 'unchanged_sync_writes':writes[0]}
    server = uvicorn.Server(uvicorn.Config(create_app(fixture.service), host='127.0.0.1', port=0,
        ws='websockets-sansio', log_level='error'))
    thread=threading.Thread(target=server.run,daemon=True); thread.start()
    while not server.started: time.sleep(.02)
    root=f'http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}'
    times, errors, write_times = [], [], []
    stop=threading.Event()
    def writer():
        revision=0
        while not stop.is_set():
            start=time.perf_counter()
            try:
                store.append_google(fixture.cid,config,'Live source',f'Live result {revision}','live')
                fixture.service.worker.on_google_update(fixture.cid,config)
            except Exception as e: errors.append(type(e).__name__)
            write_times.append((time.perf_counter()-start)*1000)
            revision+=1; stop.wait(.05)
    def reader():
        for _ in range(15):
            start=time.perf_counter()
            try:
                with urllib.request.urlopen(root+'/api/conversations/'+fixture.cid,timeout=12) as response: response.read()
            except Exception as e: errors.append(type(e).__name__)
            times.append((time.perf_counter()-start)*1000)
    try:
        worker=threading.Thread(target=writer,daemon=True); worker.start()
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda _: reader(),range(3)))
        stop.set(); worker.join(12)
        report.update(api_under_writes=summary(times), translation_write=summary(write_times), errors=errors)
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            page=browser.new_page(viewport={'width':1440,'height':900})
            page.goto(root); page.locator('nav button').nth(4).click()
            expect(page.locator('.translation-reading')).to_be_visible()
            expect(page.locator('.translation-reading-text').first.locator('[data-line-id]')).to_have_count(args.lines)
            hover=page.evaluate('''async () => {
                const nodes = document.querySelectorAll('.translation-reading-text:first-of-type [data-line-id]');
                const frames = [];
                for (let i=0;i<12;i++) {
                    const node = nodes[Math.floor(nodes.length / 4) * (i % 3)];
                    const t = performance.now();
                    node.dispatchEvent(new MouseEvent('mouseover', {bubbles:true, relatedTarget:document.body}));
                    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
                    frames.push(performance.now()-t);
                    node.dispatchEvent(new MouseEvent('mouseout', {bubbles:true, relatedTarget:document.body}));
                }
                return frames;
            }''')
            report['hover_to_paint']=summary(hover)
            browser.close()
        path=Path('cache') / f'translation-performance-{args.label}.json'
        path.write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))
    finally:
        stop.set(); server.should_exit=True; thread.join(10); fixture.tearDown()


if __name__ == '__main__': main()

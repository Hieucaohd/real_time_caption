"""Offline smoke test run by the web EXE builder with an isolated data folder."""
import json
import traceback
from pathlib import Path

from caption import paths


def run():
    report = {'ok': False, 'checks': [], 'data_dir': str(paths.DATA_DIR)}
    try:
        from caption import defaults
        defaults.check()
        report['checks'].append('bundled-release-defaults')
        from fastapi.testclient import TestClient
        from .server import create_app
        from caption import mathjax_render, markdown_html
        from caption.answer_export import prepare_html, export_html
        from playwright.sync_api import sync_playwright
        from playwright._impl._driver import compute_driver_executable
        import uiautomation
        import tkinter
        from . import overlay, screen
        from uvicorn.protocols.websockets.websockets_sansio import WebSocketsSansIOProtocol

        driver = Path(compute_driver_executable()[0])
        assert driver.is_file(), f'Missing Playwright driver: {driver}'
        with sync_playwright() as playwright:
            assert playwright.chromium.name == 'chromium'
        report['checks'].append('playwright-driver')
        with uiautomation.UIAutomationInitializerInThread():
            assert uiautomation.GetRootControl() is not None
        report['checks'].append('windows-ui-automation')
        assert (paths.BUNDLE_DIR / 'web/dist/index.html').is_file()
        assert list((paths.BUNDLE_DIR / 'web/dist/assets').glob('*.woff2'))
        assert list(paths.PROMPTS_DIR.glob('chat/*.txt'))
        with TestClient(create_app()) as client:
            assert client.get('/api/health').json()['data_dir'] == str(paths.DATA_DIR)
            assert client.get('/').status_code == 200
            assert client.get('/assets/main.js').status_code == 200
            assert client.get('/assets/main.css').status_code == 200
            assert client.get('/api/bootstrap').status_code == 200
            headers = {'X-RTC-Client': 'web'}
            cid = client.post('/api/conversations', json={'title': 'Packaged smoke test'}, headers=headers).json()['id']
            assert client.get(f'/api/conversations/{cid}/research').json() == []
            with client.websocket_connect(f'/api/conversations/{cid}/translation-stream',
                                          headers={'origin': 'http://testserver'}) as socket:
                assert socket.receive_json()['conversation_id'] == cid
        report['checks'].append('translation-websocket')
        report['checks'].append('react-api-sqlite-prompts-fonts')
        # This verifies native libraries and runtime data used by Word/PDF export.
        mathjax_render._load_bridge()
        rendered = prepare_html(r'## Export test' + '\n\n' + r'\[\frac{1}{2}\]')
        assert 'data:image/png;base64,' in rendered
        for kind in ('pdf', 'docx'):
            output = paths.DATA_DIR / f'packaged-test.{kind}'
            export_html(rendered, output)
            assert output.stat().st_size > 100
        report['checks'].append('mathjax-pdf-docx')
        report['ok'] = True
    except Exception:
        report['error'] = traceback.format_exc()
    (paths.DATA_DIR / 'web-selftest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return 0 if report['ok'] else 1

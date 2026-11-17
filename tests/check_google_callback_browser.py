"""Regression: a real MutationObserver must never reenter Playwright's dispatcher."""
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from caption import google_translate as google


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        context = browser.new_context()
        context.route('**/*', lambda route: route.fulfill(content_type='text/html', body='''
            <textarea jsname="BJE2fc"></textarea><span class="ryNqvb"></span>
            <script>document.querySelector('textarea').addEventListener('input', event => {
                setTimeout(() => document.querySelector('.ryNqvb').textContent = 'Translated ' + event.target.value, 30);
            });</script>'''))
        old, new = context.new_page(), context.new_page()
        for page in (old, new):
            page.goto('https://translate.google.com/?sl=en&tl=vi&op=translate&hl=en')
        old.evaluate('name => window.name = name', google.TAB_NAME + ':old')
        saved = []
        start = time.perf_counter()
        with patch.object(google, '_choose_tab', return_value=new):
            result = google._translate_stream(browser, 'Live speech', 'en', 'vi', None,
                'current', None, saved.append, {})
        elapsed = time.perf_counter() - start
        assert result == 'Translated Live speech', result
        assert old.is_closed() and not new.is_closed()
        assert saved[-1] == result
        assert elapsed < 5, elapsed
        print(f'Real observer + save + old-tab close completed in {elapsed:.3f}s; no callback reentrancy.')
        browser.close()


if __name__ == '__main__':
    main()

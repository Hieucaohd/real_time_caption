"""Exercise effort selection in real Chromium with a deterministic model picker."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from playwright.sync_api import sync_playwright
from caption.chatgpt import _set_power, ChatGPTError

HTML = '''<button aria-label="Select ChatGPT model" aria-expanded="false">High</button>
<button id="decoy">Another control</button>
<div role="menu" hidden>
<div role="menuitem" data-reasoning-slider="true" tabindex="0">Power
<span role="slider" aria-valuemin="0" aria-valuemax="2" aria-valuenow="2" style="display:inline-block;width:8px;height:8px"></span>
</div><span role="status">High, 3 of 3.</span></div>
<script>
const picker=document.querySelector('button'), menu=document.querySelector('[role=menu]'),
 power=document.querySelector('[role=menuitem]'), slider=document.querySelector('[role=slider]'), status=document.querySelector('[role=status]');
let current=2, saved=2;
window.opens=0; window.reject=false; window.steal=false;
function render() {slider.setAttribute('aria-valuenow', current);status.textContent=['Instant','Medium','High'][current]+', '+(current+1)+' of 3.';}
picker.onclick=()=>{menu.hidden=false;picker.setAttribute('aria-expanded','true');current=saved;render();window.opens++;power.focus();};
power.onkeydown=e=>{
 if(e.key==='ArrowLeft'||e.key==='ArrowRight') {e.preventDefault();current=Math.max(0,Math.min(2,current+(e.key==='ArrowLeft'?-1:1)));render();if(window.steal)document.querySelector('#decoy').focus();}
 if(e.key==='Escape'){saved=window.reject?saved:current;menu.hidden=true;picker.setAttribute('aria-expanded','false');picker.textContent=['Instant','Medium','High'][saved];}
};
</script>'''


def main():
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome', headless=True)
        page=browser.new_page()
        page.set_content(HTML)
        page.evaluate('window.steal=true')
        assert _set_power(page,0)=='Instant'  # Focus stolen after each arrow.
        assert page.get_by_label('Select ChatGPT model').inner_text()=='Instant'
        assert page.evaluate('window.opens')==2  # Reopened to confirm committed value.
        assert _set_power(page,1)=='Medium'
        assert _set_power(page,2)=='High'
        before=page.evaluate('window.opens')
        assert _set_power(page,2)=='High'
        assert page.evaluate('window.opens')==before+1  # No change needed.
        page.evaluate('window.reject=true')
        try:
            _set_power(page,0)
            raise AssertionError('An uncommitted slider value was reported as success')
        except ChatGPTError:
            pass
        assert page.locator('[role=menu]').is_hidden()
        page.evaluate("slider.setAttribute('aria-valuemax','1')")
        try:
            _set_power(page,2)
            raise AssertionError('Unsupported High was silently downgraded')
        except ChatGPTError:
            pass
        browser.close()
        print('Power browser checks passed: 3 levels, focus changes, unchanged effort, persisted value verification and unsupported effort.')


if __name__=='__main__': main()

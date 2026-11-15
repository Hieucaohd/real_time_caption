"""Apply externally saved preferences to idle desktop controls, preserving local edits."""
from dataclasses import asdict

from .settings import Settings
from .translation import ENGINES


def refresh_settings(app):
    current = app.settings
    latest = Settings.load()
    baseline = getattr(current, '_baseline', asdict(current))
    controls = {
        'summary_new_chat': app.new_chat_var,
        'summary_attach_transcript': app.summary_attach_transcript_var,
        'summary_max_lines': app.summary_max_lines_var,
        'summary_prompt': app.prompt_var,
        'chat_max_lines': app.chat.max_lines_var,
        'chat_attach_history': app.chat.history_var,
        'chat_screenshot': app.chat.screenshot_var,
        'chat_prompt': app.chat.prompt_var,
        'voca_api_key': app.vocab.key_var,
        'translation_source': app.translation.source_var,
        'translation_target': app.translation.target_var,
        'translation_device': app.translation.device_var,
        'translation_enabled': app.translation.enabled_var,
        'translation_partial': app.translation.partial_var,
        'translation_batch_size': app.translation.batch_size_var,
        'translation_batch_timeout': app.translation.batch_timeout_var,
        **app.translation._fields,
    }
    powers = {'summary_chatgpt_power':app.summary_power_var, 'chat_chatgpt_power':app.chat.power_var,
              'vocab_chatgpt_power':app.vocab.power_var}
    changed = set()
    for key, value in asdict(latest).items():
        old = baseline.get(key)
        if value == old or getattr(current, key) != old:
            continue
        control = controls.get(key)
        if control is not None:
            if str(control.get()) != str(old):
                continue
            control.set(value)
        if key in powers:
            if powers[key].get() != app._power_choices()[old + 1]:
                continue
            powers[key].set(app._power_choices()[value + 1])
        if key == 'translation_engine':
            if app.translation.engine() != old:
                continue
            app.translation.engine_var.set(ENGINES[value])
            for page in app.translation._config_pages.values():
                page.pack_forget()
            app.translation._config_pages[value].pack(fill='x')
        if key == 'translation_gemini_prompt':
            if app.translation.prompt.get('1.0','end-1c') != old:
                continue
            app.translation.prompt.delete('1.0','end')
            app.translation.prompt.insert('1.0',value)
        setattr(current, key, value)
        baseline[key] = value
        changed.add(key)
    current._baseline = baseline
    if 'chatgpt_selected_tab' in changed:
        app.chat.refresh_chrome_tabs()
    if 'summary_chatgpt_selected_tab' in changed:
        app.refresh_summary_chrome_tabs()
    if 'voca_collection_id' in changed:
        # Keep an ID not yet loaded from Voca selectable until the next refresh.
        cid = current.voca_collection_id
        label = next((name for name, value in app.vocab._collections.items() if value == cid), cid)
        app.vocab._collections[label] = cid
        app.vocab.collection_var.set(label)
    if any(key.startswith('overlay_') for key in changed):
        app.overlay.font.configure(size=current.overlay_font_size)
        app.overlay.set_transparent(current.overlay_transparent)
        app.overlay.set_click_through(current.overlay_click_through)
        app.transparent_var.set(current.overlay_transparent)
        app.click_through_var.set(current.overlay_click_through)
        app.overlay._apply_mode()
        app.overlay._apply_geometry()

"""Release defaults, read from the bundle, never from the user's data folder."""
from copy import deepcopy
from functools import lru_cache
import json
import math
from pathlib import Path
from typing import get_type_hints

from . import paths


def validate(data):
    # Imported lazily: Settings factories call us only after the class exists.
    from .settings import Settings
    schemas = {
        'settings': get_type_hints(Settings),
        'realtime_chat': dict(interval=int, prompt=(str, type(None)), power=(int, type(None)),
                             summary_power=(int, type(None)), screenshot=(bool, type(None)),
                             actions_prompt=str, summary_prompt=str),
        'research': dict(prompt=(str, type(None)), power=(int, type(None)),
                         attach_transcript=(bool, type(None)), attach_history=(bool, type(None)),
                         max_lines=(int, type(None)), screenshot=(bool, type(None)),
                         new_chat=bool, ancestor_depth=int, tab_mode=str),
        'realtime_popup': dict(opacity=int, font_size=int, width=int, height=int),
        'web': dict(theme=str, initial_tab=str, sidebar_hidden=bool, reading_focus=bool,
                    transcript_auto_scroll=bool, realtime_auto_scroll=bool),
        'livecaptions': dict(microphone=(bool, type(None)), language=str),
        'research_graph': dict(snap=bool, wheel_zoom=bool, mode=str, move_subtree=bool),
    }
    if not isinstance(data, dict) or set(data) != {'schema_version', *schemas}:
        raise ValueError('defaults.json: missing or unknown configuration section')
    if type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise ValueError('defaults.json: schema_version must be 1')
    for section, schema in schemas.items():
        values = data[section]
        if not isinstance(values, dict) or set(values) != set(schema):
            raise ValueError(f'defaults.json: missing or unknown keys in {section}')
        for key, expected in schema.items():
            types = expected if isinstance(expected, tuple) else (expected,)
            if expected is float:
                types = (int, float)
            value = values[key]
            if type(value) not in types or isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f'defaults.json: invalid type for {section}.{key}')

    def choice(section, key, allowed):
        if data[section][key] not in allowed:
            raise ValueError(f'defaults.json: invalid {section}.{key}; choose from {allowed}')

    def between(section, key, low, high):
        value = data[section][key]
        if value is not None and not low <= value <= high:
            raise ValueError(f'defaults.json: {section}.{key} must be between {low} and {high}')

    choice('settings', 'web_capture_source', ('windows', 'chatgpt'))
    choice('settings', 'translation_engine', ('azure', 'google', 'chrome', 'google_web', 'gemini', 'vinai'))
    choice('settings', 'translation_view', ('history', 'parallel'))
    choice('settings', 'translation_device', ('cpu', 'cuda', 'auto'))
    for key in ('translation_source', 'translation_target'):
        choice('settings', key, ('en', 'vi', 'ja', 'ko', 'zh', 'fr', 'de', 'es'))
    if data['settings']['translation_source'] == data['settings']['translation_target']:
        raise ValueError('defaults.json: translation source and target must differ')
    for key in ('summary_chatgpt_power', 'chat_chatgpt_power', 'vocab_chatgpt_power', 'research_power'):
        between('settings', key, -1, 2)
    for key in ('summary_max_lines', 'chat_max_lines', 'research_max_lines'):
        between('settings', key, 0, 1_000_000)
    between('settings', 'translation_batch_size', 1, 50)
    between('settings', 'translation_batch_timeout', .1, 300)
    between('settings', 'overlay_font_size', 8, 96)
    between('settings', 'overlay_lines', 1, 10)
    between('settings', 'overlay_alpha', .1, 1)
    between('realtime_chat', 'interval', 5, 3600)
    for key in ('power', 'summary_power'):
        between('realtime_chat', key, -1, 2)
    for key in ('actions_prompt', 'summary_prompt'):
        if not 1 <= len(data['realtime_chat'][key].strip()) <= 200_000:
            raise ValueError(f'defaults.json: realtime_chat.{key} must contain 1-200000 characters')
    between('research', 'power', -1, 2)
    between('research', 'max_lines', 0, 1_000_000)
    between('research', 'ancestor_depth', -1, 10000)
    choice('research', 'tab_mode', ('existing', 'new', 'branch'))
    between('realtime_popup', 'opacity', 40, 100)
    between('realtime_popup', 'font_size', 1, 32)
    for key in ('width', 'height'):
        between('realtime_popup', key, 200, 4096)
    choice('web', 'theme', ('light', 'dark'))
    choice('web', 'initial_tab', ('transcript', 'chat', 'research', 'summary', 'translation', 'vocab', 'realtime'))
    choice('research_graph', 'mode', ('select', 'hand'))
    return data


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'defaults.json: duplicate key {key}')
        result[key] = value
    return result


@lru_cache(maxsize=4)
def _load(path):
    try:
        return validate(json.loads(Path(path).read_text(encoding='utf-8-sig'), object_pairs_hook=_unique))
    except (OSError, ValueError) as exc:
        raise ValueError(f'Cannot load release defaults from {path}: {exc}') from exc


def get(section, key=None):
    values = _load(paths.BUNDLE_DIR / 'config' / 'defaults.json')[section]
    return deepcopy(values if key is None else values[key])


def check():
    """Validate the exact bundled file before packaging or starting a smoke test."""
    _load.cache_clear()
    _load(paths.BUNDLE_DIR / 'config' / 'defaults.json')


if __name__ == '__main__':
    check()
    print('config/defaults.json is valid')

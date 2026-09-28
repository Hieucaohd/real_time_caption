"""New-word helpers: find the caption line a word came from, ask ChatGPT for a structured
translation, and turn the result into Voca items.

ChatGPT is asked (via the editable ``prompts/vocabulary.txt``) to answer with a JSON array;
the parser accepts it inside a ```json fence or bare, and tolerates a single object.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .paths import PROMPTS_DIR

VOCAB_PROMPT_PATH = PROMPTS_DIR / "vocabulary.txt"
VOCA_SOURCE = "realtime_caption"  # shown in Voca as "via realtime_caption"

_CAPTION_LINE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s*(.*)$")
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)


class VocabError(RuntimeError):
    pass


@dataclass
class NewWord:
    word: str
    sentence: str = ""  # caption line containing the word, if found
    time: str = ""  # its [HH:MM:SS]
    # Filled from ChatGPT's answer
    translation: str = ""
    meaning: str = ""
    part_of_speech: str = ""
    phonetic: str = ""
    context_translation: str = ""
    # Filled from Voca's answer
    voca_status: str = ""  # created | updated | unchanged | failed | "" (not sent)
    voca_error: str = ""


def parse_input(text: str) -> list[str]:
    """One word or phrase per line (commas/semicolons also split); duplicates dropped."""
    words: list[str] = []
    for part in re.split(r"[\n;,]+", text):
        word = " ".join(part.split()).strip(" .!?\"'“”‘’")
        if word and word.lower() not in (w.lower() for w in words):
            words.append(word[:200])
    return words


def _word_pattern(word: str) -> re.Pattern:
    """Match the word and simple inflections (settle → settles/settled/settling)."""
    parts = [re.escape(p) for p in word.split()]
    last = parts[-1]
    stem = re.escape(word.split()[-1][:-1]) if word.lower().endswith("e") else last
    parts[-1] = f"(?:{last}(?:s|es|d|ed|'s)?|{stem}ing|{last}ing)"
    return re.compile(r"(?<![\w'])" + r"\s+".join(parts) + r"(?![\w'])", re.I)


def find_context(transcript: Path | None, word: str) -> tuple[str, str]:
    """(sentence, time) for the most recent caption sentence containing ``word``, or ("", "").

    Caption lines often hold several sentences; only the one with the word is returned.
    """
    if transcript is None:
        return "", ""
    try:
        lines = transcript.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "", ""
    pattern = _word_pattern(word)
    for line in reversed(lines):
        m = _CAPTION_LINE.match(line)
        if not m or not pattern.search(m.group(2)):
            continue
        sentences = re.split(r"(?<=[.!?])\s+", m.group(2).strip())
        sentence = next((s for s in reversed(sentences) if pattern.search(s)), m.group(2))
        return sentence.strip()[:2000], m.group(1)
    return "", ""


def build_prompt(words: list[NewWord]) -> str:
    try:
        template = VOCAB_PROMPT_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise VocabError(f"Could not read the prompt file {VOCAB_PROMPT_PATH}:\n{exc}") from exc
    listing = "\n".join(
        f'{i}. "{w.word}"' + (f' — câu: "{w.sentence}"' if w.sentence else "") for i, w in enumerate(words, 1)
    )
    return template.replace("{words}", listing).strip()


def parse_answer(answer: str, words: list[NewWord]) -> None:
    """Fill ``words`` in place from ChatGPT's JSON answer."""
    candidates = [m.group(1) for m in _FENCE.finditer(answer)] + [answer]
    data = None
    for text in candidates:
        start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
        if start < 0:
            continue
        try:
            data, _ = json.JSONDecoder().raw_decode(text[start:])
            break
        except ValueError:
            continue
    if isinstance(data, dict):
        data = data.get("items") or data.get("words") or [data]
    if not isinstance(data, list) or not data:
        raise VocabError("ChatGPT's answer did not contain the expected JSON list.")

    by_word = {str(d.get("word", "")).strip().lower(): d for d in data if isinstance(d, dict)}
    for i, w in enumerate(words):
        d = by_word.get(w.word.lower()) or (data[i] if i < len(data) and isinstance(data[i], dict) else {})
        w.word = str(d.get("word") or w.word).strip()[:200]
        w.translation = str(d.get("translation") or "").strip()[:1000]
        w.meaning = str(d.get("meaning") or "").strip()[:2000]
        w.part_of_speech = str(d.get("part_of_speech") or "").strip()[:30]
        w.phonetic = str(d.get("phonetic") or "").strip()[:200]
        w.context_translation = str(d.get("context_translation") or "").strip()[:2000]


def voca_items(words: list[NewWord], source_title: str) -> list[dict]:
    """Voca ``WordInput`` objects (see voca/docs/external-api/reference.md)."""
    items = []
    for w in words:
        item: dict = {"word": w.word}
        for key in ("translation", "meaning", "part_of_speech", "phonetic"):
            if getattr(w, key):
                item[key] = getattr(w, key)
        if w.sentence:
            context = {"sentence": w.sentence, "source_title": source_title[:255], "source_type": "caption"}
            if w.context_translation:
                context["translation"] = w.context_translation
            if w.time:
                context["location"] = w.time
            item["context"] = context
        items.append(item)
    return items


def card_html(w: NewWord) -> str:
    """One word as an HTML card for the New words tab."""
    e = html.escape
    head = f"<b class='vw'>{e(w.word)}</b>"
    if w.phonetic:
        head += f" <span class='vphon'>{e(w.phonetic)}</span>"
    if w.part_of_speech:
        head += f" <i class='vpos'>{e(w.part_of_speech)}</i>"
    parts = [f"<div>{head}</div>"]
    if w.translation:
        parts.append(f"<div class='vtr'>{e(w.translation)}</div>")
    if w.meaning:
        parts.append(f"<div class='vmean'>{e(w.meaning)}</div>")
    if w.sentence:
        quote = f"{e(w.sentence)}" + (f"<br><i>{e(w.context_translation)}</i>" if w.context_translation else "")
        stamp = f"<span class='vtime'>[{e(w.time)}]</span> " if w.time else ""
        parts.append(f"<blockquote>{stamp}{quote}</blockquote>")
    status = {
        "created": ("ok", "Saved to Voca — new word"),
        "updated": ("ok", "Saved to Voca — added new meaning/context"),
        "unchanged": ("same", "Already in Voca — nothing new to add"),
        "failed": ("bad", f"Not saved: {w.voca_error}"),
    }.get(w.voca_status, ("same", w.voca_error or "Not sent to Voca"))
    parts.append(f"<div class='vstat vstat-{status[0]}'>{e(status[1])}</div>")
    return f"<div class='vcard'>{''.join(parts)}</div>"

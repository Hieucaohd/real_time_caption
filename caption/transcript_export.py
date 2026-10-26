"""Select a transcript excerpt without modifying the recorded files."""

from __future__ import annotations


def last_lines(text: str, count: int) -> str:
    if count < 0:
        raise ValueError("Line count cannot be negative.")
    text = text.strip()
    if count == 0:
        return text
    lines = text.splitlines()
    captions = [index for index, line in enumerate(lines) if line.startswith("[")]
    if not captions:
        return "\n".join(lines[-count:])
    if count >= len(captions):
        return text
    # Keep continuation lines of the selected captions, including multiline ASR
    # text. Metadata is not counted as speech; keep the source/start headers.
    headers = [line for line in lines[:captions[0]] if line.startswith("#")][:2]
    return "\n".join(headers + [f"# Excerpt: the last {count} of {len(captions)} caption lines", ""]
                     + lines[captions[-count]:]).strip()

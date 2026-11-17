"""Extract additions to a growing translation without rewriting displayed text."""
import re
from difflib import SequenceMatcher


def added_tail(previous, current):
    if not previous:
        return current
    if current.startswith(previous):
        return current[len(previous):]
    # Google may rephrase earlier words when more context arrives. Locate the
    # end of the already-read text in the new output; ignore edits before it.
    old = list(re.finditer(r'\w+|[^\w\s]', previous))
    new = list(re.finditer(r'\w+|[^\w\s]', current))
    matcher = SequenceMatcher(None, [m.group().casefold() for m in old],
                              [m.group().casefold() for m in new], autojunk=False)
    for block in reversed(matcher.get_matching_blocks()):
        if block.size and block.a + block.size == len(old):
            return current[new[block.b + block.size - 1].end():]
    # A rewrite with no recognizable old ending has no reliable added suffix.
    # Keep displayed text, and use the new raw output as the next baseline.
    return ''

"""Append-only live source for one Start/Continue, independent of old transcript."""
import re
import uuid
from difflib import SequenceMatcher

from .append_translation import added_tail


class GoogleCapture:
    def __init__(self, conversation_id, anchor=''):
        self.conversation_id, self.anchor = conversation_id, anchor
        self.key = uuid.uuid4().hex
        self.committed = self.partial = self.observed = self.text = ''

    def final(self, text):
        self.committed = ' '.join((self.committed + ' ' + text).split())
        self.partial = ''  # The reader sends the remaining partial immediately after finals.

    def snapshot(self):
        current = ' '.join((self.committed + ' ' + self.partial).split())
        if not current or self.observed.startswith(current):
            return self.text  # Final/partial handoff can briefly shorten the preview.
        tail = added_tail(self.observed, current)
        if not tail and self.observed:
            # If recognition revises the last word AND adds new words, retain
            # previously sent words and extract only the extra tail words.
            old = list(re.finditer(r'\S+', self.observed))
            new = list(re.finditer(r'\S+', current))
            changes = SequenceMatcher(None, [m.group() for m in old], [m.group() for m in new], autojunk=False).get_opcodes()
            kind, a, b, c, d = changes[-1]
            if kind == 'replace' and b == len(old) and d - c > b - a:
                tail = ' ' + current[new[c + b - a].start():]
        self.observed = current
        self.text += tail
        return self.text

"""Read a growing, revisable composer without appending its whole text repeatedly."""
import re
from difflib import SequenceMatcher


def normalized(text):
    return re.sub(r'\s+', ' ', text or '').strip()


class DictationText:
    def __init__(self, baseline='', *, stable_seconds=.8, idle_seconds=1.5):
        self.text = normalized(baseline)
        self.offset = len(self.text)
        self.changed_at = 0.0
        self.candidate = ''
        self.candidate_since = 0.0
        self.stable_seconds, self.idle_seconds = stable_seconds, idle_seconds

    def _move_boundary(self, text):
        if not self.offset or text.startswith(self.text[:self.offset]):
            return self.offset
        matcher = SequenceMatcher(None, self.text, text, autojunk=False)
        changes = matcher.get_opcodes()
        if sum(block.size for block in matcher.get_matching_blocks()) < len(self.text) * .5:
            return 0
        for kind, start, end, new_start, new_end in changes:
            if self.offset <= end:
                if kind == 'equal':
                    return new_start + self.offset - start
                return new_end if self.offset == end else new_start
        return len(text)

    def update(self, text, now, *, flush=False):
        text = normalized(text)
        # Ignore transient empty snapshots while the editor is hydrating.
        if text and text != self.text:
            self.offset = self._move_boundary(text)
            self.text, self.changed_at = text, now
        tail = self.text[self.offset:]
        pending = tail.lstrip()
        candidate = ''
        if flush or now - self.changed_at >= self.idle_seconds:
            candidate = pending
        elif self.candidate and pending.startswith(self.candidate):
            candidate = self.candidate
        else:
            endings = list(re.finditer(r'[。！？]|[.!?](?=\s|$)', pending))
            if endings:
                candidate = pending[:endings[-1].end()]
            elif len(pending) > 180:
                # Long unpunctuated speech reaches the DB; keep the newest words
                # revisable in the preview instead of stopping the microphone.
                cut = pending.rfind(' ', 0, 160)
                if cut > 0:
                    candidate = pending[:cut]
        if candidate != self.candidate:
            self.candidate, self.candidate_since = candidate, now
        ready = flush or now - self.changed_at >= self.idle_seconds or now - self.candidate_since >= self.stable_seconds
        if candidate and ready:
            self.offset += len(tail) - len(pending) + len(candidate)
            self.candidate = ''
            return [candidate], self.text[self.offset:].lstrip()
        return [], pending

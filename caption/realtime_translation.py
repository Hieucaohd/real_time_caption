"""Keep live translation passages independent of finalized translation jobs."""
import uuid


class RealtimePassage:
    # Bound work during continuous speech without translating each added word
    # in isolation. Only consume a prefix after captions confirm its source.
    LIMIT = 600

    def __init__(self):
        self.key = uuid.uuid4().hex
        self.committed = ''
        self.rendered_source = ''

    @staticmethod
    def normalize(text):
        return ' '.join(text.split())

    def final(self, text):
        self.committed = self.normalize(self.committed + ' ' + text)

    def source(self, partial):
        text = self.normalize(self.committed + ' ' + partial)
        end = text.find(' ', self.LIMIT)
        return text[:end] if end >= 0 else text

    def advance(self, partial):
        source = self.rendered_source
        confirmed = source and (self.committed == source or self.committed.startswith(source + ' '))
        if not confirmed or (partial.strip() and len(source) < self.LIMIT):
            return False
        self.committed = self.committed[len(source):].strip()
        self.rendered_source = ''
        self.key = uuid.uuid4().hex
        return True

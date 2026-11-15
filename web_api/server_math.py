"""Shared Markdown normalization for browser readers."""
import re

def browser_markdown(text):
    from caption.markdown_html import _prepare_math, _FENCE, _INLINE_CODE
    # Match native handling of ChatGPT Copy's escaped delimiters, protecting code.
    prepared = _prepare_math(text)
    def inline(part):
        out, pos = [], 0
        for code in _INLINE_CODE.finditer(part):
            out.append(re.sub(r'(?<!\\)\\\((.+?)\\\)', lambda m: '$' + m[1] + '$', part[pos:code.start()], flags=re.S))
            out.append(code[0]); pos = code.end()
        out.append(re.sub(r'(?<!\\)\\\((.+?)\\\)', lambda m: '$' + m[1] + '$', part[pos:], flags=re.S))
        return ''.join(out)
    out, pos = [], 0
    for fence in _FENCE.finditer(prepared):
        out.append(inline(prepared[pos:fence.start()])); out.append(fence[0]); pos = fence.end()
    return ''.join(out) + inline(prepared[pos:])

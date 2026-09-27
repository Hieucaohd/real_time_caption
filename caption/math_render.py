"""Render LaTeX formulas from ChatGPT answers as images.

chatgpt.com typesets math with KaTeX (a JavaScript library). The HTML engine inside
the app (Tkhtml via tkinterweb) cannot run KaTeX or lay out its CSS, so formulas are
drawn with matplotlib's mathtext (a pure-Python TeX renderer with Computer Modern
symbols) and embedded as PNG images. mathtext covers a subset of LaTeX, so common
constructs it lacks are rewritten first (``\\boxed``, ``\\dfrac``, ``\\underbrace``,
multi-line ``\\\\`` / ``aligned``); anything still unsupported is shown as its TeX
source instead of breaking the answer.
"""

from __future__ import annotations

import base64
import functools
import html
import io
import logging
import re

import matplotlib

matplotlib.use("Agg")  # no GUI backend needed; we only rasterise to PNG

from matplotlib import mathtext, rc_context  # noqa: E402
from matplotlib.font_manager import FontProperties  # noqa: E402

log = logging.getLogger(__name__)
logging.getLogger("matplotlib").setLevel(logging.ERROR)  # silence glyph-substitution chatter

# Times New Roman (ships with Windows) looks like TeX and covers Vietnamese for \text{...};
# math symbols fall back to Computer Modern, which KaTeX's fonts are modelled on. (Cambria
# was tried first but, being a .ttc collection, drops digits/superscripts in mathtext.)
RC = {
    "mathtext.fontset": "custom",
    "mathtext.rm": "Times New Roman",
    "mathtext.it": "Times New Roman:italic",
    "mathtext.bf": "Times New Roman:bold",
    "mathtext.sf": "Segoe UI",
    "mathtext.tt": "Consolas",
    "mathtext.fallback": "cm",
}
TEXT_COLOR = "#202020"
INLINE_PT = 11.5
DISPLAY_PT = 13
dpi = 96.0  # set from the real screen DPI at startup (see set_dpi)

_SIMPLE = {
    "dfrac": r"\frac",
    "tfrac": r"\frac",
    "cfrac": r"\frac",
    "operatorname": r"\mathrm",
    "textbf": r"\mathbf",
    "textit": r"\mathit",
    "textrm": r"\text",
    "mbox": r"\text",
    "displaystyle": "",
    "textstyle": "",
    "limits": "",
    "nolimits": "",
    "notag": "",
    "nonumber": "",
    "left.": "",
    "right.": "",
}
_ENV = re.compile(r"\\(begin|end)\{(aligned|align\*?|gathered|gather\*?|split|cases|eqnarray\*?|multline\*?)\}")


def set_dpi(value: float) -> None:
    global dpi
    if abs(value - dpi) > 0.5:
        dpi = value
        _render_png.cache_clear()


# ----------------------------------------------------------------- TeX rewriting


def _group(s: str, start: int) -> tuple[str, int] | None:
    """Balanced ``{...}`` group starting at ``start``; returns (content, index after it)."""
    if start >= len(s) or s[start] != "{":
        return None
    depth = 0
    for i in range(start, len(s)):
        if s[i] == "\\":
            continue
        if s[i] == "{" and (i == 0 or s[i - 1] != "\\"):
            depth += 1
        elif s[i] == "}" and s[i - 1] != "\\":
            depth -= 1
            if depth == 0:
                return s[start + 1 : i], i + 1
    return None


def _rewrite_command(s: str, name: str, fn) -> str:
    r"""Replace every ``\name{arg}`` (plus an optional ``_{..}``/``^{..}``) with fn(arg, sub, sup)."""
    pattern = re.compile(r"\\" + name + r"(?![A-Za-z])\s*")
    out, pos = [], 0
    while True:
        m = pattern.search(s, pos)
        if not m:
            out.append(s[pos:])
            return "".join(out)
        arg = _group(s, m.end())
        if arg is None:
            out.append(s[pos : m.end()])
            pos = m.end()
            continue
        content, end = arg
        sub = sup = None
        rest = s[end:].lstrip()
        if rest[:1] in "_^":
            script = _group(rest, 1)
            if script:
                if rest[0] == "_":
                    sub = script[0]
                else:
                    sup = script[0]
                end = len(s) - len(rest) + script[1]
        out.append(s[pos : m.start()])
        out.append(fn(content, sub, sup))
        pos = end


_TOKEN = re.compile(r"\s*(\{|\\[A-Za-z]+|[^\s{}\\])")


def _brace_args(s: str, name: str, count: int) -> str:
    r"""TeX lets single-token arguments skip braces (``\frac12``, ``\sqrt5``); mathtext doesn't."""
    pattern = re.compile(r"\\" + name + r"(?![A-Za-z])")
    out, pos = [], 0
    for m in pattern.finditer(s):
        if m.start() < pos:
            continue
        out.append(s[pos : m.end()])
        i = m.end()
        optional = re.match(r"\s*\[[^\]]*\]", s[i:])  # \sqrt[n]{x}
        if optional:
            out.append(optional.group(0))
            i += optional.end()
        for _ in range(count):
            t = _TOKEN.match(s, i)
            if not t:
                break
            if t.group(1) == "{":
                g = _group(s, t.start(1))
                if not g:
                    break
                out.append(s[i : g[1]])
                i = g[1]
            else:
                out.append("{" + t.group(1) + "}")
                i = t.end()
        pos = i
    out.append(s[pos:])
    return "".join(out)


def _normalise(tex: str) -> tuple[list[str], bool]:
    """Rewrite for mathtext; returns (rows to render, draw a box around it)."""
    tex = tex.strip()
    boxed = False
    whole = re.fullmatch(r"\\boxed\s*(\{.*\})", tex, re.S)
    if whole and _group(whole.group(1), 0) and _group(whole.group(1), 0)[1] == len(whole.group(1)):
        boxed, tex = True, _group(whole.group(1), 0)[0]
    tex = _rewrite_command(tex, "boxed", lambda c, sub, sup: "{" + c + "}")
    tex = _rewrite_command(
        tex, "underbrace", lambda c, sub, sup: r"\underset{" + (sub or "") + "}{" + c + "}" if sub else "{" + c + "}"
    )
    tex = _rewrite_command(
        tex, "overbrace", lambda c, sub, sup: r"\overset{" + (sup or "") + "}{" + c + "}" if sup else "{" + c + "}"
    )
    tex = _rewrite_command(tex, "xrightarrow", lambda c, sub, sup: r"\overset{" + c + r"}{\longrightarrow}")
    tex = _rewrite_command(tex, "xleftarrow", lambda c, sub, sup: r"\overset{" + c + r"}{\longleftarrow}")
    for name, repl in _SIMPLE.items():
        tex = re.sub(r"\\" + re.escape(name) + (r"(?![A-Za-z])" if name[-1].isalpha() else ""), lambda _m: repl, tex)
    tex = _brace_args(tex, "frac", 2)
    tex = _brace_args(tex, "sqrt", 1)
    tex = _ENV.sub("", tex)
    rows = [r.replace("&", " ").replace("\n", " ").strip() for r in re.split(r"\\\\(?:\[[^\]]*\])?", tex)]
    return [r for r in rows if r], boxed


# -------------------------------------------------------------------- rendering


@functools.lru_cache(maxsize=2048)
def _render_png(tex: str, size: float, dpi_value: float) -> tuple[str, int, int, int] | None:
    """(base64 PNG, width, height, depth) or None if mathtext can't handle it."""
    buf = io.BytesIO()
    try:
        with rc_context(RC):
            depth = mathtext.math_to_image(
                f"${tex}$", buf, prop=FontProperties(size=size), dpi=dpi_value, format="png", color=TEXT_COLOR
            )
    except Exception:  # noqa: BLE001 - unsupported TeX; caller falls back to source
        return None
    data = buf.getvalue()
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return base64.b64encode(data).decode("ascii"), width, height, int(round(depth or 0))


def _img(tex: str, size: float, css_class: str) -> str | None:
    rendered = _render_png(tex, size, dpi)
    if rendered is None:
        return None
    b64, width, height, depth = rendered
    alt = html.escape(tex, quote=True)
    return (
        f'<img class="{css_class}" src="data:image/png;base64,{b64}" width="{width}" height="{height}" '
        f'style="vertical-align: -{depth}px" alt="{alt}">'
    )


def _source(tex: str, display: bool) -> str:
    code = f"<code class='tex'>{html.escape(tex.strip())}</code>"
    return f"<div class='math-display'>{code}</div>" if display else code


def inline_html(tex: str) -> str:
    rows, boxed = _normalise(tex)
    imgs = [_img(r, INLINE_PT, "math-inline" + (" math-boxed" if boxed else "")) for r in rows]
    if not rows or None in imgs:
        return _source(tex, display=False)
    return " ".join(imgs)


def display_html(tex: str) -> str:
    rows, boxed = _normalise(tex)
    imgs = [_img(r, DISPLAY_PT, "math-block") for r in rows]
    if not rows or None in imgs:
        return _source(tex, display=True)
    inner = "".join(f"<div class='math-row'>{img}</div>" for img in imgs)
    if boxed:
        inner = f"<div class='math-boxed'>{inner}</div>"
    return f"<div class='math-display'>{inner}</div>"

"""Render ChatGPT's markdown answers with a real HTML engine.

markdown-it-py (CommonMark + GFM tables, the dialect ChatGPT writes) turns markdown
into HTML, and tkinterweb's ``HtmlFrame`` (Tkhtml) displays it inside Tkinter, so
tables, code blocks, quotes and nested lists render properly. LaTeX formulas are
rendered by MathJax and embedded as images. Raw HTML in answers is escaped, and links
open in the default browser instead of inside the app.
"""

from __future__ import annotations

import html
import re
import webbrowser
from typing import Callable

import tkinter as tk
from markdown_it import MarkdownIt
from tkinterweb import HtmlFrame

from mdit_py_plugins.dollarmath import dollarmath_plugin
from mdit_py_plugins.texmath import texmath_plugin

from . import mathjax_render

# Math is pulled out *before* markdown parsing (like remark-math on chatgpt.com), so
# backslashes and underscores in formulas aren't eaten as escapes/emphasis. ChatGPT writes
# \( inline \) and \[ display \]; $...$ / $$...$$ are accepted too.
_md = (
    MarkdownIt("commonmark", {"html": False})
    .enable(["table", "strikethrough"])
    .use(texmath_plugin, delimiters="brackets")
    .use(dollarmath_plugin, allow_digits=False, double_inline=True)
)
def _render_formula(tokens, idx, env, display=False):
    render = mathjax_render.display_html if display else mathjax_render.inline_html
    dpi = env.get("math_dpi")
    return render(tokens[idx].content) if dpi is None else render(tokens[idx].content, dpi_value=dpi)


_md.add_render_rule("math_inline", lambda self, tokens, idx, options, env: _render_formula(tokens, idx, env))
for _rule in ("math_inline_double", "math_block", "math_block_eqno", "math_block_label"):
    _md.add_render_rule(_rule, lambda self, tokens, idx, options, env: _render_formula(tokens, idx, env, display=True))

CSS = """
body { font-family: 'Segoe UI'; font-size: 11pt; color: #202020; background: #ffffff; margin: 6px 10px; }
h1 { font-size: 17pt; margin: 10px 0 6px 0; }
h2 { font-size: 15pt; margin: 10px 0 5px 0; }
h3 { font-size: 13pt; margin: 8px 0 4px 0; }
h4, h5, h6 { font-size: 11pt; margin: 6px 0 3px 0; }
p { margin: 4px 0; }
ul, ol { margin-top: 2px; margin-bottom: 4px; }
li { margin: 1px 0; }
hr { border: 0; border-top: 1px solid #d0d0d0; margin: 10px 0; }
table { border-collapse: collapse; margin: 6px 0; }
th, td { border: 1px solid #cfcfcf; padding: 4px 8px; vertical-align: top; }
th { background: #f3f3f3; }
code { font-family: Consolas; font-size: 10pt; background: #ececec; }
pre { font-family: Consolas; font-size: 10pt; background: #f6f8fa; padding: 8px; }
pre code { background: transparent; }
blockquote { color: #555555; border-left: 3px solid #cccccc; margin: 6px 0; padding-left: 10px; }
a { color: #1a5fb4; }
.meta { color: #888888; font-size: 9pt; margin: 10px 0 2px 0; }
.meta-right { text-align: right; }
.user { background: #d9eaff; margin: 0 8px 4px 22%; padding: 6px 10px; }
.bot { background: #f1f1f1; margin: 0 22% 4px 0; padding: 4px 10px; }
.pending { color: #888888; font-style: italic; margin: 8px 0; }
.math-display { text-align: center; margin: 8px 0; }
.math-row { margin: 3px 0; }
.math-boxed { border: 1px solid #8a8a8a; padding: 4px 10px; display: inline-block; }
img.math-boxed { padding: 2px 4px; }
.math-array-wrap { overflow-x: auto; }
table.math-array { border-collapse: collapse; margin: 4px auto; }
table.math-array td.math-array-cell { border: 0; padding: 3px 10px; vertical-align: middle; }
table.math-array td.math-array-vbar { border-left: 1px solid #505050; }
table.math-array td.math-array-right { border-right: 1px solid #505050; }
table.math-array tr.math-array-rule td { border-top: 1px solid #505050; }
table.math-array tr.math-array-bottom-rule td { border-bottom: 1px solid #505050; }
code.tex { color: #7a3e00; background: #fff6e5; }
.vcard { margin: 2px 0 8px 0; }
.vw { font-size: 13pt; }
.vphon { color: #555555; font-family: 'Segoe UI'; }
.vpos { color: #8a5a00; }
.vtr { font-size: 12pt; margin: 2px 0; }
.vmean { color: #444444; }
.vtime { color: #888888; }
.vstat { font-size: 9pt; margin-top: 4px; }
.vstat-ok { color: #1e7b34; }
.vstat-same { color: #777777; }
.vstat-bad { color: #b3261e; }
.shot { margin-top: 6px; }
.shot img { border: 1px solid #b8c8e0; }
.message-actions { font-size: 9pt; margin-top: 6px; text-align: right; }
.message-actions a { margin-left: 10px; text-decoration: none; }
.message-actions .disabled { color: #999999; margin-left: 10px; }
"""


_FENCE = re.compile(r"(^|\n)(```|~~~).*?(\n\2[^\n]*(?=\n|$)|$)", re.S)
_INLINE_CODE = re.compile(r"(?<!`)(`+)(?!`).*?(?<!`)\1(?!`)", re.S)
_BRACKET_MATH = re.compile(
    r"(?<!\\)(?P<display_slashes>\\{1,2})\[(?P<display>.+?)(?P=display_slashes)\]"
    r"|(?<!\\)(?P<inline_slashes>\\{1,2})\((?P<inline>.+?)(?P=inline_slashes)\)",
    re.S,
)
_COPIED_TEX_TOKEN = re.compile(
    r"\\(?:text[a-z]*|mbox|operatorname\*?)\s*\{|\\[{}]|[{}]|\\_"
)


def _unescape_copied_tex(tex: str) -> str:
    r"""Undo copied Markdown's ``\_`` inside math, not literal text groups.

    Only called for the double-escaped delimiters produced by ChatGPT's Copy
    action. TeX commands, escaped symbols and ``\\`` array rows stay intact.
    """
    text_groups: list[bool] = []

    def replace(match: re.Match) -> str:
        token = match.group(0)
        if token.startswith("\\") and token.endswith("{") and token != r"\{":
            text_groups.append(True)
        elif token == "{":
            text_groups.append(bool(text_groups and text_groups[-1]))
        elif token == "}":
            if text_groups:
                text_groups.pop()
        elif token == r"\_" and not (text_groups and text_groups[-1]):
            return "_"
        return token

    return _COPIED_TEX_TOKEN.sub(replace, tex)


def _prepare_math(markdown: str) -> str:
    r"""Normalise bracket math, including double-escaped ChatGPT Copy output.

    ChatGPT often puts a display formula right under a list item or sentence without a
    blank line; markdown then treats it as paragraph text and ``\[`` as an escaped ``[``.
    ``$$...$$`` is recognised anywhere (block or inline). Fenced and inline code
    are left untouched. Never globally unescape backslashes: array rows use them.
    """
    def formula(match: re.Match) -> str:
        display = match.group("display") is not None
        tex = match.group("display" if display else "inline")
        slashes = match.group("display_slashes" if display else "inline_slashes")
        if len(slashes) == 2:
            tex = _unescape_copied_tex(tex)
        if display:
            return "$$" + " ".join(tex.split()) + "$$"
        return r"\(" + tex + r"\)"

    def convert(text: str) -> str:
        out, pos = [], 0
        for code in _INLINE_CODE.finditer(text):
            out.append(_BRACKET_MATH.sub(formula, text[pos : code.start()]))
            out.append(code.group(0))
            pos = code.end()
        out.append(_BRACKET_MATH.sub(formula, text[pos:]))
        return "".join(out)

    out, pos = [], 0
    for fence in _FENCE.finditer(markdown):
        out.append(convert(markdown[pos : fence.start()]))
        out.append(fence.group(0))
        pos = fence.end()
    out.append(convert(markdown[pos:]))
    return "".join(out)


def to_html(markdown: str, *, math_dpi: float | None = None) -> str:
    return _md.render(_prepare_math(markdown), env={"math_dpi": math_dpi})


def plain_to_html(text: str) -> str:
    """User-typed text: escape it and keep its line breaks."""
    return html.escape(text.strip()).replace("\n", "<br>")


def page(body: str) -> str:
    return f"<html><head><style>{CSS}</style></head><body>{body}</body></html>"


def make_view(master: tk.Misc, on_link_click: Callable[[str], None] | None = None) -> HtmlFrame:
    # Formula images are rasterised at the real screen DPI so they match the text size.
    mathjax_render.set_dpi(master.winfo_fpixels("1i"))
    view = HtmlFrame(
        master,
        messages_enabled=False,
        on_link_click=on_link_click or (lambda url: webbrowser.open(url)),
    )
    view.load_html(page(""))
    return view


def show(view: HtmlFrame, body: str, scroll_to_end: bool = False) -> None:
    if scroll_to_end:
        # Tkhtml can perform several layout passes (especially when formulas or
        # screenshots are present).  The fragment handles the initial load and the
        # repeated moves keep the view pinned after later geometry changes.
        anchor = "rtc-chat-end"
        view.load_html(page(body + f"<div id='{anchor}'></div>"), fragment=anchor)
        scroll_to(view, 1.0)
    else:
        view.load_html(page(body))


def scroll_to(view: HtmlFrame, fraction: float) -> None:
    """Reliably scroll an HTML view after all of Tkhtml's layout passes."""
    generation = getattr(view, "_rtc_scroll_generation", 0) + 1
    view._rtc_scroll_generation = generation

    def move() -> None:
        if getattr(view, "_rtc_scroll_generation", 0) != generation:
            return
        try:
            view.yview_moveto(fraction)
        except tk.TclError:
            pass  # the app may have closed while a delayed move was pending

    for delay in (0, 40, 120, 300, 700):
        view.after(delay, move)

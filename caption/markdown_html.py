"""Render ChatGPT's markdown answers with a real HTML engine.

markdown-it-py (CommonMark + GFM tables, the dialect ChatGPT writes) turns markdown
into HTML, and tkinterweb's ``HtmlFrame`` (Tkhtml) displays it inside Tkinter, so
tables, code blocks, quotes and nested lists render properly. Raw HTML in answers is
escaped, and links open in the default browser instead of inside the app.
"""

from __future__ import annotations

import html
import webbrowser

import tkinter as tk
from markdown_it import MarkdownIt
from tkinterweb import HtmlFrame

_md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])

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
"""


def to_html(markdown: str) -> str:
    return _md.render(markdown)


def plain_to_html(text: str) -> str:
    """User-typed text: escape it and keep its line breaks."""
    return html.escape(text.strip()).replace("\n", "<br>")


def page(body: str) -> str:
    return f"<html><head><style>{CSS}</style></head><body>{body}</body></html>"


def make_view(master: tk.Misc) -> HtmlFrame:
    view = HtmlFrame(master, messages_enabled=False, on_link_click=lambda url: webbrowser.open(url))
    view.load_html(page(""))
    return view


def show(view: HtmlFrame, body: str, scroll_to_end: bool = False) -> None:
    view.load_html(page(body))
    if scroll_to_end:
        # Layout happens after idle; scroll once it has.
        view.after(80, lambda: view.yview_moveto(1.0))

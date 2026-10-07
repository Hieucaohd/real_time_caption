"""Render TeX with MathJax and rasterise its SVG for TkinterWeb.

TkinterWeb uses Tkhtml rather than Chromium.  Its JavaScript DOM bridge is not
complete enough to run MathJax in the page, so MathJax runs against its supported
``liteAdaptor`` DOM via PythonMonkey.  The resulting SVG is rasterised by resvg and
embedded as a PNG.  This keeps MathJax's broad TeX support while remaining fully
offline and compatible with the existing HTML widget.
"""

from __future__ import annotations

import base64
import functools
import html
import logging
import re
import threading
from pathlib import Path

from . import math_render
from .paths import BUNDLE_DIR

log = logging.getLogger(__name__)

TEXT_COLOR = "#202020"
INLINE_PT = 11.5
DISPLAY_PT = 13.0

_SVG = re.compile(r"<svg\b[\s\S]*</svg>")
_VERTICAL_ALIGN = re.compile(r"vertical-align:\s*([+-]?[0-9.]+)ex")
_lock = threading.RLock()
_render_function = None
_load_error: Exception | None = None


def set_dpi(value: float) -> None:
    """Use the real screen DPI and invalidate size-dependent cached PNGs."""
    math_render.set_dpi(value)
    _render_png.cache_clear()


def _bridge_path() -> Path:
    return BUNDLE_DIR / "assets" / "mathjax-runtime" / "mathjax-bridge.cjs"


def _load_bridge():
    global _render_function, _load_error
    if _render_function is not None:
        return _render_function
    if _load_error is not None:
        raise _load_error

    with _lock:
        if _render_function is not None:
            return _render_function
        try:
            import pythonmonkey as pm

            bridge = _bridge_path()
            require = pm.createRequire(str(bridge.with_name("python-host.js")))
            exports = require("./mathjax-bridge.cjs")
            render = exports["render"]
            if not callable(render):
                raise RuntimeError("MathJax bridge did not export render()")
            _render_function = render
        except Exception as error:  # noqa: BLE001 - fallback renderer handles it
            _load_error = error
            log.exception("MathJax could not be loaded; using the legacy math renderer")
            raise
    return _render_function


@functools.lru_cache(maxsize=2048)
def _render_png(tex: str, display: bool, size_pt: float, dpi_value: float) -> tuple[str, int, int, float] | None:
    """Return ``(base64 PNG, width, height, vertical-align px)``."""
    try:
        import resvg_py

        with _lock:
            rendered = str(_load_bridge()(tex, display, 80 * 16))
        match = _SVG.search(rendered)
        if match is None:
            raise RuntimeError("MathJax returned no SVG")

        svg = match.group(0).replace("currentColor", TEXT_COLOR)
        em_px = size_pt * dpi_value / 72.0
        png = resvg_py.svg_to_bytes(
            svg_string=svg,
            font_size=em_px,
            dpi=dpi_value,
            skip_system_fonts=True,
        )
        width = int.from_bytes(png[16:20], "big")
        height = int.from_bytes(png[20:24], "big")
        align_match = _VERTICAL_ALIGN.search(svg)
        # MathJax sizes its x-height at 0.5em.  Preserve its inline baseline.
        align_px = 0.0 if display else (float(align_match.group(1)) * em_px / 2 if align_match else 0.0)
        return base64.b64encode(png).decode("ascii"), width, height, align_px
    except Exception:  # noqa: BLE001 - malformed/unsupported TeX uses the safe fallback
        log.debug("MathJax failed to render %r", tex, exc_info=True)
        return None


def _image(tex: str, display: bool, size_pt: float) -> str | None:
    rendered = _render_png(tex.strip(), display, size_pt, math_render.dpi)
    if rendered is None:
        return None
    data, width, height, align_px = rendered
    css_class = "math-block" if display else "math-inline"
    alt = html.escape(tex.strip(), quote=True)
    return (
        f'<img class="{css_class}" src="data:image/png;base64,{data}" '
        f'width="{width}" height="{height}" style="vertical-align:{align_px:.2f}px" alt="{alt}">'
    )


def inline_html(tex: str) -> str:
    image = _image(tex, display=False, size_pt=INLINE_PT)
    return image if image is not None else math_render.inline_html(tex)


def display_html(tex: str) -> str:
    image = _image(tex, display=True, size_pt=DISPLAY_PT)
    if image is None:
        return math_render.display_html(tex)
    return f"<div class='math-display'><div class='math-row'>{image}</div></div>"


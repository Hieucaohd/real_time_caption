from __future__ import annotations

import unittest
from unittest.mock import patch

from caption import markdown_html, math_render, mathjax_render


ARRAY = r"""\begin{array}{c|c}
f(x) & \displaystyle \int_0^b f(x)\,dx\\
\hline
x^2 & \dfrac{b^3}{3}\\[4pt]
x=x^1 & \dfrac{b^2}{2}\\[4pt]
1=x^0 & b=\dfrac{b^1}{1}
\end{array}"""


class MathArrayRenderTests(unittest.TestCase):
    def test_array_renders_as_table_instead_of_tex_fallback(self) -> None:
        result = math_render.display_html(ARRAY)
        self.assertIn('class="math-array"', result)
        self.assertIn("math-array-vbar", result)
        self.assertIn("math-array-rule", result)
        self.assertGreaterEqual(result.count("data:image/png;base64,"), 8)
        self.assertNotIn("<code class='tex'>", result)

    def test_array_survives_markdown_math_parsing(self) -> None:
        result = markdown_html.to_html("Trên bảng:\n\\[" + ARRAY + "\\]")
        self.assertIn('class="math-block"', result)
        self.assertIn("data:image/png;base64,", result)
        self.assertNotIn("<code class='tex'>", result)

    def test_mathjax_handles_array_and_nested_fraction(self) -> None:
        for tex in (
            ARRAY,
            r"\frac{1^2+\cdots+n^2}{n^3} \approx \frac{\frac13n^3}{n^3} = \frac13,",
            r"\begin{aligned}x&=1\\y&=2\end{aligned}",
        ):
            result = mathjax_render.display_html(tex)
            self.assertIn("data:image/png;base64,", result)
            self.assertNotIn("<code class='tex'>", result)

    def test_nested_fraction_with_shorthand_arguments(self) -> None:
        tex = r"\frac{1^2+\cdots+n^2}{n^3} \approx \frac{\frac13n^3}{n^3} = \frac13,"
        rows, _boxed = math_render._normalise(tex)
        self.assertIn(r"\frac{\frac{1}{3}n^3}{n^3}", rows[0])
        result = math_render.display_html(tex)
        self.assertIn("data:image/png;base64,", result)
        self.assertNotIn("<code class='tex'>", result)


class CopiedMathTests(unittest.TestCase):
    def render(self, markdown):
        with patch.object(mathjax_render, "inline_html", return_value="<INLINE/>") as inline, \
                patch.object(mathjax_render, "display_html", return_value="<DISPLAY/>") as display:
            result = markdown_html.to_html(markdown)
        return result, inline, display

    def test_copied_inline_and_display_use_correct_renderers(self):
        result, inline, display = self.render(
            r"Limit \\(L\\) and \\(x\\)." + "\n"
            r"\\[ \lim\_{x\to a}f(x)=L \\]"
        )
        self.assertEqual(inline.call_count, 2)
        inline.assert_any_call("L")
        inline.assert_any_call("x")
        display.assert_called_once_with(r"\lim_{x\to a}f(x)=L")
        self.assertNotIn(r"\lim", result)

    def test_copied_array_preserves_row_breaks_and_spacing(self):
        _, inline, display = self.render(r"\\[" + ARRAY + r"\\]")
        inline.assert_not_called()
        display.assert_called_once_with(" ".join(ARRAY.split()))

    def test_preserves_literal_text_symbols_and_normal_tex_escapes(self):
        tex = r"x\_1 + \text{a\_b {c\_d}} + \operatorname{a\_b} + y\_{2} + \{5\%\}"
        _, _, display = self.render(r"\\[" + tex + r"\\]")
        display.assert_called_once_with(
            r"x_1 + \text{a\_b {c\_d}} + \operatorname{a\_b} + y_{2} + \{5\%\}"
        )

    def test_regular_delimiters_leave_tex_untouched(self):
        tex = r"\text{a\_b} + \lim_{x\to a}f(x)"
        _, _, display = self.render(r"\[" + tex + r"\]")
        display.assert_called_once_with(tex)

    def test_does_not_convert_fenced_or_inline_code(self):
        markdown = (
            "```latex\n" + r"\\[x\_1\\]" + "\n```\n"
            "~~~latex\n" + r"\[y_2\]" + "\n~~~\n"
            r"Use `\\[x\_1\\]` or ``\[y_2\]`` and `\\(z\\)`."
        )
        self.assertEqual(markdown_html._prepare_math(markdown), markdown)
        _, inline, display = self.render(markdown)
        inline.assert_not_called()
        display.assert_not_called()

    def test_copied_formula_renders_with_mathjax(self):
        result = markdown_html.to_html(r"\\[\boxed{\lim\_{x\to a^-}f(x)=L}\\]")
        self.assertIn('class="math-block"', result)
        self.assertIn("data:image/png;base64,", result)
        self.assertNotIn("<code class='tex'>", result)


class HtmlScrollTests(unittest.TestCase):
    def test_show_pins_chat_to_bottom_across_layout_passes(self) -> None:
        class View:
            def __init__(self):
                self.loaded = None
                self.fragment = None
                self.delays = []
                self.positions = []

            def load_html(self, html, fragment=None):
                self.loaded = html
                self.fragment = fragment

            def after(self, delay, callback):
                self.delays.append(delay)
                callback()

            def yview_moveto(self, fraction):
                self.positions.append(fraction)

        view = View()
        markdown_html.show(view, "<p>Answer</p>", scroll_to_end=True)

        self.assertEqual(view.fragment, "rtc-chat-end")
        self.assertIn("id='rtc-chat-end'", view.loaded)
        self.assertEqual(view.delays, [0, 40, 120, 300, 700])
        self.assertEqual(view.positions, [1.0] * 5)

    def test_latest_scroll_request_cancels_older_delayed_moves(self) -> None:
        class View:
            def __init__(self):
                self.callbacks = []
                self.positions = []

            def after(self, _delay, callback):
                self.callbacks.append(callback)

            def yview_moveto(self, fraction):
                self.positions.append(fraction)

        view = View()
        markdown_html.scroll_to(view, 1.0)
        markdown_html.scroll_to(view, 0.0)
        for callback in view.callbacks:
            callback()

        self.assertEqual(view.positions, [0.0] * 5)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

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

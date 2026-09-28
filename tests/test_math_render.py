from __future__ import annotations

import unittest

from caption import markdown_html, math_render


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
        self.assertIn('class="math-array"', result)
        self.assertNotIn("\\begin{array}", result)

    def test_nested_fraction_with_shorthand_arguments(self) -> None:
        tex = r"\frac{1^2+\cdots+n^2}{n^3} \approx \frac{\frac13n^3}{n^3} = \frac13,"
        rows, _boxed = math_render._normalise(tex)
        self.assertIn(r"\frac{\frac{1}{3}n^3}{n^3}", rows[0])
        result = math_render.display_html(tex)
        self.assertIn("data:image/png;base64,", result)
        self.assertNotIn("<code class='tex'>", result)


if __name__ == "__main__":
    unittest.main()

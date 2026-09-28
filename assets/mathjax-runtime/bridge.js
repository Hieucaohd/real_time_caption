/* Server-side TeX -> SVG bridge used by caption/mathjax_render.py.
 *
 * MathJax's lite DOM is deliberately used here.  TkinterWeb's experimental DOM
 * bridge is not complete enough for MathJax's browser component, whereas the
 * lite adaptor is the supported MathJax approach for non-browser applications.
 */
"use strict";

const {mathjax} = require("mathjax-full/js/mathjax.js");
const {TeX} = require("mathjax-full/js/input/tex.js");
const {SVG} = require("mathjax-full/js/output/svg.js");
const {liteAdaptor} = require("mathjax-full/js/adaptors/liteAdaptor.js");
const {RegisterHTMLHandler} = require("mathjax-full/js/handlers/html.js");
const {AllPackages} = require("mathjax-full/js/input/tex/AllPackages.js");

const EM = 16;
const EX = 8;
const adaptor = liteAdaptor({fontSize: EM});
RegisterHTMLHandler(adaptor);

const input = new TeX({
  packages: AllPackages,
  formatError(jax, error) {
    throw error;
  },
});
const output = new SVG({fontCache: "none"});
const document = mathjax.document("", {InputJax: input, OutputJax: output});

exports.render = function render(tex, display, containerWidth) {
  const node = document.convert(String(tex), {
    display: Boolean(display),
    em: EM,
    ex: EX,
    containerWidth: Number(containerWidth || 80 * EM),
  });
  return adaptor.outerHTML(node);
};

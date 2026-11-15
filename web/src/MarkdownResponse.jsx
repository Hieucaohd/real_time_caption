import React, { useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";
import rehypeKatex from "rehype-katex";
import rehypeHighlight from "rehype-highlight";
import { Check, Copy } from "lucide-react";
import remarkDisplayMath from "./remarkDisplayMath";

function nodeText(node) {
  return node?.type === "text"
    ? node.value
    : (node?.children || []).map(nodeText).join("");
}

function CodeBlock({ node, children, ...props }) {
  const code = node.children.find((child) => child.tagName === "code");
  const language =
    code?.properties?.className
      ?.find((name) => name.startsWith("language-"))
      ?.slice(9) || "text";
  const [status, setStatus] = useState("");
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  async function copy() {
    try {
      await navigator.clipboard.writeText(nodeText(code || node));
      setStatus("copied");
    } catch {
      setStatus("error");
    }
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setStatus(""), 2000);
  }
  return (
    <div className="response-code-block">
      <div className="response-code-header">
        <span>{language}</span>
        <button type="button" onClick={copy} aria-label="Copy code">
          {status === "copied" ? <Check size={14} /> : <Copy size={14} />}
          <span aria-live="polite">
            {status === "copied"
              ? "Copied!"
              : status === "error"
                ? "Không copy được"
                : "Copy code"}
          </span>
        </button>
      </div>
      <pre {...props} tabIndex={0}>
        {children}
      </pre>
    </div>
  );
}

const components = {
  pre: CodeBlock,
  table: ({ node, ...props }) => (
    <div
      className="response-table-scroll"
      role="region"
      aria-label="Bảng trong câu trả lời"
      tabIndex={0}
    >
      <table {...props} />
    </div>
  ),
  a: ({ node, ...props }) => (
    <a {...props} target="_blank" rel="noopener noreferrer" />
  ),
};
const remarkPlugins = [remarkGfm, remarkMath, remarkDisplayMath];
const rehypePlugins = [
  [rehypeKatex, { strict: false, throwOnError: false, trust: false }],
  [rehypeHighlight, { detect: false, ignoreMissing: true }],
];

export default React.memo(function MarkdownResponse({ text }) {
  return (
    <div className="markdown response-markdown">
      <Markdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={components}
      >
        {text || ""}
      </Markdown>
    </div>
  );
});

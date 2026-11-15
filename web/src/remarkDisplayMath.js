// ChatGPT Copy normalizes \[...\] to $$...$$ on one line. remark-math
// parses that as inlineMath; preserve its display intent before KaTeX runs.
export default function remarkDisplayMath() {
  return (tree, file) => {
    const source = String(file);
    function walk(node) {
      if (node.type === "inlineMath" && node.position) {
        const raw = source.slice(
          node.position.start.offset,
          node.position.end.offset,
        );
        if (raw.startsWith("$$") && raw.endsWith("$$")) {
          node.data = {
            ...node.data,
            hProperties: {
              ...node.data?.hProperties,
              className: ["language-math", "math-display"],
            },
          };
        }
      }
      node.children?.forEach(walk);
    }
    walk(tree);
  };
}

import React, { useEffect, useMemo, useState } from "react";
import { readingMarkRange } from "./readingMark";

function sourceRange(root, group) {
  if (group.mark) return readingMarkRange(root, group.mark);
  // Old nodes only saved an excerpt. Never guess between repeated occurrences.
  const text = root.textContent, start = text.indexOf(group.excerpt);
  if (start < 0 || text.indexOf(group.excerpt, start + 1) >= 0) return null;
  return readingMarkRange(root, { start, end: start + group.excerpt.length, text: group.excerpt });
}

export default function BranchMarks({ answerRef, nodeId, content, marks = [] }) {
  const signature = JSON.stringify(marks);
  const groups = useMemo(() => {
    const byRange = new Map();
    for (const child of JSON.parse(signature)) {
      const key = JSON.stringify(child.mark || child.excerpt);
      if (!byRange.has(key)) byRange.set(key, { key, mark: child.mark, excerpt: child.excerpt, children: [] });
      byRange.get(key).children.push(child);
    }
    return [...byRange.values()];
  }, [signature]);
  const [hover, setHover] = useState(null);

  useEffect(() => {
    setHover(null);
    const root = answerRef.current;
    if (!root) return;
    const ranges = groups.map((group) => ({ group, range: sourceRange(root, group) })).filter((item) => item.range);
    CSS.highlights.set("rtc-branch-marks", new Highlight(...ranges.map((item) => item.range)));
    const clear = () => setHover(null);
    const move = (event) => {
      if (!window.getSelection()?.isCollapsed) return clear();
      const hits = ranges.filter(({ range }) => [...range.getClientRects()].some((rect) =>
        event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom));
      if (!hits.length) return clear();
      setHover({ children: hits.flatMap(({ group }) => group.children),
        x: Math.max(8, Math.min(event.clientX + 12, window.innerWidth - 328)), y: event.clientY });
    };
    const pane = root.closest(".research-reading");
    root.addEventListener("pointermove", move);
    root.addEventListener("pointerleave", clear);
    root.addEventListener("pointerdown", clear);
    pane?.addEventListener("scroll", clear);
    return () => {
      CSS.highlights.delete("rtc-branch-marks");
      root.removeEventListener("pointermove", move);
      root.removeEventListener("pointerleave", clear);
      root.removeEventListener("pointerdown", clear);
      pane?.removeEventListener("scroll", clear);
    };
  }, [answerRef, nodeId, content, groups]);

  const names = (children) => <ul>{children.map((child) => <li key={child.id}>
    {child.title}{child.hidden ? " (đã ẩn)" : ""}
  </li>)}</ul>;
  if (!groups.length) return null;
  return <>
    <details className="branch-marks-list">
      <summary>Các đoạn đã tạo nhánh ({groups.length})</summary>
      <p className="muted">Dấu xanh chỉ để xem node con được tạo từ đoạn nào. Di chuột lên đoạn gạch chân để xem tên node.</p>
      {groups.map((group) => <div className="branch-mark-entry" key={group.key}>
        <blockquote>{group.excerpt}</blockquote>
        <small>Node con đã tạo:</small>{names(group.children)}
      </div>)}
    </details>
    {hover && <div role="tooltip" className="branch-mark-tooltip" style={{ left: hover.x,
      ...(hover.y > window.innerHeight / 2 ? { bottom: window.innerHeight - hover.y + 12 } : { top: hover.y + 16 }) }}>
      <strong>Node con từ đoạn này</strong>{names(hover.children)}
    </div>}
  </>;
}

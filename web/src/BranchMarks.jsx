import React, { useEffect, useMemo, useRef, useState } from "react";
import { readingMarkRange } from "./readingMark";

function sourceRange(root, group) {
  if (group.mark) return readingMarkRange(root, group.mark);
  // Old nodes only saved an excerpt. Never guess between repeated occurrences.
  const text = root.textContent, start = text.indexOf(group.excerpt);
  if (start < 0 || text.indexOf(group.excerpt, start + 1) >= 0) return null;
  return readingMarkRange(root, { start, end: start + group.excerpt.length, text: group.excerpt });
}

export default function BranchMarks({ answerRef, nodeId, content, marks = [], onSelect, onHover }) {
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
  const [picker, setPicker] = useState(null);
  const pickerRef = useRef(null), popup = useRef(null), actions = useRef({ onSelect, onHover });
  pickerRef.current = picker;
  actions.current = { onSelect, onHover };
  const open = (child) => {
    setHover(null);
    setPicker(null);
    actions.current.onSelect?.(child);
  };
  // Moving within the same marked passage only repositions the tooltip; avoid
  // rerendering the whole graph for every pixel of pointer movement.
  const activeIds = JSON.stringify((hover || picker)?.children.map((child) => child.id) || []);

  useEffect(() => {
    onHover?.(JSON.parse(activeIds));
  }, [activeIds, onHover]);

  useEffect(() => {
    if (!picker) return;
    popup.current?.querySelector("button")?.focus({ preventScroll: true });
    const close = () => { setPicker(null); setHover(null); };
    const outside = (event) => { if (!popup.current?.contains(event.target)) close(); };
    const key = (event) => { if (event.key === "Escape") { event.stopPropagation(); close(); } };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", key);
    };
  }, [picker]);

  useEffect(() => {
    setHover(null);
    setPicker(null);
    const root = answerRef.current;
    if (!root) return;
    const ranges = groups.map((group) => ({ group, range: sourceRange(root, group) })).filter((item) => item.range);
    CSS.highlights.set("rtc-branch-marks", new Highlight(...ranges.map((item) => item.range)));
    let down = null;
    const clear = () => { setHover(null); root.classList.remove("branch-mark-hover"); };
    const hit = (event) => {
      if (event.target.closest?.("a, button, input, textarea, select")) return [];
      const hits = ranges.filter(({ range }) => [...range.getClientRects()].some((rect) =>
        rect.width > 0 && rect.height > 0 && event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom));
      return [...new Map(hits.flatMap(({ group }) => group.children).map((child) => [child.id, child])).values()];
    };
    const position = (event, children) => ({ children,
      x: Math.max(8, Math.min(event.clientX + 12, window.innerWidth - 328)), y: event.clientY });
    const move = (event) => {
      if (pickerRef.current) return;
      if (event.buttons || !window.getSelection()?.isCollapsed) return clear();
      const children = hit(event);
      if (!children.length) return clear();
      root.classList.add("branch-mark-hover");
      setHover(position(event, children));
    };
    const press = (event) => { down = { x: event.clientX, y: event.clientY }; clear(); };
    const click = (event) => {
      if (event.button !== 0 || event.detail > 1 || !down ||
          Math.hypot(event.clientX - down.x, event.clientY - down.y) > 5 || !window.getSelection()?.isCollapsed) return;
      const children = hit(event);
      if (!children.length) return;
      event.preventDefault();
      clear();
      if (children.length === 1) actions.current.onSelect?.(children[0]);
      else setPicker(position(event, children));
    };
    const scroll = () => { clear(); setPicker(null); };
    const leave = () => {
      if (!pickerRef.current) clear();
      else root.classList.remove("branch-mark-hover");
    };
    const pane = root.closest(".research-reading");
    root.addEventListener("pointermove", move);
    root.addEventListener("pointerleave", leave);
    root.addEventListener("pointerdown", press);
    root.addEventListener("click", click);
    pane?.addEventListener("scroll", scroll);
    return () => {
      CSS.highlights.delete("rtc-branch-marks");
      root.removeEventListener("pointermove", move);
      root.removeEventListener("pointerleave", leave);
      root.removeEventListener("pointerdown", press);
      root.removeEventListener("click", click);
      root.classList.remove("branch-mark-hover");
      pane?.removeEventListener("scroll", scroll);
      actions.current.onHover?.([]);
    };
  }, [answerRef, nodeId, content, groups]);

  const names = (children, interactive = false) => <ul>{children.map((child) => <li key={child.id}>
    {interactive ? <button type="button" className="branch-mark-link" onClick={() => open(child)}
      onPointerEnter={() => setHover({ children: [child] })} onPointerLeave={() => setHover(null)}
      onFocus={() => setHover({ children: [child] })} onBlur={() => setHover(null)}>
      {child.title}{child.hidden ? " (đã ẩn)" : ""}
    </button> : <>{child.title}{child.hidden ? " (đã ẩn)" : ""}</>}
  </li>)}</ul>;
  const location = (item) => ({ left: item.x,
    ...(item.y > window.innerHeight / 2 ? { bottom: window.innerHeight - item.y + 12 } : { top: item.y + 16 }) });
  if (!groups.length) return null;
  return <>
    <details className="branch-marks-list">
      <summary>Các đoạn đã tạo nhánh ({groups.length})</summary>
      <p className="muted">Di chuột lên dấu xanh để làm sáng node con và đường nối. Bấm đoạn đánh dấu hoặc tên node để mở câu trả lời.</p>
      {groups.map((group) => <div className="branch-mark-entry" key={group.key}>
        <blockquote>{group.excerpt}</blockquote>
        <small>Node con đã tạo:</small>{names(group.children, true)}
      </div>)}
    </details>
    {!picker && hover?.x !== undefined && <div role="tooltip" className="branch-mark-tooltip" style={location(hover)}>
      <strong>Node con từ đoạn này</strong>{names(hover.children)}
      <small>Bấm để {hover.children.length === 1 ? "mở node con" : "chọn node con"}.</small>
    </div>}
    {picker && <div ref={popup} role="dialog" aria-label="Chọn node con" className="branch-mark-tooltip branch-mark-picker" style={location(picker)}>
      <strong>Chọn node con</strong>{names(picker.children, true)}
      <button type="button" onClick={() => { setPicker(null); setHover(null); }}>Đóng</button>
    </div>}
  </>;
}

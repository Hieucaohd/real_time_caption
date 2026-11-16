// Store a single yellow reading mark per answer without modifying its Markdown.
export function selectedReadingMark(root) {
  const selection = window.getSelection();
  if (!root || !selection?.rangeCount || selection.isCollapsed) return null;
  const range = selection.getRangeAt(0);
  if (!root.contains(range.startContainer) || !root.contains(range.endContainer)) return null;
  const text = range.toString();
  if (!text.trim()) return null;
  const before = range.cloneRange();
  before.selectNodeContents(root);
  before.setEnd(range.startContainer, range.startOffset);
  const start = before.toString().length;
  return { start, end: start + text.length, text };
}

export function readingMarkRange(root, mark) {
  if (!root || !mark) return null;
  const text = root.textContent;
  let { start, end } = mark;
  if (text.slice(start, end) !== mark.text) {
    start = text.indexOf(mark.text);
    if (start < 0) return null;
    end = start + mark.text.length;
  }
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let offset = 0, started = false, node;
  while ((node = walker.nextNode())) {
    const next = offset + node.length;
    if (!started && start <= next) {
      range.setStart(node, start - offset);
      started = true;
    }
    if (started && end <= next) {
      range.setEnd(node, end - offset);
      return range;
    }
    offset = next;
  }
  return null;
}

export function scrollToReadingMark(root, mark) {
  const range = readingMarkRange(root, mark);
  const pane = root?.closest(".research-reading");
  if (!range || !pane?.clientHeight) return;
  pane.scrollTop += range.getBoundingClientRect().top - pane.getBoundingClientRect().top - 20;
}

import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export default function ChildLinkPicker({ selection, nodes, onSave, close }) {
  const dialog = useRef(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const children = nodes.filter(node => node.parent_id === selection.parentId);
  const child = children.find(node => node.id === selected);
  const matches = children.filter(node => `${node.title} ${node.question}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()));
  useEffect(() => {
    const previous = document.activeElement;
    dialog.current.showModal();
    return () => { if (previous?.isConnected) previous.focus({ preventScroll: true }); };
  }, []);
  async function save(e) {
    e.preventDefault();
    if (!child || saving) return;
    setSaving(true);
    setError("");
    try { await onSave(child.id); }
    catch (e) { setError(e.message); setSaving(false); }
  }
  return createPortal(<dialog ref={dialog} className="child-link-picker" aria-labelledby="child-link-title"
    onCancel={e => { e.preventDefault(); if (!saving) close(); }}>
    <form onSubmit={save}>
      <h3 id="child-link-title">Chọn node con để liên kết</h3>
      <blockquote>{selection.excerpt}</blockquote>
      <input autoFocus aria-label="Tìm node con để liên kết" placeholder="Tìm node con…"
        value={query} onChange={e => setQuery(e.target.value)} disabled={saving} />
      <div className="child-link-options">
        {matches.map(node => <label key={node.id}>
          <input type="radio" name="child-link" value={node.id} checked={selected === node.id}
            onChange={() => setSelected(node.id)} disabled={saving} />
          <span>{node.title}{node.hidden ? " (đã ẩn)" : ""}</span>
        </label>)}
        {!matches.length && <p>{children.length ? "Không tìm thấy node con phù hợp." : "Node này chưa có node con. Hãy tạo nhánh con trước."}</p>}
      </div>
      {child?.excerpt && <p className="child-link-note">Node con này đã có đoạn liên kết. Lưu sẽ chuyển liên kết sang đoạn vừa bôi đen.</p>}
      {error && <p className="alert" role="alert">{error}</p>}
      <div className="inline">
        <button type="button" disabled={saving} onClick={close}>Hủy</button>
        <button type="submit" className="primary" disabled={!child || saving}>{saving ? "Đang lưu…" : "Lưu liên kết"}</button>
      </div>
    </form>
  </dialog>, document.body);
}

import React, { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

export default function ConversationNameModal({ initialTitle, rename, save, close }) {
  const [title, setTitle] = useState(initialTitle);
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const input = useRef(null), submitting = useRef(false);
  useEffect(() => { input.current?.focus(); input.current?.select(); }, []);
  async function submit(event) {
    event.preventDefault();
    if (!title.trim() || submitting.current) return;
    submitting.current = true; setBusy(true); setError("");
    try { await save(title.trim()); close(); }
    catch (e) { setError(e.message); }
    finally { submitting.current = false; setBusy(false); }
  }
  return <div className="modal-backdrop">
    <form className="modal" role="dialog" aria-modal="true" aria-labelledby="conversation-name-title"
      onSubmit={submit} onKeyDown={(e) => { if (e.key === "Escape" && !busy) { e.stopPropagation(); close(); } }}>
      <div className="modal-head">
        <h2 id="conversation-name-title">{rename ? "Đổi tên cuộc hội thoại" : "Tạo cuộc hội thoại mới"}</h2>
        <button type="button" onClick={close} disabled={busy} aria-label="Đóng đặt tên"><X size={18} /></button>
      </div>
      <div className="modal-body">
        <label className="field"><span>Tên cuộc hội thoại</span>
          <input ref={input} value={title} maxLength={200} required disabled={busy}
            onChange={(e) => setTitle(e.target.value)} />
        </label>
        {error && <p className="alert" role="alert">{error}</p>}
      </div>
      <div className="modal-foot">
        <button type="button" onClick={close} disabled={busy}>Hủy</button>
        <button type="submit" className="primary" disabled={busy || !title.trim()}>
          {busy ? "Đang lưu…" : rename ? "Lưu tên" : "Tạo cuộc hội thoại"}
        </button>
      </div>
    </form>
  </div>;
}

import React, { useState } from "react";

function conversationLink(value) {
  try {
    const url = new URL(value);
    if (url.protocol === "https:" && ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(url.hostname)
      && /\/c\/[^/]+\/?$/.test(url.pathname)) return url.href;
  } catch { /* Older answers have no recorded source. */ }
  return null;
}

export default function ResponseSource({ response, onSave }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const fields = [
    ["response_conversation_url", "Link cuộc hội thoại ChatGPT", "url"],
    ["response_position", "Số thứ tự câu trả lời", "number"],
  ];
  const url = conversationLink(response.response_conversation_url);
  async function save(e) {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    setError("");
    try {
      await onSave({ ...draft, response_position: draft.response_position === "" ? null : Number(draft.response_position) });
      setEditing(false);
    } catch (e) {
      let message = e.message;
      try {
        const details = JSON.parse(message);
        if (Array.isArray(details)) message = details.map((item) => item.msg?.replace(/^Value error, /, "")).filter(Boolean).join(" · ");
      } catch { /* Plain API or network error. */ }
      setError(message);
    } finally { setSaving(false); }
  }
  return (
    <details className="response-source">
      <summary>Nguồn ChatGPT</summary>
      {url ? <a href={url} target="_blank" rel="noopener noreferrer">{url}</a>
        : <p>Chưa lưu link cuộc hội thoại cho câu trả lời này.</p>}
      <dl>
        <dt>Vị trí trong cuộc hội thoại</dt>
        <dd>{response.response_position ? `Câu trả lời thứ ${response.response_position}` : "Chưa có số thứ tự"}</dd>
      </dl>
      {onSave && !editing && <button type="button" onClick={() => {
        setDraft(Object.fromEntries(fields.map(([key]) => [key, response[key] || ""])));
        setError("");
        setEditing(true);
      }}>Cập nhật nguồn</button>}
      {editing && <form className="response-source-editor" onSubmit={save}>
        <p>Nhập thông tin bạn có; trường chưa biết có thể để trống.</p>
        <p>Đếm các câu trả lời của ChatGPT từ đầu cuộc hội thoại: 1, 2, 3… Không đếm tin nhắn của bạn.</p>
        {fields.map(([key, title, type]) => <label className="field" key={key}>
          <span>{title}</span>
          <input aria-label={title} type={type} value={draft[key]} disabled={saving}
            maxLength={type === "url" ? 2000 : undefined}
            min={type === "number" ? 1 : undefined} max={type === "number" ? 2147483647 : undefined}
            step={type === "number" ? 1 : undefined}
            onChange={(e) => setDraft((old) => ({ ...old, [key]: e.target.value }))} />
        </label>)}
        {error && <p className="alert" role="alert">{error}</p>}
        <div className="inline">
          <button className="primary" type="submit" disabled={saving}>{saving ? "Đang lưu…" : "Lưu nguồn"}</button>
          <button type="button" disabled={saving} onClick={() => setEditing(false)}>Hủy</button>
        </div>
      </form>}
    </details>
  );
}

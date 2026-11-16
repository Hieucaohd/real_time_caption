import React, { useEffect, useState } from "react";

export default function LiveCaptionsSettings({ api }) {
  const [draft, setDraft] = useState({ microphone: null, language: "" });
  const [languages, setLanguages] = useState([]);
  const [actual, setActual] = useState(null);
  const [busy, setBusy] = useState("loading");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let live = true;
    api("/livecaptions/settings").then(value => { if (live) setDraft(value); })
      .catch(e => { if (live) setError(e.message); })
      .finally(() => { if (live) setBusy(""); });
    return () => { live = false; };
  }, [api]);
  async function run(save) {
    if (busy) return;
    setBusy(save ? "saving" : "reading"); setError(""); setNotice("");
    try {
      const value = await api(save ? "/livecaptions/settings" : "/livecaptions/inspect", {
        method: save ? "PUT" : "POST", ...(save ? { body: draft } : {}),
      });
      setLanguages(value.languages); setActual(value);
      if (!save) setDraft(old => ({
        microphone: old.microphone ?? value.microphone,
        language: old.language || value.language,
      }));
      setNotice(!value.ready
        ? `${save ? "Đã lưu lựa chọn. " : ""}Windows cần hoàn tất thiết lập hoặc tải gói ngôn ngữ. Mở cửa sổ Live Captions để tiếp tục, sau đó bấm Lưu và áp dụng lại.`
        : save ? "Đã lưu và xác nhận cài đặt trên Windows Live Captions." : "Đã đọc trạng thái và danh sách ngôn ngữ từ Windows.");
    } catch (e) { setError(e.message); }
    finally { setBusy(""); }
  }
  return <section className="livecaptions-settings" aria-label="Cài đặt Windows Live Captions">
    <h3>Windows Live Captions</h3>
    <p>Điều khiển micro và ngôn ngữ phụ đề của Windows. Lựa chọn được lưu và áp dụng lại khi bắt đầu lấy transcript.</p>
    <button type="button" disabled={!!busy} onClick={() => run(false)}>
      {busy === "reading" ? "Đang đọc từ Windows…" : "Tải lại ngôn ngữ và trạng thái từ Windows"}
    </button>
    {actual && <small>Windows: {actual.language} · Micro: {actual.microphone == null ? "Chưa xác nhận" : actual.microphone ? "Bật" : "Tắt"}</small>}
    <label className="inline">
      <input type="checkbox" checked={draft.microphone === true} disabled={!!busy}
        onChange={e => setDraft(old => ({ ...old, microphone: e.target.checked }))} />
      Nhận giọng nói từ micro
    </label>
    {draft.microphone === null && <small>Chưa lưu tùy chọn micro; app giữ nguyên trạng thái Windows.</small>}
    <label className="field"><span>Ngôn ngữ Windows Live Captions</span>
      <select aria-label="Ngôn ngữ Windows Live Captions" value={draft.language} disabled={!!busy}
        onChange={e => setDraft(old => ({ ...old, language: e.target.value }))}>
        <option value="">Giữ ngôn ngữ hiện tại của Windows</option>
        {draft.language && !languages.includes(draft.language) && <option value={draft.language}>{draft.language} (đã lưu)</option>}
        {languages.map(language => <option key={language} value={language}>{language}</option>)}
      </select>
    </label>
    <small>Bấm tải lại để lấy danh sách Windows hỗ trợ. Đây là ngôn ngữ Live Captions, độc lập với phần Translation bên dưới.</small>
    <div><button type="button" className="primary" disabled={!!busy} onClick={() => run(true)}>
      {busy === "saving" ? "Đang áp dụng…" : "Lưu và áp dụng cho Windows"}
    </button></div>
    {notice && <p role="status">{notice}</p>}
    {error && <p className="alert" role="alert">{error}</p>}
  </section>;
}

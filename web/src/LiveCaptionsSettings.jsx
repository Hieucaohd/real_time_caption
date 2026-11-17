import React, { useEffect, useId, useRef, useState } from "react";

export default function LiveCaptionsSettings({ api, warning = "" }) {
  const [draft, setDraft] = useState({ microphone: null, language: "" });
  const [languages, setLanguages] = useState([]);
  const [actual, setActual] = useState(null);
  const [busy, setBusy] = useState("loading");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [languageOpen, setLanguageOpen] = useState(false);
  const languageId = useId();
  const chooser = useRef(null), trigger = useRef(null), list = useRef(null), running = useRef(false);
  useEffect(() => {
    if (!languageOpen) return;
    const outside = (event) => { if (!chooser.current?.contains(event.target)) setLanguageOpen(false); };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [languageOpen]);
  useEffect(() => {
    if (languageOpen && !busy) list.current?.querySelector('[aria-selected="true"]')?.focus();
  }, [languageOpen, busy]);
  useEffect(() => {
    let live = true;
    api("/livecaptions/settings").then(value => { if (live) setDraft(value); })
      .catch(e => { if (live) setError(e.message); })
      .finally(() => { if (live) setBusy(""); });
    return () => { live = false; };
  }, [api]);
  async function run(save) {
    if (busy || running.current) return;
    running.current = true;
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
    finally { running.current = false; setBusy(""); }
  }
  function openLanguages() {
    if (busy || running.current) return;
    if (languageOpen) { setLanguageOpen(false); return; }
    setLanguageOpen(true);
    run(false);
  }
  const choices = ["", ...new Set([draft.language, ...languages].filter(Boolean))];
  return <section className="livecaptions-settings" aria-label="Cài đặt Windows Live Captions">
    <h3>Windows Live Captions</h3>
    <p>Cài đặt đã lưu được tự khôi phục khi Windows Live Captions mở lại. App chỉ áp dụng một lần cho mỗi phiên cửa sổ, không lặp lại khi tạo cuộc hội thoại hoặc Pause / Continue.</p>
    {warning && <p role="status">{warning}</p>}
    {actual && <small>Windows: {actual.language} · Micro: {actual.microphone == null ? "Chưa xác nhận" : actual.microphone ? "Bật" : "Tắt"}</small>}
    <label className="inline">
      <input type="checkbox" checked={draft.microphone === true} disabled={!!busy}
        onChange={e => setDraft(old => ({ ...old, microphone: e.target.checked }))} />
      Nhận giọng nói từ micro
    </label>
    {draft.microphone === null && <small>Chưa lưu tùy chọn micro; app giữ nguyên trạng thái Windows.</small>}
    <div className="field livecaptions-language" ref={chooser}
      onKeyDown={(event) => {
        if (event.key === "Escape" && languageOpen) {
          event.stopPropagation(); setLanguageOpen(false); trigger.current?.focus();
        }
      }}>
      <span id={languageId}>Ngôn ngữ Windows Live Captions</span>
      <button type="button" role="combobox" aria-labelledby={languageId} aria-haspopup="listbox"
        aria-expanded={languageOpen} aria-controls={`${languageId}-options`} aria-busy={busy === "reading"}
        ref={trigger} disabled={!!busy} onClick={openLanguages}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (!languageOpen) openLanguages();
            else list.current?.querySelector('[role="option"]')?.focus();
          }
        }}>
        <span>{busy === "reading" ? "Đang tải ngôn ngữ từ Windows…" : draft.language || "Giữ ngôn ngữ hiện tại của Windows"}</span><span aria-hidden="true">▾</span>
      </button>
      {languageOpen && <div className="livecaptions-language-options" ref={list}
        id={`${languageId}-options`} role="listbox" aria-labelledby={languageId} aria-busy={busy === "reading"}
        onKeyDown={(event) => {
          const options = [...list.current.querySelectorAll('[role="option"]')];
          const index = options.indexOf(document.activeElement);
          const next = event.key === "ArrowDown" ? (index + 1) % options.length
            : event.key === "ArrowUp" ? (index - 1 + options.length) % options.length
            : event.key === "Home" ? 0 : event.key === "End" ? options.length - 1 : null;
          if (next !== null) { event.preventDefault(); options[next]?.focus(); }
        }}>
        {busy === "reading" ? <div role="status">Đang tải ngôn ngữ từ Windows…</div> : choices.map(language => (
          <button type="button" role="option" aria-selected={draft.language === language} key={language}
            onClick={() => { setDraft(old => ({ ...old, language })); setLanguageOpen(false); trigger.current?.focus(); }}>
            {language || "Giữ ngôn ngữ hiện tại của Windows"}
          </button>
        ))}
      </div>}
    </div>
    <small>Mở ô chọn để lấy danh sách mới từ Windows. Ngôn ngữ phụ đề này độc lập với phần Translation bên dưới.</small>
    <div><button type="button" className="primary" disabled={!!busy} onClick={() => run(true)}>
      {busy === "saving" ? "Đang áp dụng…" : "Lưu và áp dụng cho Windows"}
    </button></div>
    {notice && <p role="status">{notice}</p>}
    {error && <p className="alert" role="alert">{error} Lỗi thao tác cài đặt không dừng việc đọc phụ đề; hãy kiểm tra trạng thái hiện tại trong Windows trước khi thử lại.</p>}
  </section>;
}

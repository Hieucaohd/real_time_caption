import React, { useRef, useState } from "react";
import { RefreshCw } from "lucide-react";

export default function RecopyAnswer({ onCopy, disabled = false }) {
  const running = useRef(false);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  async function copyAgain() {
    if (running.current) return;
    running.current = true; setBusy(true); setError(""); setNotice("");
    try {
      await onCopy();
      setNotice("Đã lấy lại và lưu câu trả lời.");
    } catch (e) { setError(e.message); }
    finally { running.current = false; setBusy(false); }
  }
  return <span className="recopy-answer inline">
    <button type="button" disabled={disabled || busy} onClick={copyAgain}
      title="Copy lại response của cùng câu hỏi trên ChatGPT và cập nhật nội dung đã lưu; không gửi lại câu hỏi">
      <RefreshCw size={14} className={busy ? "spin" : ""} />
      {busy ? "Đang lấy lại…" : "Lấy lại câu trả lời"}
    </button>
    {error && <small role="alert">{error}</small>}
    {notice && <small role="status">{notice}</small>}
  </span>;
}

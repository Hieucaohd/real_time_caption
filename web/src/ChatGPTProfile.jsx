import React, { useEffect, useRef, useState } from "react";

const plans = [
  ["free", "Free"], ["go", "Go"], ["plus", "Plus"], ["pro", "Pro"],
  ["business", "Business"], ["enterprise", "Enterprise"], ["edu", "Edu"], ["other", "Khác"],
];

export function SignedOutNotice({ openSettings }) {
  return <p className="chatgpt-free-notice">Các thao tác ChatGPT đang tắt. Sau khi đăng nhập trên Chrome, chọn “Đã đăng nhập” trong{" "}
    {openSettings ? <button type="button" onClick={openSettings}>Mở cài đặt ChatGPT</button> : "Settings ở cửa sổ chính"}.
  </p>;
}

export function FreeAttachmentNotice({ setup = false }) {
  return <p className="chatgpt-free-notice">
    {setup
      ? "Với gói Free, app tắt đính kèm file, dán ảnh và chụp màn hình. Bạn vẫn có thể chat bằng văn bản theo hạn mức của ChatGPT."
      : "App đã tắt đính kèm file, dán ảnh và chụp màn hình cho gói Free. Bạn có thể đổi gói trong Settings."}
  </p>;
}

export function ChatGPTProfileFields({ value, set, disabled }) {
  return <>
    <div className="form-grid">
      <label className="field">
        <span>Gói ChatGPT</span>
        <select value={value.chatgpt_plan} onChange={e => set("chatgpt_plan", e.target.value)} required disabled={disabled}>
          <option value="" disabled>Chọn gói…</option>
          {plans.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
      </label>
      <label className="field">
        <span>Đã đăng nhập ChatGPT trên Chrome của app?</span>
        <select value={value.chatgpt_login_status} onChange={e => set("chatgpt_login_status", e.target.value)} required disabled={disabled}>
          <option value="" disabled>Chọn trạng thái…</option>
          <option value="signed_in">Đã đăng nhập</option>
          <option value="signed_out">Chưa đăng nhập</option>
        </select>
      </label>
    </div>
    {value.chatgpt_login_status === "signed_out" ? <SignedOutNotice />
      : value.chatgpt_plan === "free" && <FreeAttachmentNotice setup />}
  </>;
}

export default function ChatGPTProfileDialog({ settings, save }) {
  const dialog = useRef(null);
  const [value, setValue] = useState(() => ({
    chatgpt_plan: settings.chatgpt_plan || "",
    chatgpt_login_status: settings.chatgpt_login_status || "",
  }));
  const [saving, setSaving] = useState(false), [error, setError] = useState("");
  useEffect(() => {
    const element = dialog.current;
    element.showModal();
    return () => element.close();
  }, []);
  async function submit(e) {
    e.preventDefault();
    if (saving || !value.chatgpt_plan || !value.chatgpt_login_status) return;
    setSaving(true);
    setError("");
    try { await save(value); }
    catch (err) { setError(err.message); }
    finally { setSaving(false); }
  }
  return <dialog ref={dialog} className="modal chatgpt-profile-dialog" aria-labelledby="chatgpt-profile-title" onCancel={e => e.preventDefault()}>
    <form onSubmit={submit}>
      <div className="modal-head"><h2 id="chatgpt-profile-title">Thiết lập ChatGPT</h2></div>
      <div className="modal-body">
        <ChatGPTProfileFields value={value} set={(key, choice) => setValue(old => ({ ...old, [key]: choice }))} disabled={saving} />
        {error && <p role="alert">{error}</p>}
      </div>
      <div className="modal-foot">
        <span>Có thể thay đổi sau trong Settings.</span>
        <button type="submit" className="primary" disabled={saving || !value.chatgpt_plan || !value.chatgpt_login_status}>
          {saving ? "Đang lưu…" : "Lưu và tiếp tục"}
        </button>
      </div>
    </form>
  </dialog>;
}

import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Camera, Copy, MessageSquare, Paperclip, Play, Save, Send, Settings2, Square, ArrowUp, ArrowDown, PanelTop } from "lucide-react";
import ChatOptions from "./ChatOptions";
import PromptModal from "./PromptModal";
import RealtimePrompts from "./RealtimePrompts";
import AttachmentList from "./AttachmentList";
import { FreeAttachmentNotice } from "./ChatGPTProfile";
import Md from "./MarkdownResponse";
import "./realtime-chat.css";

const stamp = value => new Date(value).toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const attachments = files => (files || []).map(path => ({ path, name: path.split(/[\\/]/).at(-1) }));
const powerLabel = power => ["Giữ nguyên", "Instant", "Medium", "High"][(power ?? -1) + 1];

export default function RealtimeChat({ cid, api, post, prompts, refreshPrompts, popup = false }) {
  const [state, setState] = useState(null), [config, setConfig] = useState(null);
  const [busy, setBusy] = useState(false), [uploading, setUploading] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [message, setMessage] = useState(""), [files, setFiles] = useState([]);
  const [editingPrompt, setEditingPrompt] = useState(false), [follow, setFollow] = useState(true);
  const [older, setOlder] = useState([]), [moreHistory, setMoreHistory] = useState(true);
  const initialized = useRef(false), sending = useRef(false), alive = useRef(true);
  const input = useRef(null), fileInput = useRef(null), chatScroll = useRef(null), summaryScroll = useRef(null);
  const base = `/conversations/${cid}/realtime-chat`;
  async function load() {
    const result = await api(base);
    if (!alive.current) return;
    setState(result);
    if (!initialized.current) setFollow(result.auto_scroll ?? true);
    if (popup || !initialized.current) { initialized.current = true; setConfig(result.config); }
  }
  useEffect(() => {
    alive.current = true;
    let timer;
    const tick = async () => {
      try { await load(); } catch (e) { if (alive.current) setError(e.message); }
      if (alive.current) timer = setTimeout(tick, 1000);
    };
    tick();
    return () => { alive.current = false; clearTimeout(timer); };
  }, [cid]);
  const events = [...new Map([...older, ...(state?.events || [])].map(event => [event.id, event])).values()].sort((a, b) => a.id - b.id);
  const chat = events.filter(event => event.kind === "chat");
  const summaries = events.filter(event => event.kind === "summary" && event.status === "done");
  const lanes = state?.lanes || {};
  const working = Object.values(lanes).some(lane => lane.status === "working");
  const active = !!state?.running || working || chat.some(e => e.status === "queued");
  const settingsLocked = busy || !!state?.settings_locked || active;
  const attachmentsAllowed = state?.attachments_allowed !== false;
  useEffect(() => { if (settingsLocked) setEditingPrompt(false); }, [settingsLocked]);
  useLayoutEffect(() => {
    if (follow) for (const ref of [chatScroll, summaryScroll])
      if (ref.current) ref.current.scrollTop = ref.current.scrollHeight;
  }, [follow, chat.at(-1)?.answer, chat.length, summaries.length, lanes.actions?.partial, lanes.summary?.partial]);
  useLayoutEffect(() => {
    const area = input.current;
    if (area) { area.style.height = "auto"; area.style.height = Math.min(area.scrollHeight, 180) + "px"; }
  }, [message, !!config]);
  async function action(fn) {
    if (sending.current) return;
    sending.current = true; setBusy(true); setError(""); setNotice("");
    try { await fn(); await load(); }
    catch (e) { if (alive.current) setError(e.message); }
    finally { sending.current = false; if (alive.current) setBusy(false); }
  }
  async function save() {
    await api(base + "/settings", { method: "PUT", body: config });
    setNotice("Đã lưu cài đặt Chat realtime.");
  }
  async function upload(selected) {
    if (!attachmentsAllowed) return;
    setUploading(true); setError("");
    try {
      for (const file of selected) {
        const body = new FormData(); body.append("file", file);
        const result = await post(`/conversations/${cid}/files`, body);
        if (alive.current) setFiles(old => [...old, { ...result, type: file.type }]);
      }
    } catch (e) { if (alive.current) setError(e.message); }
    finally { if (alive.current) setUploading(false); }
  }
  async function screenshot() {
    if (!attachmentsAllowed) return;
    let stream;
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({ video: true, audio: false });
      const video = document.createElement("video"); video.srcObject = stream; await video.play();
      const canvas = document.createElement("canvas"); canvas.width = video.videoWidth; canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0);
      const blob = await new Promise(resolve => canvas.toBlob(resolve, "image/png"));
      await upload([new File([blob], "screen.png", { type: "image/png" })]);
    } catch (e) { if (e.name !== "NotAllowedError") setError(e.message); }
    finally { stream?.getTracks().forEach(track => track.stop()); }
  }
  function send(actionId = "") {
    if (uploading || (!actionId && !message.trim())) return;
    action(async () => {
      if (!popup && !settingsLocked) await save();
      await post(base + "/messages", { message, action_id: actionId, files: attachmentsAllowed ? files.map(file => file.path) : [] });
      if (!actionId) setMessage("");
      setFiles([]); setNotice("");
    });
  }
  if (!config) return <div className="rt-loading">{error || "Đang tải Chat realtime…"}</div>;
  return <div className="realtime-chat">
    <div className="rt-toolbar">
      <strong><MessageSquare size={17} /> Chat realtime</strong>
      <span className="muted">{state.running ? `Đang theo dõi · ${config.interval} giây/lượt` : "Đã dừng"}</span>
      <label className="toggle"><input type="checkbox" checked={follow} onChange={e => setFollow(e.target.checked)} />Tự cuộn</label>
      {state.events.length === 200 && moreHistory && <button disabled={busy} onClick={() => action(async () => {
        setFollow(false);
        const result = await api(base + '/history?before=' + events[0].id);
        setOlder([...result, ...events]); setMoreHistory(result.length === 200);
      })}>Tải lịch sử trước</button>}
      {!popup && <button disabled={busy} onClick={() => action(() => post(`/conversations/${cid}/realtime-popup`))}><PanelTop size={16} /> Popup Chat realtime</button>}
      {!popup && <button disabled={settingsLocked} onClick={() => action(save)}><Save size={16} /> Lưu cài đặt</button>}
      <button className={active ? "danger" : "primary"} disabled={busy} onClick={() => action(async () => {
        if (active) await post(base + "/stop");
        else {
          // The popup always starts with the latest saved settings from the main UI.
          const current = popup ? (await api(base)).config : config;
          const result = await post(base + "/start", current); setConfig(result.config); setState(result);
        }
      })}>{active ? <Square size={16} /> : <Play size={16} />}{active ? "Stop" : "Start"}</button>
    </div>
    {!popup && <details className="chat-settings">
      <summary><Settings2 size={14} /><strong>Tùy chọn Chat realtime</strong><span>Hỏi đáp: {powerLabel(config.power)} · Tóm tắt: {powerLabel(config.summary_power ?? config.power)} · {config.interval} giây/lượt</span></summary>
      <ChatOptions value={config} onChange={patch => setConfig(old => ({ ...old, ...patch }))}
        attachmentsAllowed={attachmentsAllowed}
        prompts={prompts} managedContext
        editPrompt={() => setEditingPrompt(true)} scope="realtime" disabled={settingsLocked}>
        <label className="field"><span>Chu kỳ cập nhật (giây)</span><input aria-label="Chu kỳ Chat realtime" type="number" min="5" max="3600"
          value={config.interval} onChange={e => setConfig(old => ({ ...old, interval: Number(e.target.value) }))} /></label>
      </ChatOptions>
      <RealtimePrompts config={config} onChange={patch => setConfig(old => ({ ...old, ...patch }))}
        api={api} post={post} disabled={settingsLocked} />
      <p className="muted rt-help">Power gợi ý / hỏi đáp và Power tóm tắt áp dụng riêng cho từng tab. “Giữ nguyên” không đổi mức của tab tương ứng. Stop rồi Start dùng lại hai tab cũ nếu còn mở; chưa có transcript mới thì không gửi lượt tự động. Bấm Stop và chờ dừng xong để chỉnh cài đặt.</p>
    </details>}
    {error && <div className="rt-error" role="alert">{error}<button onClick={() => setError("")}>Đóng</button></div>}
    {Object.entries(lanes).filter(([, lane]) => lane.status === "error" && lane.error_code === "chatgpt_verification_required").map(([key, lane]) => (
      <div className="rt-error" role="alert" key={key}>
        <div><strong>Cần xác minh ChatGPT — {key === "actions" ? "Gợi ý / hỏi đáp" : "Tóm tắt"}</strong>
          <p>{lane.detail}</p>
          <p>Tab: {(key === "actions" ? config.selected_tab_title : config.summary_tab_title) || "ChatGPT"}. Sau khi xác minh, bấm Stop rồi Start để tiếp tục theo dõi.</p>
        </div>
      </div>
    ))}
    {notice && <p className="rt-notice" role="status">{notice}</p>}
    <div className="rt-columns">
      <section className="rt-chat-column" aria-label="Hỏi đáp realtime">
        <div className="rt-suggestions">
          <div className="rt-lane-status"><strong>Gợi ý hành động</strong><small>{lanes.actions?.detail || "Bấm Start để theo dõi cuộc trò chuyện"}</small></div>
          <div className="rt-action-buttons">{state.actions.map(item => <button key={item.id} title={item.prompt} disabled={busy || uploading} onClick={() => send(item.id)}>{item.label}</button>)}</div>
          {!state.actions.length && <small className="muted">Gợi ý sẽ xuất hiện khi ChatGPT nhận transcript mới.</small>}
        </div>
        <div className="chat-scroll scroll" ref={chatScroll}>
          {!chat.length && <p className="muted rt-empty">Chọn một gợi ý hoặc nhập câu hỏi để trao đổi về cuộc trò chuyện.</p>}
          {chat.map(event => <React.Fragment key={event.id}>
            <article className="message user"><div className="message-meta">Bạn · {stamp(event.created_at)}</div><p className="user-text">{event.question}</p>
              <AttachmentList cid={cid} files={attachments(event.files)} /></article>
            <article className="message bot"><div className="message-meta">ChatGPT</div>
              {event.answer ? <Md text={event.answer} /> : <p className="muted">{event.detail || (event.status === "queued" ? "Đã xếp hàng, chờ tab hỏi đáp sẵn sàng…" : "Đang chờ ChatGPT…")}</p>}
              {event.status === "done" && <button onClick={() => navigator.clipboard.writeText(event.answer).catch(e => setError(e.message))}><Copy size={14} /> Copy</button>}
              {["error", "cancelled", "interrupted"].includes(event.status) && <button onClick={() => { setMessage(event.question); setFiles(attachments(event.files)); input.current?.focus(); }}>Dùng lại câu hỏi</button>}
            </article>
          </React.Fragment>)}
          {lanes.actions?.kind === "chat" && lanes.actions?.partial && <article className="message bot preview"><Md text={lanes.actions.partial} /></article>}
        </div>
        <div className="composer chat-composer">
          {attachmentsAllowed ? <AttachmentList cid={cid} files={files} disabled={busy || uploading}
            onRemove={file => setFiles(old => old.filter(item => item.path !== file.path))} /> : <FreeAttachmentNotice />}
          <textarea ref={input} rows={1} aria-label="Câu hỏi Chat realtime" placeholder="Nhập câu hỏi… Enter để gửi, Shift + Enter xuống dòng"
            value={message} onChange={e => setMessage(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
            onPaste={e => { if (!attachmentsAllowed) return; const images = [...e.clipboardData.files].filter(file => file.type.startsWith("image/")); if (images.length) { e.preventDefault(); upload(images); } }} />
          <input type="file" multiple hidden ref={fileInput} disabled={!attachmentsAllowed} onChange={e => { upload([...e.target.files]); e.target.value = ""; }} />
          <div className="composer-actions"><div className="inline">
            <button disabled={uploading || !attachmentsAllowed} onClick={() => fileInput.current?.click()}><Paperclip size={16} /> Attach files</button>
            <button disabled={uploading || !attachmentsAllowed} onClick={screenshot}><Camera size={16} /> Screenshot</button>
            <button title="Lên đầu" onClick={() => chatScroll.current?.scrollTo({ top: 0 })}><ArrowUp size={16} /></button>
            <button title="Xuống cuối" onClick={() => chatScroll.current?.scrollTo({ top: chatScroll.current.scrollHeight })}><ArrowDown size={16} /></button>
          </div><button className="primary" disabled={busy || uploading || !message.trim()} onClick={() => send()}><Send size={16} /> Send</button></div>
        </div>
      </section>
      <section className="rt-summary-column" aria-label="Tóm tắt realtime">
        <div className="rt-lane-status"><strong>Tóm tắt cuộc trò chuyện</strong><small>{lanes.summary?.detail || "Tab riêng · cùng chu kỳ cập nhật"}</small></div>
        <div className="rt-summaries scroll" ref={summaryScroll}>
          {!summaries.length && <p className="muted rt-empty">Mỗi lượt thêm một bản tóm tắt phần mới. Nội dung được lưu vào cuộc hội thoại.</p>}
          {summaries.map(event => <article className="rt-summary" key={event.id}><time>{stamp(event.created_at)}</time><Md text={event.answer} /></article>)}
          {lanes.summary?.status === "working" && lanes.summary?.partial && <article className="rt-summary preview"><Md text={lanes.summary.partial} /></article>}
        </div>
      </section>
    </div>
    {!popup && editingPrompt && <PromptModal kind="chat" selected={config.prompt} api={api} post={post} close={() => setEditingPrompt(false)}
      done={async name => { setConfig(old => ({ ...old, prompt: name })); setEditingPrompt(false); await refreshPrompts(); }} />}
  </div>;
}

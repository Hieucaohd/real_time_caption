import React, { useState, useEffect, useLayoutEffect, useRef } from "react";
import { createRoot } from "react-dom/client";
import Md from "./MarkdownResponse";
import ResearchTab from "./ResearchTab";
import ResponseSource from "./ResponseSource";
import AttachmentList from "./AttachmentList";
import ChatOptions from "./ChatOptions";
import PromptModal from "./PromptModal";
import LiveCaptionsSettings from "./LiveCaptionsSettings";
import ChromeTranslatorSettings from "./ChromeTranslatorSettings";
import TranslationReading from "./TranslationReading";
import RecopyAnswer from "./RecopyAnswer";
import ConversationNameModal from "./ConversationNameModal";
import { chromeTranslator } from "./chromeTranslator";
import { createChatGPTTabLoader } from "./chatGPTTabs";
import { chatPreferences, savedChatPreferences } from "./chatPreferences";
import {
  AudioLines,
  Plus,
  Search,
  Settings2,
  Play,
  Pause,
  Download,
  Upload,
  Copy,
  MessageSquare,
  BookOpen,
  Languages,
  FileText,
  PanelTop,
  FolderOpen,
  Pencil,
  RefreshCw,
  Save,
  X,
  Send,
  Paperclip,
  Camera,
  Check,
  ArrowDown,
  ArrowUp,
  Square,
  ChevronRight,
  GitBranch,
  PanelLeftClose,
  PanelLeftOpen,
  Maximize2,
  Minimize2,
} from "lucide-react";
import "katex/dist/katex.min.css";
import "./style.css";
import "./reading-layout.css";
import "./response-markdown.css";
import "./theme.css";

// Unique per document, including duplicated app tabs. Chrome automation uses this
// marker to return to the exact caller without reloading its draft or modal.
const appTabId = crypto.randomUUID();
document.documentElement.dataset.rtcTabId = appTabId;
window.__rtcChromeTranslator = chromeTranslator;

async function api(path, options = {}, raw = false) {
  const headers = { "X-RTC-Client": "web", "X-RTC-Tab": appTabId, ...options.headers };
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.body);
  }
  const response = await fetch("/api" + path, { ...options, headers });
  if (!response.ok) {
    let detail;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = response.statusText;
    }
    const messages = Array.isArray(detail)
      ? detail.map((item) => typeof item.msg === "string" ? item.msg.replace(/^Value error, /, "") : "").filter(Boolean)
      : [];
    throw new Error(typeof detail === "string" ? detail
      : [...new Set(messages)].join(" · ") || "Yêu cầu không hợp lệ. Vui lòng kiểm tra lại thông tin.");
  }
  return raw ? response : response.json();
}
const post = (path, body = {}) => api(path, { method: "POST", body });
const tabs = [
  ["transcript", FileText, "Transcript"],
  ["chat", MessageSquare, "Chat"],
  ["research", GitBranch, "Nghiên cứu theo nhánh"],
  ["summary", BookOpen, "Summary"],
  ["translation", Languages, "Translation"],
  ["vocab", Plus, "New words"],
];
function Button({ children, icon: Icon, className = "", ...props }) {
  return (
    <button className={className} {...props}>
      {Icon && <Icon size={16} />} {children}
    </button>
  );
}
function Field({ label, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}
function Toggle({ label, checked, onChange }) {
  return (
    <label className="toggle">
      <input
        type="checkbox"
        checked={!!checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span>{label}</span>
    </label>
  );
}
function Empty({ icon: Icon = FileText, title, text }) {
  return (
    <div className="empty">
      <Icon size={32} />
      <h3>{title}</h3>
      <p>{text}</p>
    </div>
  );
}
const stamp = (text) =>
  text
    ? new Date(text).toLocaleString("vi-VN", {
        day: "2-digit",
        month: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "";

function App() {
  const [boot, setBoot] = useState(null),
    [detail, setDetail] = useState(null);
  const [cid, setCid] = useState(
    localStorage.getItem("rtc-conversation") || "",
  );
  const cidRef = useRef(cid);
  cidRef.current = cid;
  const [tab, setTab] = useState("transcript"),
    [query, setQuery] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [settingsOpen, setSettingsOpen] = useState(false),
    [promptKind, setPromptKind] = useState(null);
  const [conversationDialog, setConversationDialog] = useState(null);
  const [transferring, setTransferring] = useState("");
  const transferRef = useRef(false), importInput = useRef(null);
  const [researchFocus, setResearchFocus] = useState(null);
  const [sidebarHidden, setSidebarHidden] = useState(false),
    [readingFocus, setReadingFocus] = useState(false);
  const [chromeTabs, setChromeTabs] = useState([]),
    [loadingTabs, setLoadingTabs] = useState(false);
  const tabLoader = useRef(null), tabLoads = useRef(0), chatPreparation = useRef(0);
  if (!tabLoader.current) tabLoader.current = createChatGPTTabLoader(api, post);
  const [chatDraft, setChatDraft] = useState(null);
  const chatTabInitialized = useRef(false);
  const [savingChat, setSavingChat] = useState(false);
  const [preparingChatTab, setPreparingChatTab] = useState(false);
  const [message, setMessage] = useState(""),
    [files, setFiles] = useState([]),
    [uploading, setUploading] = useState(false);
  const [newChat, setNewChat] = useState(false),
    [summaryId, setSummaryId] = useState(null);
  const [words, setWords] = useState(""),
    [saveVoca, setSaveVoca] = useState(false),
    [vocabResult, setVocabResult] = useState([]);
  const [lineId, setLineId] = useState(null),
    [history, setHistory] = useState([]);
  const [follow, setFollow] = useState(true),
    [hiddenTranscript, setHiddenTranscript] = useState(false),
    [lastCount, setLastCount] = useState(0);
  const transcriptEnd = useRef(null),
    chatEnd = useRef(null),
    fileInput = useRef(null),
    chatScroll = useRef(null);
  const translationLinesScroll = useRef(null), translationHistoryScroll = useRef(null);
  const [translationFollowOverride, setTranslationFollowOverride] = useState(null);
  const [translationViewOverride, setTranslationViewOverride] = useState(null);
  const [savingTranslation, setSavingTranslation] = useState(false);
  const messageInput = useRef(null);
  useLayoutEffect(() => {
    const area = messageInput.current;
    if (!area) return;
    area.style.height = "0px";
    area.style.height = Math.min(140, Math.max(36, area.scrollHeight)) + "px";
  }, [message, tab, readingFocus, sidebarHidden]);
  const jobSeen = useRef(new Set());
  const [dismissedCaptureError, setDismissedCaptureError] = useState("");
  const captureError = boot?.capture?.error || (boot?.translation_error ? `Dịch: ${boot.translation_error} Transcript vẫn được thu bình thường khi đang ghi.` : "");
  useEffect(() => { setDismissedCaptureError(""); }, [captureError, boot?.capture?.conversation_id]);
  const visibleError = error || (captureError !== dismissedCaptureError ? captureError : "");
  const settings = boot?.settings || {},
    active = boot?.capture?.active;
  const captureSource = boot?.capture?.source || settings.web_capture_source || "windows";
  const captureLabel = captureSource === "chatgpt" ? "ChatGPT · Micro" : "Windows Live Captions";
  const chatConfig = chatDraft || chatPreferences(settings);
  const chatSelectedTab = chatConfig.selected_tab;
  const chatConfigRef = useRef(chatConfig);
  chatConfigRef.current = chatConfig;
  useEffect(() => {
    if (boot) setChatDraft((old) => old || chatPreferences(boot.settings));
  }, [!!boot]);
  useLayoutEffect(() => {
    if (!boot) return;
    const theme = settings.web_theme === "dark" ? "dark" : "light";
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("rtc-theme", theme); } catch { /* SQLite remains authoritative. */ }
  }, [settings.web_theme]);
  const current = boot?.conversations.find((c) => c.id === cid);
  const busyJob = boot?.jobs.find((j) =>
    ["queued", "working"].includes(j.status),
  );
  const jobs = (boot?.jobs || []).filter(
    (j) =>
      j.conversation_id === cid &&
      j.kind !== "research" &&
      (j.kind === tab ||
        (tab === "translation" && j.kind.startsWith("translation-")) ||
        (tab === "vocab" && j.kind === "voca-save")),
  );
  const messages = detail?.messages || [];
  const translationFollow = translationFollowOverride ?? settings.translation_auto_scroll ?? true;
  const translationView = translationViewOverride ?? settings.translation_view ?? "history";
  const latestTranslationLine = detail?.lines?.findLast((line) => line.last_state?.endsWith(": done"))?.id
    || detail?.lines?.at(-1)?.id;
  const lastTranslationSource = detail?.lines?.at(-1)?.id;
  useLayoutEffect(() => {
    if (tab !== "translation" || !translationFollow) return;
    if (latestTranslationLine) setLineId(latestTranslationLine);
    const list = translationLinesScroll.current;
    if (list) list.scrollTop = list.scrollHeight;
  }, [tab, cid, translationView, translationFollow, latestTranslationLine, lastTranslationSource]);
  useLayoutEffect(() => {
    if (tab !== "translation" || !translationFollow) return;
    const panel = translationHistoryScroll.current;
    if (panel) panel.scrollTop = panel.scrollHeight;
  }, [tab, translationView, translationFollow, history, boot?.translation_preview]);

  function choose(id) {
    setCid(id);
    cidRef.current = id;
    localStorage.setItem("rtc-conversation", id);
    setDetail(null);
    setFiles([]);
    setSummaryId(null);
    setLineId(null);
    setHistory([]);
    setHiddenTranscript(false);
    setVocabResult([]);
    setResearchFocus(null);
  }
  async function refresh() {
    const data = await api("/bootstrap");
    setBoot(data);
    let selected = cidRef.current;
    if (!data.conversations.some((c) => c.id === selected)) {
      selected = data.conversations[0]?.id || "";
      choose(selected);
    }
    if (selected) {
      const result = await api("/conversations/" + selected);
      if (cidRef.current === selected) setDetail(result);
    }
    for (const job of data.jobs) {
      if (job.kind === "chat" && job.status === "error" && job.tab_recovery && !jobSeen.current.has(job.id)) {
        jobSeen.current.add(job.id);
        const recovered = job.tab_recovery;
        // Do not override a tab the user deliberately selected while waiting.
        if (chatConfigRef.current.selected_tab === recovered.old_key) {
          chatPreparation.current += 1;
          setPreparingChatTab(false);
          setChromeTabs(recovered.tabs);
          const next = { ...chatConfigRef.current, selected_tab: recovered.selected_tab,
            selected_tab_title: recovered.selected_tab_title };
          chatConfigRef.current = next;
          setChatDraft(next);
        }
      }
      if (job.status === "done" && !jobSeen.current.has(job.id)) {
        jobSeen.current.add(job.id);
        if (
          job.conversation_id === cidRef.current &&
          Array.isArray(job.result) &&
          ["vocab", "voca-save"].includes(job.kind)
        )
          setVocabResult(job.result);
      }
    }
  }
  useEffect(() => {
    let live = true,
      timer;
    const tick = async () => {
      try {
        await refresh();
      } catch (e) {
        if (live) setError(e.message);
      }
      if (live) timer = setTimeout(tick, 1000);
    };
    tick();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, []);
  useEffect(() => {
    if (cid) refresh().catch((e) => setError(e.message));
  }, [cid]);
  useEffect(() => {
    if (tab !== "chat" || !boot || !cid || chatTabInitialized.current) return;
    chatTabInitialized.current = true;
    // A saved selection is usable without opening Chrome's tab list. Only a
    // first-time setup with no saved tab discovers/creates a default once.
    if (!chatConfigRef.current.selected_tab) prepareChatTab();
  }, [tab, cid, !!boot]);
  useEffect(() => {
    if (follow && tab === "transcript")
      transcriptEnd.current?.scrollIntoView({ block: "end" });
  }, [detail?.transcript, boot?.capture?.partial, tab]);
  useEffect(() => {
    if (tab === "chat") chatEnd.current?.scrollIntoView({ block: "end" });
  }, [messages.length, busyJob?.kind === "chat" ? busyJob.partial : "", tab]);
  useEffect(() => {
    setHistory([]);
  }, [lineId]);
  useEffect(() => {
    if (!lineId) return;
    let live = true;
    api("/translation/" + lineId + "/history")
      .then((value) => { if (live) setHistory(value); })
      .catch((e) => { if (live) setError(e.message); });
    return () => { live = false; };
  }, [lineId, detail?.lines?.find((l) => l.id === lineId)?.last_state]);
  useEffect(() => {
    if (notice) {
      const timer = setTimeout(() => setNotice(""), 3500);
      return () => clearTimeout(timer);
    }
  }, [notice]);
  async function action(fn) {
    setError("");
    try {
      const value = await fn();
      await refresh();
      return value;
    } catch (e) {
      setError(e.message);
    }
  }
  async function changeSettings(values) {
    const updated = await api("/settings", { method: "PATCH", body: values });
    setBoot((old) => ({ ...old, settings: updated }));
    return updated;
  }
  async function saveTranslationSettings() {
    if (savingTranslation) return;
    setSavingTranslation(true);
    try {
      const values = Object.fromEntries(Object.entries(settings).filter(([key]) => key.startsWith("translation_")));
      await changeSettings({ ...values, translation_view: translationView, translation_auto_scroll: translationFollow });
      setNotice("Đã lưu cài đặt dịch và chế độ hiển thị.");
    } catch (e) { setError(e.message); }
    finally { setSavingTranslation(false); }
  }
  function create() {
    setConversationDialog({ title: "New conversation" });
  }
  function rename() {
    if (current) setConversationDialog({ cid, title: current.title });
  }
  async function exportConversation() {
    if (!cid || transferRef.current) return;
    transferRef.current = true;
    setTransferring("export"); setError("");
    try {
      const response = await api(`/conversations/${cid}/export`, {}, true);
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
      const filename = encoded ? decodeURIComponent(encoded) : disposition.match(/filename="([^"]+)"/)?.[1] || "conversation.rtc-conversation.zip";
      const url = URL.createObjectURL(blob), link = document.createElement("a");
      link.href = url; link.download = filename; document.body.append(link); link.click(); link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      setNotice("Đã export hội thoại. Chép file ZIP sang thiết bị khác rồi chọn Import hội thoại.");
    } catch (e) { setError(e.message); }
    finally { transferRef.current = false; setTransferring(""); }
  }
  async function importConversation(file) {
    if (!file || transferRef.current) return;
    transferRef.current = true;
    setTransferring("import"); setError("");
    try {
      const body = new FormData(); body.append("file", file);
      const item = await post("/conversations/import", body);
      choose(item.id); setQuery(""); setTab("transcript");
      await refresh();
      setNotice("Đã import bản sao hội thoại: " + item.title);
    } catch (e) { setError(e.message); }
    finally { transferRef.current = false; setTransferring(""); }
  }
  async function scanTabs({ ensure = false } = {}) {
    tabLoads.current += 1;
    setLoadingTabs(true);
    try {
      const tabs = await tabLoader.current({ ensure });
      setChromeTabs(tabs);
      return tabs;
    } catch (e) {
      setError(e.message);
      return e.message;
    } finally {
      tabLoads.current -= 1;
      setLoadingTabs(tabLoads.current > 0);
    }
  }
  async function prepareChatTab() {
    const revision = ++chatPreparation.current;
    setPreparingChatTab(true);
    try {
      const tabs = await scanTabs({ ensure: true });
      if (revision !== chatPreparation.current) return;
      if (!Array.isArray(tabs)) return; // scanTabs already displays the connection error.
      const chosen = tabs.find((t) => t.key === chatConfigRef.current.selected_tab) || tabs[0];
      setChatDraft((old) => ({ ...(old || chatConfigRef.current),
        selected_tab: chosen.key, selected_tab_title: chosen.title }));
    } catch (e) { setError(e.message); }
    finally { if (revision === chatPreparation.current) setPreparingChatTab(false); }
  }
  async function saveChatSettings() {
    if (savingChat) return;
    setSavingChat(true);
    try {
      const value = { ...chatConfigRef.current };
      value.prompt ||= boot.prompts.chat?.at(-1) || "";
      value.selected_tab_title = chromeTabs.find((t) => t.key === value.selected_tab)?.title || value.selected_tab_title;
      await changeSettings(savedChatPreferences(value));
      setChatDraft(value);
      setNotice("Đã lưu cài đặt Chat và tab ChatGPT đã chọn");
    } catch (e) { setError(e.message); }
    finally { setSavingChat(false); }
  }
  async function copy(text) {
    try {
      await navigator.clipboard.writeText(text);
      setNotice("Đã copy");
    } catch (e) {
      setError(e.message);
    }
  }
  async function upload(selected) {
    if (!cid) return;
    const target = cid;
    setUploading(true);
    try {
      for (const file of selected) {
        const body = new FormData();
        body.append("file", file);
        const result = await post("/conversations/" + target + "/files", body);
        if (cidRef.current === target) setFiles((old) => [...old, { ...result, type: file.type }]);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setUploading(false);
    }
  }
  async function screen() {
    let stream;
    try {
      stream = await navigator.mediaDevices.getDisplayMedia({
        video: true,
        audio: false,
      });
      const video = document.createElement("video");
      video.srcObject = stream;
      await video.play();
      const canvas = document.createElement("canvas");
      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      canvas.getContext("2d").drawImage(video, 0, 0);
      const blob = await new Promise((resolve) =>
        canvas.toBlob(resolve, "image/png"),
      );
      await upload([new File([blob], "screen.png", { type: "image/png" })]);
    } catch (e) {
      if (e.name !== "NotAllowedError") setError(e.message);
    } finally {
      stream?.getTracks().forEach((t) => t.stop());
    }
  }
  async function submit(kind) {
    if (kind === "chat" && (preparingChatTab || !chatSelectedTab)) return;
    const payload = { kind };
    if (kind === "chat")
      Object.assign(payload, {
        ...chatConfig,
        message,
        files: files.map((f) => f.path),
      });
    if (kind === "summary")
      Object.assign(payload, { new_chat: settings.summary_new_chat });
    if (kind === "vocab")
      Object.assign(payload, {
        message: words,
        new_chat: newChat,
        save_voca: saveVoca,
      });
    await action(async () => {
      await post("/conversations/" + cid + "/jobs", payload);
      if (kind === "chat") {
        setMessage("");
        setFiles([]);
      }
    });
  }
  const download = (kind, mid) =>
    "/api/conversations/" + cid + "/answers/" + mid + "/export?format=" + kind;
  function answerActions(m) {
    return (
      <div className="answer-actions">
        {m.kind === "chat" && <RecopyAnswer key={m.id} onCopy={async () => {
          await post(`/conversations/${cid}/answers/${m.id}/recopy`);
          await refresh();
        }} />}
        {m.kind === "chat" && !m.header.startsWith("Error") && (
          <Button
            icon={GitBranch}
            onClick={() =>
              action(async () => {
                const node = await post(
                  "/conversations/" + cid + "/research/import",
                  { message_id: m.id },
                );
                setResearchFocus({ id: node.id, nonce: Date.now() });
                setTab("research");
              })
            }
          >
            Nghiên cứu theo nhánh
          </Button>
        )}
        <Button icon={Copy} onClick={() => copy(m.content)}>
          Copy
        </Button>
        {["md", "docx", "pdf"].map((f) => (
          <a key={f} href={download(f, m.id)} download>
            {f.toUpperCase()}
          </a>
        ))}
      </div>
    );
  }
  function options(kind) {
    if (kind === "chat")
      return (
        <ChatOptions
          value={chatConfig}
          onChange={(patch) => {
            if ("selected_tab" in patch) {
              chatPreparation.current += 1;
              setPreparingChatTab(false);
              patch = { ...patch, selected_tab_title: chromeTabs.find((t) => t.key === patch.selected_tab)?.title || "" };
            }
            setChatDraft((old) => ({ ...(old || chatConfigRef.current), ...patch }));
          }}
          tabs={chromeTabs}
          scanTabs={prepareChatTab}
          loadingTabs={loadingTabs}
          tabDisabled={preparingChatTab}
          prompts={boot.prompts.chat || []}
          editPrompt={() => setPromptKind("chat")}
          onSave={saveChatSettings}
          saving={savingChat}
          disabled={savingChat}
        />
      );
    return (
      <div className="options">
        <Field label="ChatGPT tab">
          <div className="inline">
            <select
              aria-label={kind + " ChatGPT tab"}
              value={settings["summary_chatgpt_selected_tab"] || ""}
              onChange={(e) =>
                action(() =>
                  changeSettings({
                    ["summary_chatgpt_selected_tab"]: e.target.value,
                  }),
                )
              }
            >
              <option value="">Chọn tab Chrome…</option>
              {chromeTabs.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.title}
                </option>
              ))}
              {!chromeTabs.some(
                (t) => t.key === settings["summary_chatgpt_selected_tab"],
              ) &&
                settings["summary_chatgpt_selected_tab"] && (
                  <option value={settings["summary_chatgpt_selected_tab"]}>
                    Tab đã lưu — Refresh để xem
                  </option>
                )}
            </select>
            <Button
              icon={RefreshCw}
              title="Refresh Chrome tabs"
              onClick={scanTabs}
              disabled={loadingTabs}
            />
          </div>
        </Field>
        <Field label="Prompt">
          <div className="inline">
            <select
              value={
                settings[kind + "_prompt"] || boot.prompts[kind]?.at(-1) || ""
              }
              onChange={(e) =>
                action(() =>
                  changeSettings({ [kind + "_prompt"]: e.target.value }),
                )
              }
            >
              {boot.prompts[kind]?.map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
            <Button
              icon={Pencil}
              title="Edit prompt"
              onClick={() => setPromptKind(kind)}
            />
          </div>
        </Field>
        <Field label="Power">
          <select
            value={settings[kind + "_chatgpt_power"]}
            onChange={(e) =>
              action(() =>
                changeSettings({ [kind + "_chatgpt_power"]: +e.target.value }),
              )
            }
          >
            {["Giữ nguyên", "Instant", "Medium", "High"].map((p, i) => (
              <option key={p} value={i - 1}>
                {p}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Số dòng transcript (0 = tất cả)">
          <input
            type="number"
            min="0"
            value={settings[kind + "_max_lines"] ?? 300}
            onChange={(e) =>
              action(() =>
                changeSettings({
                  [kind + "_max_lines"]: Math.max(0, +e.target.value),
                }),
              )
            }
          />
        </Field>
        <>
          <Toggle
            label="Đính kèm transcript"
            checked={settings.summary_attach_transcript}
            onChange={(v) =>
              action(() => changeSettings({ summary_attach_transcript: v }))
            }
          />
          <Toggle
            label="New ChatGPT chat"
            checked={settings.summary_new_chat}
            onChange={(v) =>
              action(() => changeSettings({ summary_new_chat: v }))
            }
          />
        </>
      </div>
    );
  }
  const savedVocab = messages
    .filter((m) => m.kind === "vocab" && m.role === "bot")
    .flatMap((m) => {
      try {
        return JSON.parse(m.content);
      } catch {
        return [];
      }
    });
  const displayWords = vocabResult.length ? vocabResult : savedVocab;

  if (!boot)
    return (
      <div className="loading">
        <AudioLines size={38} />
        <h2>Real-time Caption</h2>
        <p>{error || "Đang kết nối…"}</p>
        <Button onClick={() => location.reload()}>Thử lại</Button>
      </div>
    );
  return (
    <div
      className={
        "shell compact-workspace " +
        (sidebarHidden ? "sidebar-hidden " : "") +
        (readingFocus ? "focus-reading" : "")
      }
    >
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-icon">
            <AudioLines size={22} />
          </div>
          <div>
            Real-time Caption<small>YOUR LEARNING WORKSPACE</small>
          </div>
        </div>
        <Button className="primary new" icon={Plus} onClick={create}>
          New conversation
        </Button>
        <div className="conversation-transfer">
          <Button icon={Download} onClick={exportConversation} disabled={!cid || !!transferring}
            aria-label="Export hội thoại" title="Export toàn bộ hội thoại thành ZIP">
            {transferring === "export" ? "Đang export…" : "Export"}
          </Button>
          <Button icon={Upload} onClick={() => importInput.current.click()} disabled={!!transferring}
            aria-label="Import hội thoại" title="Import hội thoại từ file ZIP">
            {transferring === "import" ? "Đang import…" : "Import"}
          </Button>
          <input ref={importInput} hidden type="file" accept=".zip" aria-label="Chọn file hội thoại để import"
            onChange={(e) => { importConversation(e.target.files[0]); e.target.value = ""; }} />
        </div>
        <div className="search">
          <Search size={15} />
          <input
            aria-label="Search conversations"
            placeholder="Tìm cuộc hội thoại…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="section-label">
          CONVERSATIONS <span>{boot.conversations.length}</span>
        </div>
        <div className="conversation-list">
          {boot.conversations
            .filter((c) => c.title.toLowerCase().includes(query.toLowerCase()))
            .map((c) => (
              <button
                className={"conversation " + (c.id === cid ? "selected" : "")}
                key={c.id}
                onClick={() => choose(c.id)}
              >
                <MessageSquare size={17} />
                <div>
                  <strong>{c.title}</strong>
                  <small>
                    {stamp(c.updated_at)}{" "}
                    {c.status === "recording" && (
                      <span className="live-dot">● LIVE</span>
                    )}
                  </small>
                </div>
                <ChevronRight size={14} />
              </button>
            ))}
        </div>
        <div className="sidebar-bottom">
          <div className="sync">
            <span /> Dùng chung dữ liệu với desktop
          </div>
          <Button icon={Settings2} onClick={() => setSettingsOpen(true)}>
            Settings
          </Button>
        </div>
      </aside>
      <main>
        <div className="workspace-top">
          <header>
            <Button
              icon={
                sidebarHidden || readingFocus ? PanelLeftOpen : PanelLeftClose
              }
              title={
                sidebarHidden || readingFocus
                  ? "Hiện danh sách hội thoại"
                  : "Ẩn danh sách hội thoại"
              }
              aria-label="Ẩn/hiện danh sách hội thoại"
              aria-expanded={!sidebarHidden && !readingFocus}
              onClick={() => {
                if (readingFocus) {
                  setReadingFocus(false);
                  setSidebarHidden(false);
                } else setSidebarHidden((v) => !v);
              }}
            />
            <div className="conversation-title">
              <h1 title={current?.title}>
                {current?.title || "Bắt đầu cuộc hội thoại mới"}
              </h1>
            </div>
            <div className="header-actions">
              <Button
                icon={readingFocus ? Minimize2 : Maximize2}
                className="reading-focus-toggle"
                aria-pressed={readingFocus}
                title={readingFocus ? "Thoát tập trung" : "Tập trung đọc"}
                onClick={() => setReadingFocus((v) => !v)}
              >
                {readingFocus ? "Thoát tập trung" : "Tập trung đọc"}
              </Button>
              <Button
                icon={Pencil}
                title="Rename conversation"
                onClick={rename}
                disabled={!cid}
              />
              <Button
                icon={FolderOpen}
                title="Open conversation folder"
                onClick={() =>
                  action(() => post("/conversations/" + cid + "/open-folder"))
                }
                disabled={!cid}
              />
              <Button
                icon={PanelTop}
                title="Caption overlay"
                onClick={() => action(() => post("/overlay"))}
              >
                Caption overlay
              </Button>
            </div>
          </header>
          <div className="capture-bar">
            <div className={"source-icon " + (active ? "recording" : "")}>
              <AudioLines size={23} />
            </div>
            <div>
              <select className="capture-source" aria-label="Nguồn transcript" value={captureSource} disabled={active}
                onChange={(e) => action(() => api("/settings", { method: "PATCH", body: { web_capture_source: e.target.value } }))}>
                <option value="windows">Windows Live Captions</option>
                <option value="chatgpt">ChatGPT · Micro</option>
              </select>
              <small title={captureSource === "chatgpt" && boot.capture.warning || boot.capture.status}>
                {captureSource === "chatgpt" && boot.capture.warning || boot.capture.status}
              </small>
            </div>
            <span className="source-tag">{captureSource === "chatgpt" ? `${settings.chatgpt_dictation_interval || 15}s / đoạn` : "Win + Ctrl + L"}</span>
            <Button
              className={active ? "stop" : "primary"}
              icon={active ? Pause : Play}
              disabled={!cid}
              onClick={() =>
                action(() =>
                  post(
                    active
                      ? "/capture/pause"
                      : "/conversations/" + cid + "/capture",
                  ),
                )
              }
            >
              {active ? "Pause" : "Start / Continue"}
            </Button>
          </div>
        </div>
        <nav>
          {tabs.map(([key, Icon, label]) => (
            <button
              key={key}
              className={key === tab ? "active" : ""}
              onClick={() => setTab(key)}
            >
              <Icon size={17} />
              {label}
            </button>
          ))}
        </nav>
        {busyJob?.kind === "research" && tab !== "research" && (
          <div className="research-notice">
            Đang trả lời một nhánh nghiên cứu.{" "}
            <button
              onClick={() => {
                if (cid !== busyJob.conversation_id)
                  choose(busyJob.conversation_id);
                setResearchFocus({ id: busyJob.node_id, nonce: Date.now() });
                setTab("research");
              }}
            >
              Xem tiến độ / hủy
            </button>
          </div>
        )}
        {visibleError && (
          <div role="alert" className="alert">
            {visibleError}
            <button onClick={() => { setError(""); setDismissedCaptureError(captureError); }} aria-label="Close error">
              <X size={16} />
            </button>
          </div>
        )}
        {notice && (
          <div className="toast">
            <Check size={16} />
            {notice}
          </div>
        )}
        {!cid ? (
          <Empty
            title="Không có cuộc hội thoại"
            text="Tạo cuộc hội thoại để ghi transcript, hỏi ChatGPT và lưu kiến thức."
          />
        ) : (
          <div className="workspace">
            {tab === "research" && (
              <ResearchTab
                key={cid}
                cid={cid}
                api={api}
                post={post}
                Md={Md}
                settings={settings}
                prompts={boot.prompts.chat || []}
                refreshPrompts={refresh}
                chatAttach={chatConfig.attach_transcript}
                chromeTabs={chromeTabs}
                scanTabs={scanTabs}
                loadingTabs={loadingTabs}
                focus={researchFocus}
                goChat={() => setTab("chat")}
                readingFocus={readingFocus}
              />
            )}
            {tab === "transcript" && (
              <>
                <div className="panel-toolbar">
                  <div>
                    <h2>Live transcript</h2>
                    <span className="muted">
                      Nội dung tự động lưu vào cuộc hội thoại
                    </span>
                  </div>
                  <div className="inline">
                    <Toggle
                      label="Auto-scroll"
                      checked={follow}
                      onChange={setFollow}
                    />
                    <Button
                      icon={Copy}
                      onClick={() => copy(detail?.transcript || "")}
                    >
                      Copy
                    </Button>
                    <Button onClick={() => setHiddenTranscript((v) => !v)}>
                      {hiddenTranscript ? "Hiện lại" : "Clear view"}
                    </Button>
                    <input
                      className="line-count"
                      type="number"
                      min="0"
                      title="Download last N lines (0 = all)"
                      value={lastCount}
                      onChange={(e) =>
                        setLastCount(Math.max(0, +e.target.value))
                      }
                    />
                    <a
                      className="button"
                      href={
                        "/api/conversations/" +
                        cid +
                        "/transcript?count=" +
                        lastCount
                      }
                      download
                    >
                      <Download size={16} />
                      Download
                    </a>
                  </div>
                </div>
                <div className="transcript scroll">
                  {!detail?.transcript && (
                    <Empty
                      icon={AudioLines}
                      title="Sẵn sàng lắng nghe"
                      text={captureSource === "chatgpt" ? "Bấm Start để mở tab ChatGPT thu micro. Văn bản được thêm sau mỗi đoạn ghi âm; cho phép micro trong Chrome nếu được hỏi." : "Bấm Start, mở Windows Live Captions rồi phát âm thanh có lời nói."}
                    />
                  )}
                  {!hiddenTranscript && <pre>{detail?.transcript}</pre>}
                  {boot.capture.conversation_id === cid && (
                    <p className="partial">{boot.capture.partial}</p>
                  )}
                  <div ref={transcriptEnd} />
                </div>
              </>
            )}
            {tab === "chat" && (
              <>
                <details className="chat-settings">
                  <summary>
                    <Settings2 size={14} />
                    <strong>Tùy chọn Chat</strong>
                    <span
                      title={
                        chromeTabs.find(
                          (t) => t.key === chatSelectedTab,
                        )?.title
                      }
                    >
                      {chromeTabs.find(
                        (t) => t.key === chatSelectedTab,
                      )?.title || chatConfig.selected_tab_title ||
                        (preparingChatTab
                          ? "Đang chọn tab ChatGPT…"
                          : chatSelectedTab ? "Tab ChatGPT đã lưu" : "Chọn tab ChatGPT")}
                    </span>
                    <span
                      title={chatConfig.prompt || boot.prompts.chat?.at(-1)}
                    >
                      {chatConfig.prompt || boot.prompts.chat?.at(-1)}
                    </span>
                    {chatConfig.new_chat && <b className="new-chat-indicator">Chat mới</b>}
                    <ChevronRight size={14} className="settings-chevron" />
                  </summary>
                  {options("chat")}
                </details>
                <div className="chat-scroll scroll" ref={chatScroll}>
                  {!messages.filter((m) => m.kind === "chat").length && (
                    <Empty
                      icon={MessageSquare}
                      title="Hỏi sâu hơn về nội dung"
                      text="Đính kèm transcript, ảnh hoặc tài liệu để ChatGPT trả lời theo ngữ cảnh."
                    />
                  )}
                  {messages
                    .filter((m) => m.kind === "chat")
                    .map((m) => (
                      <article className={"message " + m.role} key={m.id}>
                        <div className="message-meta">
                          {m.role === "bot" ? "ChatGPT" : "Bạn"}{" "}
                          <time>{stamp(m.created_at)}</time>
                        </div>
                        {m.role === "bot" ? (
                          m.content ? <Md text={m.markdown} /> : <p className="muted">Chưa lấy được nội dung. Bấm “Lấy lại câu trả lời” để copy lại từ ChatGPT.</p>
                        ) : (
                          <p className="user-text">{m.content}</p>
                        )}
                        <Attachments message={m} cid={cid} />
                        {m.role === "bot" && <ResponseSource response={m} onSave={async (values) => {
                          await api(`/conversations/${cid}/answers/${m.id}/source`, { method: "PUT", body: values });
                          await refresh();
                        }} />}
                        {m.role === "bot" && answerActions(m)}
                        {m.role === "user" && (
                          <Button onClick={() => setMessage(m.content)}>
                            Dùng lại câu hỏi
                          </Button>
                        )}
                      </article>
                    ))}
                  {jobs
                    .filter((j) => j.status === "working" && j.partial)
                    .map((j) => (
                      <article key={j.id} className="message bot preview">
                        <pre>{j.partial}</pre>
                      </article>
                    ))}
                  <div ref={chatEnd} />
                </div>
                <div className="composer chat-composer">
                  <AttachmentList files={files} cid={cid}
                    onRemove={(file) => setFiles((old) => old.filter((item) => item.path !== file.path))} />
                  <textarea
                    ref={messageInput}
                    rows={1}
                    aria-label="Câu hỏi Chat"
                    placeholder="Nhập câu hỏi… Enter để gửi, Shift + Enter xuống dòng"
                    value={message}
                    onChange={(e) => setMessage(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && !e.shiftKey) {
                        e.preventDefault();
                        if (message.trim() && !busyJob && !uploading)
                          submit("chat");
                      }
                    }}
                    onPaste={(e) => {
                      const images = [...e.clipboardData.files].filter((f) =>
                        f.type.startsWith("image/"),
                      );
                      if (images.length) {
                        e.preventDefault();
                        upload(images);
                      }
                    }}
                  />
                  <div className="composer-actions">
                    <div className="inline">
                      <Button
                        icon={Paperclip}
                        onClick={() => fileInput.current.click()}
                        disabled={uploading}
                      >
                        Attach files
                      </Button>
                      <Button
                        icon={Camera}
                        onClick={screen}
                        disabled={uploading}
                      >
                        Screenshot
                      </Button>
                      <Button
                        icon={ArrowUp}
                        title="Top"
                        onClick={() => chatScroll.current.scrollTo({ top: 0 })}
                      />
                      <Button
                        icon={ArrowDown}
                        title="Bottom"
                        onClick={() => chatEnd.current.scrollIntoView()}
                      />
                      <input
                        hidden
                        ref={fileInput}
                        type="file"
                        multiple
                        onChange={(e) => {
                          upload([...e.target.files]);
                          e.target.value = "";
                        }}
                      />
                    </div>
                    <Button
                      className="primary"
                      icon={Send}
                      disabled={!message.trim() || !!busyJob || uploading || preparingChatTab || !chatSelectedTab}
                      onClick={() => submit("chat")}
                    >
                      {uploading ? "Uploading…" : "Send"}
                    </Button>
                  </div>
                </div>
              </>
            )}
            {tab === "summary" && (
              <>
                {options("summary")}
                <div className="panel-toolbar">
                  <Field label="Lịch sử tóm tắt">
                    <select
                      value={
                        summaryId ||
                        messages
                          .filter(
                            (m) => m.kind === "summary" && m.role === "bot",
                          )
                          .at(-1)?.id ||
                        ""
                      }
                      onChange={(e) => setSummaryId(+e.target.value)}
                    >
                      <option value="">Chọn bản tóm tắt</option>
                      {messages
                        .filter((m) => m.kind === "summary" && m.role === "bot")
                        .map((m) => (
                          <option key={m.id} value={m.id}>
                            {stamp(m.created_at)} · #{m.id}
                          </option>
                        ))}
                    </select>
                  </Field>
                  <Button
                    className="primary"
                    icon={BookOpen}
                    onClick={() => submit("summary")}
                    disabled={!!busyJob}
                  >
                    Summarize
                  </Button>
                </div>
                <div className="summary scroll">
                  {(() => {
                    const m =
                      messages.find((m) => m.id === summaryId) ||
                      messages
                        .filter((m) => m.kind === "summary" && m.role === "bot")
                        .at(-1);
                    return m ? (
                      <>
                        <Md text={m.markdown} />
                        {answerActions(m)}
                      </>
                    ) : (
                      <Empty
                        icon={BookOpen}
                        title="Kiến thức sau mỗi buổi học"
                        text="Chọn prompt để tóm tắt, giải thích hoặc tạo bài tập từ transcript."
                      />
                    );
                  })()}
                  {jobs
                    .filter((j) => j.status === "working")
                    .map((j) => (
                      <pre className="partial" key={j.id}>
                        {j.partial}
                      </pre>
                    ))}
                </div>
              </>
            )}
            {tab === "translation" && (
              <>
                <div className="panel-toolbar">
                  <div>
                    <h2>Transcript translation</h2>
                    <span className="muted">
                      {boot.engines[settings.translation_engine]} ·{" "}
                      {settings.translation_source} →{" "}
                      {settings.translation_target}
                    </span>
                  </div>
                  <div className="inline">
                    <Button
                      icon={BookOpen}
                      aria-pressed={translationView === "parallel"}
                      disabled={translationViewOverride !== null || savingTranslation}
                      onClick={async () => {
                        const next = translationView === "parallel" ? "history" : "parallel";
                        setTranslationViewOverride(next);
                        try { await changeSettings({ translation_view: next }); }
                        catch (e) { setError(e.message); }
                        finally { setTranslationViewOverride(null); }
                      }}
                    >
                      {translationView === "parallel" ? "Theo từng câu" : "Đọc song song"}
                    </Button>
                    <Toggle
                      label="Auto translate"
                      checked={settings.translation_enabled}
                      onChange={(v) =>
                        action(() => changeSettings({ translation_enabled: v }))
                      }
                    />
                    <Toggle
                      label="Tự cuộn xuống cuối"
                      checked={translationFollow}
                      onChange={async (v) => {
                        setTranslationFollowOverride(v);
                        try { await changeSettings({ translation_auto_scroll: v }); }
                        catch (e) { setError(e.message); }
                        finally { setTranslationFollowOverride(null); }
                      }}
                    />
                    {settings.translation_engine === "chrome" && <Toggle
                      label="Dịch ngay khi đang nói"
                      checked={settings.translation_partial}
                      onChange={(v) => action(() => changeSettings({ translation_partial: v }))}
                    />}
                    <Button
                      icon={Settings2}
                      onClick={() => setSettingsOpen(true)}
                    />
                    <Button
                      onClick={() =>
                        action(() =>
                          post("/conversations/" + cid + "/translate"),
                        )
                      }
                    >
                      Translate missing
                    </Button>
                    <Button
                      icon={Square}
                      onClick={() => action(() => post("/translation/stop"))}
                    >
                      Stop
                    </Button>
                    <Button icon={Save} disabled={savingTranslation || translationViewOverride !== null || translationFollowOverride !== null}
                      onClick={saveTranslationSettings}>
                      {savingTranslation ? "Đang lưu…" : "Lưu cài đặt"}
                    </Button>
                  </div>
                </div>
                {translationView === "parallel" ? (
                  <TranslationReading cid={cid} lines={detail?.lines || []} reading={detail?.translation_reading}
                    capture={boot.capture} follow={translationFollow}
                    source={settings.translation_source} target={settings.translation_target}
                    showRealtime={settings.translation_engine === "chrome" && settings.translation_partial} />
                ) : <div className="translation-grid">
                  <div className="translation-lines scroll" ref={translationLinesScroll}>
                    {!detail?.lines.length && (
                      <Empty
                        icon={Languages}
                        title="Chưa có câu để dịch"
                        text="Ghi transcript hoặc chọn một cuộc hội thoại có sẵn."
                      />
                    )}
                    {detail?.lines.map((l) => (
                      <button
                        key={l.id}
                        className={
                          "translation-line " +
                          (l.id === lineId ? "selected" : "")
                        }
                        onClick={() => setLineId(l.id)}
                      >
                        <time>{l.stamp}</time>
                        <p>{l.source_text}</p>
                        <small>{l.last_state || "Chưa dịch"}</small>
                      </button>
                    ))}
                  </div>
                  <div className="translation-history scroll" ref={translationHistoryScroll}>
                    <h3>Lịch sử bản dịch</h3>
                    {lineId ? (
                      <>
                        <Button
                          className="primary"
                          onClick={() =>
                            action(() =>
                              post("/conversations/" + cid + "/translate", {
                                ids: [lineId],
                                force: true,
                              }),
                            )
                          }
                        >
                          Translate / Retry selected
                        </Button>
                        {history.map((h) => (
                          <article key={h.id} className="translation-attempt">
                            <small>
                              {h.engine} · {h.source_lang} → {h.target_lang} ·{" "}
                              {h.status} · {stamp(h.created_at)}
                            </small>
                            {h.batch_size > 1 && (
                              <details>
                                <summary>Nhóm {h.batch_size} câu</summary>
                                <pre>{h.batch_source}</pre>
                              </details>
                            )}
                            <p>
                              {h.translated_text || h.error || "Đang xử lý…"}
                            </p>
                            {h.translated_text && (
                              <Button
                                icon={Copy}
                                onClick={() => copy(h.translated_text)}
                              >
                                Copy
                              </Button>
                            )}
                          </article>
                        ))}
                      </>
                    ) : (
                      <p className="muted">Chọn một câu để xem và dịch lại.</p>
                    )}
                    {settings.translation_engine === "chrome" && settings.translation_partial &&
                      boot.capture.conversation_id === cid && boot.translation_preview && (
                      <div className="partial" aria-label="Bản dịch tạm">
                        <small>Bản dịch tạm · Câu chưa kết thúc</small>
                        <div>{boot.translation_preview}</div>
                      </div>
                    )}
                  </div>
                </div>}
              </>
            )}
            {tab === "vocab" && (
              <>
                <div className="panel-toolbar">
                  <div>
                    <h2>Your vocabulary</h2>
                    <span className="muted">
                      Từ mới, nghĩa và ngữ cảnh trong transcript
                    </span>
                  </div>
                  <div className="inline">
                    <Toggle
                      label="Tự lưu lên Voca"
                      checked={saveVoca}
                      onChange={setSaveVoca}
                    />
                    <Button
                      icon={Settings2}
                      onClick={() => setSettingsOpen(true)}
                    />
                    <a
                      className="button"
                      href="https://voca-zeta-five.vercel.app/"
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open Voca ↗
                    </a>
                  </div>
                </div>
                <div className="vocab-input">
                  <textarea
                    placeholder="Mỗi từ/cụm từ một dòng, hoặc ngăn cách bằng dấu phẩy…"
                    value={words}
                    onChange={(e) => setWords(e.target.value)}
                  />
                  <div className="inline">
                    <Toggle
                      label="New ChatGPT chat"
                      checked={newChat}
                      onChange={setNewChat}
                    />
                    <Field label="Power">
                      <select
                        value={settings.vocab_chatgpt_power}
                        onChange={(e) =>
                          action(() =>
                            changeSettings({
                              vocab_chatgpt_power: +e.target.value,
                            }),
                          )
                        }
                      >
                        {["Giữ nguyên", "Instant", "Medium", "High"].map(
                          (p, i) => (
                            <option key={p} value={i - 1}>
                              {p}
                            </option>
                          ),
                        )}
                      </select>
                    </Field>
                    <Button
                      className="primary"
                      icon={Languages}
                      onClick={() => submit("vocab")}
                      disabled={!words.trim() || !!busyJob}
                    >
                      Translate words
                    </Button>
                    <Button
                      onClick={() =>
                        action(() =>
                          post("/conversations/" + cid + "/jobs", {
                            kind: "voca-save",
                            words: displayWords,
                          }),
                        )
                      }
                      disabled={!displayWords.length || !!busyJob}
                    >
                      Save to Voca
                    </Button>
                  </div>
                </div>
                <div className="vocab-cards scroll">
                  {messages
                    .filter((m) => m.kind === "vocab" && m.role === "bot")
                    .map((m) => (
                      <div className="vocab-card" key={"export-" + m.id}>
                        <small>
                          Vocabulary · {stamp(m.created_at)} · #{m.id}
                        </small>
                        <div className="answer-actions">
                          {["md", "docx", "pdf"].map((format) => (
                            <a
                              key={format}
                              href={download(format, m.id)}
                              download
                            >
                              {format.toUpperCase()}
                            </a>
                          ))}
                        </div>
                      </div>
                    ))}
                  {!displayWords.length && (
                    <Empty
                      icon={BookOpen}
                      title="Ghi nhớ từ mới trong ngữ cảnh"
                      text="Nhập từ cần học. App tìm câu chứa từ trong transcript và nhờ ChatGPT giải thích."
                    />
                  )}
                  {displayWords.map((w, i) => (
                    <article className="vocab-card" key={i}>
                      <div>
                        <h3>{w.word}</h3>
                        <small>
                          {w.phonetic} · {w.part_of_speech}
                        </small>
                      </div>
                      <strong>{w.translation}</strong>
                      <p>{w.meaning}</p>
                      <blockquote>
                        {w.sentence}
                        <small>{w.context_translation}</small>
                      </blockquote>
                      <small>
                        {w.time} {w.voca_status && "· Voca: " + w.voca_status}{" "}
                        {w.voca_error}
                      </small>
                    </article>
                  ))}
                </div>
              </>
            )}
            {!!jobs.length && (
              <div className="job-list">
                {jobs.slice(-3).map((j) => (
                  <div className={"job " + j.status} key={j.id}>
                    <span className="job-dot" />
                    <div>
                      <strong>
                        {j.kind} · {j.status}
                      </strong>
                      <small>
                        {j.error || j.detail}
                        {j.status === "done" &&
                        j.kind.startsWith("translation-")
                          ? ": " + j.result
                          : ""}
                      </small>
                    </div>
                    {["working", "queued"].includes(j.status) ? (
                      <Button
                        onClick={() =>
                          action(() => post("/jobs/" + j.id + "/cancel"))
                        }
                      >
                        Cancel
                      </Button>
                    ) : ["error", "cancelled"].includes(j.status) ? (
                      <Button
                        disabled={!!busyJob || (j.error_code === "chatgpt_tab_closed" && !chatSelectedTab)}
                        onClick={() =>
                          action(() => post("/jobs/" + j.id + "/retry",
                            j.error_code === "chatgpt_tab_closed" ? { selected_tab: chatConfigRef.current.selected_tab } : undefined))
                        }
                      >
                        Retry
                      </Button>
                    ) : (
                      <Check size={16} />
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        <footer>
          <span>
            <span className="live-dot">●</span> Local workspace
          </span>
          <span>{captureLabel} · Chrome 9222</span>
          <span>{detail?.lines.length || 0} transcript lines</span>
        </footer>
      </main>
      {conversationDialog && <ConversationNameModal
        initialTitle={conversationDialog.title} rename={!!conversationDialog.cid}
        close={() => setConversationDialog(null)}
        save={async (title) => {
          if (conversationDialog.cid) {
            await api("/conversations/" + conversationDialog.cid, { method: "PATCH", body: { title } });
          } else {
            const item = await post("/conversations", { title });
            choose(item.id);
          }
          refresh().catch((e) => setError(e.message));
        }} />}
      {settingsOpen && (
        <SettingsModal
          settings={settings}
          boot={boot}
          close={() => setSettingsOpen(false)}
          save={(values) =>
            action(async () => {
              await changeSettings(values);
              setSettingsOpen(false);
              setNotice("Đã lưu cài đặt");
            })
          }
          run={(kind) =>
            action(() => post("/conversations/" + cid + "/jobs", { kind }))
          }
          canRun={!!cid && !busyJob}
        />
      )}
      {promptKind && (
        <PromptModal
          api={api}
          post={post}
          kind={promptKind}
          selected={promptKind === "chat" ? chatConfig.prompt : settings[promptKind + "_prompt"]}
          close={() => setPromptKind(null)}
          done={(name) =>
            action(async () => {
              if (promptKind === "chat") setChatDraft((old) => ({ ...old, prompt: name }));
              else await changeSettings({ [promptKind + "_prompt"]: name });
              setPromptKind(null);
            })
          }
        />
      )}
    </div>
  );
}

function Attachments({ message, cid }) {
  const files = [
    ...(message.files || []),
    ...(message.attachment ? [message.attachment] : []),
  ];
  return <AttachmentList cid={cid} className="attachments"
    files={[...new Set(files)].map((path) => ({ path, name: path.split(/[\\/]/).at(-1) }))} />;
}

function SettingsModal({ settings, boot, close, save, run, canRun }) {
  const baseline = useRef(settings);
  const [draft, setDraft] = useState({ ...settings }),
    [collections, setCollections] = useState([]),
    [error, setError] = useState("");
  const set = (key, value) => setDraft((old) => ({ ...old, [key]: value }));
  const input = (key, label, type = "text") => (
    <Field label={label} key={key}>
      <input
        type={type}
        value={draft[key] ?? ""}
        onChange={(e) =>
          set(key, type === "number" ? +e.target.value : e.target.value)
        }
      />
    </Field>
  );
  return (
    <div className="modal-backdrop">
      <section className="modal">
        <div className="modal-head">
          <div>
            <span className="eyebrow">SHARED PREFERENCES</span>
            <h2>Settings</h2>
          </div>
          <Button icon={X} onClick={close} aria-label="Close settings" />
        </div>
        <div className="modal-body">
          <h3>Giao diện web</h3>
          <Field label="Chế độ hiển thị">
            <select aria-label="Chế độ hiển thị" value={draft.web_theme || "light"} onChange={(e) => set("web_theme", e.target.value)}>
              <option value="light">Sáng</option>
              <option value="dark">Tối</option>
            </select>
          </Field>
          <h3>Nguồn transcript</h3>
          <Field label="Chọn một nguồn">
            <select aria-label="Nguồn transcript trong Settings" value={draft.web_capture_source || "windows"}
              disabled={boot.capture.active} onChange={(e) => set("web_capture_source", e.target.value)}>
              <option value="windows">Windows Live Captions</option>
              <option value="chatgpt">ChatGPT · Micro (Dictate)</option>
            </select>
          </Field>
          {boot.capture.active && <p className="muted">Bấm Pause và chờ chốt transcript trước khi đổi cấu hình thu âm.</p>}
          {draft.web_capture_source === "chatgpt" ? <section className="livecaptions-settings">
            <h3>Thu âm bằng ChatGPT</h3>
            <Field label="Khoảng lấy văn bản (giây)">
              <input type="number" min="5" max="120" step="1" aria-label="Khoảng lấy văn bản (giây)"
                disabled={boot.capture.active} value={draft.chatgpt_dictation_interval ?? 15}
                onChange={(e) => set("chatgpt_dictation_interval", +e.target.value)} />
            </Field>
            <label className="inline"><input type="checkbox" disabled={boot.capture.active}
              checked={draft.chatgpt_dictation_overlap !== false}
              onChange={(e) => set("chatgpt_dictation_overlap", e.target.checked)} />Luân phiên 2 tab để giảm khoảng trống</label>
            <p>ChatGPT dùng micro được chọn trong Chrome và xử lý âm thanh trên máy chủ. Nguồn này không tự thu âm thanh hệ thống như Windows Live Captions.</p>
            <p>Văn bản được thêm sau khi chốt mỗi đoạn và chờ ChatGPT chuyển thành chữ. Khi luân phiên, tab mới bắt đầu nghe trước khi tab cũ chốt; có thể trùng vài từ ở chỗ chuyển. Tối đa 2 tab thu âm.</p>
            <p>Các tab thu âm không nhận câu hỏi từ app và tự đóng sau khi lấy văn bản. Pause sẽ chốt đoạn cuối trước khi dừng.</p>
          </section> : <LiveCaptionsSettings api={api} warning={boot.capture.warning} />}
          <h3>Translation</h3>
          <div className="form-grid">
            <Field label="Engine">
              <select
                value={draft.translation_engine}
                onChange={(e) => set("translation_engine", e.target.value)}
              >
                {Object.entries(boot.engines).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </Field>
            {["source", "target"].map((k) => (
              <Field
                key={k}
                label={k === "source" ? "Ngôn ngữ nguồn" : "Ngôn ngữ đích"}
              >
                <select
                  value={draft["translation_" + k]}
                  onChange={(e) => set("translation_" + k, e.target.value)}
                >
                  {Object.entries(boot.languages).map(([k, v]) => (
                    <option key={k} value={k}>
                      {v}
                    </option>
                  ))}
                </select>
              </Field>
            ))}
            {input("translation_batch_size", "Số câu mỗi nhóm", "number")}
            {input(
              "translation_batch_timeout",
              "Thời gian chờ nhóm (giây)",
              "number",
            )}
            <Field label="Offline compute">
              <select
                value={draft.translation_device}
                onChange={(e) => set("translation_device", e.target.value)}
              >
                {["cpu", "cuda", "auto"].map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </select>
            </Field>
          </div>
          {draft.translation_engine === "chrome" && <Toggle
            label="Dịch ngay khi đang nói"
            checked={draft.translation_partial}
            onChange={(v) => set("translation_partial", v)}
          />}
          {draft.translation_engine === "chrome" && <ChromeTranslatorSettings
            source={draft.translation_source} target={draft.translation_target} />}
          <div className="form-grid">
            {input("translation_azure_key", "Azure API key", "password")}
            {input("translation_azure_region", "Azure region")}
            {input("translation_google_key", "Google API key", "password")}
            {input("translation_gemini_key", "Gemini API key", "password")}
            {input("translation_gemini_model", "Gemini model")}
          </div>
          <Field label="Gemini translation prompt">
            <textarea
              rows="4"
              value={draft.translation_gemini_prompt}
              onChange={(e) => set("translation_gemini_prompt", e.target.value)}
            />
          </Field>
          <div className="inline">
            <Button disabled={!canRun} onClick={() => run("translation-test")}>
              Test saved configuration
            </Button>
            <Button
              disabled={!canRun}
              onClick={() => run("translation-download")}
            >
              Download VinAI model
            </Button>
          </div>
          <h3>Voca</h3>
          <div className="form-grid">
            {input("voca_api_key", "Voca API key", "password")}
            <Field label="Collection">
              <div className="inline">
                <input
                  list="collections"
                  value={draft.voca_collection_id}
                  onChange={(e) => set("voca_collection_id", e.target.value)}
                  placeholder="Default collection"
                />
                <datalist id="collections">
                  {collections.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </datalist>
                <Button
                  icon={RefreshCw}
                  title="Load collections using saved key"
                  onClick={async () => {
                    try {
                      const result = await api("/voca/collections");
                      setCollections(
                        Array.isArray(result)
                          ? result
                          : result.collections || [],
                      );
                    } catch (e) {
                      setError(e.message);
                    }
                  }}
                />
              </div>
            </Field>
          </div>
          <h3>Caption overlay</h3>
          <div className="form-grid">
            {input("overlay_font_size", "Cỡ chữ", "number")}
            {input("overlay_lines", "Số dòng", "number")}
            {input("overlay_alpha", "Độ đậm nền (0.1–1)", "number")}
          </div>
          <Toggle
            label="Nền trong suốt"
            checked={draft.overlay_transparent}
            onChange={(v) => set("overlay_transparent", v)}
          />
          <Toggle
            label="Click-through"
            checked={draft.overlay_click_through}
            onChange={(v) => set("overlay_click_through", v)}
          />
          {error && <p className="alert">{error}</p>}
        </div>
        <div className="modal-foot">
          <span>Cài đặt dùng chung với native app</span>
          <Button
            className="primary"
            onClick={() =>
              save(
                Object.fromEntries(
                  Object.entries(draft).filter(
                    ([k, v]) => v !== baseline.current[k],
                  ),
                ),
              )
            }
          >
            Save settings
          </Button>
        </div>
      </section>
    </div>
  );
}

createRoot(document.getElementById("root")).render(<App />);

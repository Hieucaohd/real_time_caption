import React, { useEffect, useRef, useState } from "react";
import { Camera, GitBranch, Paperclip, Plus, Save, Send, X } from "lucide-react";
import ChatOptions from "./ChatOptions";
import PromptModal from "./PromptModal";
import AttachmentList from "./AttachmentList";

export default function BranchComposer({
  draft,
  tabs,
  scanTabs,
  loadingTabs,
  prompts,
  refreshPrompts,
  api,
  post,
  cid,
  close,
  created,
}) {
  const [question, setQuestion] = useState(draft.question);
  const ancestors = draft.ancestors || [draft.parent];
  const [config, setConfig] = useState(() => ({ ...draft.config,
    tab_mode: draft.config.tab_mode || (draft.config.new_chat ? "new" : "existing"),
    new_chat: false,
    ancestor_depth: draft.config.ancestor_depth > 0 ? Math.min(draft.config.ancestor_depth, ancestors.length) : (draft.config.ancestor_depth ?? 0),
  }));
  const ancestorDepth = config.ancestor_depth ?? 0;
  const selectedAncestors = ancestorDepth === -1 ? ancestors : ancestorDepth > 0 ? ancestors.slice(-ancestorDepth) : [];
  const [batch, setBatch] = useState(false);
  const [files, setFiles] = useState([]);
  const [uploads, setUploads] = useState(0);
  const [sending, setSending] = useState(false);
  const [saving, setSaving] = useState(false);
  const [tabAction, setTabAction] = useState("");
  const branching = !!tabAction;
  const tabMode = config.tab_mode;
  const automaticTab = tabMode !== "existing";
  const [preparedMode, setPreparedMode] = useState("existing");
  const [openedTab, setOpenedTab] = useState(null);
  const [tabNotice, setTabNotice] = useState("");
  const branchingRef = useRef(false);
  const hasSource = !!draft.parent.response_conversation_url && draft.parent.response_position > 0;
  const availableTabs = openedTab && !tabs.some((tab) => tab.key === openedTab.key) ? [openedTab, ...tabs] : tabs;
  const [savedConfig, setSavedConfig] = useState("");
  const savingRef = useRef(false);
  const [editingPrompt, setEditingPrompt] = useState(false);
  const [error, setError] = useState("");
  const input = useRef(null),
    area = useRef(null),
    sendingRef = useRef(false),
    uploadsRef = useRef(0);
  const questions = batch
    ? question
        .split("\n")
        .map((q) => q.trim())
        .filter(Boolean)
    : [question.trim()].filter(Boolean);
  const canSend =
    questions.length > 0 &&
    questions.length <= 20 &&
    !!config.selected_tab &&
    (!automaticTab || (preparedMode === tabMode && (tabMode !== "branch" || hasSource))) &&
    !sending &&
    !saving &&
    !branching &&
    !uploads;
  useEffect(() => {
    area.current?.focus();
    let active = true;
    // Refresh once per opening, including branches created from selected text.
    scanTabs()
      .then((failure) => {
        if (active && failure) setError(failure);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    setPreparedMode("existing");
    setTabNotice("");
    if (automaticTab) {
      setPreparedMode("");
      openChatGPTTab(tabMode, true);
    }
  }, [tabMode]);

  async function saveSettings() {
    if (savingRef.current || sendingRef.current || branchingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    setError("");
    setSavedConfig("");
    try {
      await api("/research/defaults", { method: "PUT", body: config });
      setSavedConfig(JSON.stringify(config));
    } catch (e) {
      setError(e.message);
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  }

  async function openChatGPTTab(kind, automatic = false) {
    if (branchingRef.current || sendingRef.current || savingRef.current) return;
    if (kind === "branch" && !hasSource) {
      setError("Node cha chưa có link và số thứ tự câu trả lời. Hãy bổ sung Nguồn ChatGPT hoặc chọn chế độ khác.");
      return;
    }
    branchingRef.current = true;
    setTabAction(kind);
    setOpenedTab(null);
    setTabNotice("");
    setConfig((old) => ({ ...old, selected_tab: "", new_chat: false }));
    setError("");
    try {
      const tab = await post(kind === "branch" ? `/conversations/${cid}/research/${draft.parent.id}/chatgpt-branch` : "/chrome-tabs", {});
      if (!tab?.key) throw new Error("Không xác định được tab ChatGPT vừa tạo. Hãy kiểm tra Chrome rồi thử lại.");
      await scanTabs().catch(() => {});
      setOpenedTab(tab);
      setConfig((old) => ({ ...old, selected_tab: tab.key, new_chat: false }));
      if (automatic) setPreparedMode(kind);
      setTabNotice(kind === "branch" ? "Đã mở nhánh ChatGPT trong tab mới, refresh danh sách và chọn tab nhánh để gửi câu hỏi."
        : "Đã mở ChatGPT trong tab mới, refresh danh sách và chọn tab đó để gửi câu hỏi.");
    } catch (e) {
      setError(e.message);
    } finally {
      branchingRef.current = false;
      setTabAction("");
    }
  }

  async function upload(items) {
    uploadsRef.current += 1;
    setUploads(uploadsRef.current);
    setError("");
    try {
      for (const file of items) {
        const body = new FormData();
        body.append("file", file);
        const result = await post("/conversations/" + cid + "/files", body);
        setFiles((old) => [...old, { ...result, type: file.type }]);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      uploadsRef.current -= 1;
      setUploads(uploadsRef.current);
    }
  }
  async function screenshot() {
    let stream;
    uploadsRef.current += 1;
    setUploads(uploadsRef.current);
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
      if (!blob) throw new Error("Không tạo được ảnh chụp màn hình.");
      await upload([new File([blob], "screen.png", { type: "image/png" })]);
    } catch (e) {
      if (e.name !== "NotAllowedError") setError(e.message);
    } finally {
      stream?.getTracks().forEach((t) => t.stop());
      uploadsRef.current -= 1;
      setUploads(uploadsRef.current);
    }
  }
  async function submit(e) {
    e?.preventDefault();
    if (!canSend || sendingRef.current || savingRef.current || branchingRef.current || uploadsRef.current || editingPrompt)
      return;
    sendingRef.current = true;
    setSending(true);
    setError("");
    try {
      const result = await post(
        `/conversations/${cid}/research/${draft.parent.id}/children`,
        {
          questions,
          excerpt: draft.excerpt,
          parent_reading_mark: draft.parent_reading_mark,
          config,
          files: files.map((f) => f.path),
        },
      );
      await created(result);
    } catch (e) {
      setError(e.message);
    } finally {
      sendingRef.current = false;
      setSending(false);
    }
  }
  return (
    <>
      <div className="modal-backdrop">
        <form
          className="modal branch-composer"
          onSubmit={submit}
          role="dialog"
          aria-modal="true"
          aria-labelledby="branch-title"
          inert={editingPrompt || undefined}
        >
          <div className="modal-head">
            <div>
              <span className="eyebrow">ĐÀO SÂU TỪ NODE ĐANG ĐỌC</span>
              <h2 id="branch-title">Tạo nhánh con</h2>
            </div>
            <button
              type="button"
              aria-label="Đóng tạo nhánh"
              disabled={sending || saving || branching || !!uploads}
              onClick={close}
            >
              <X size={18} />
            </button>
          </div>
          <div className="modal-body">
            <p className="muted">Node cha: {draft.parent.title}</p>
            <div className="branch-ancestor-options">
              {[
                ["new", "Tạo cuộc hội thoại ChatGPT mới"],
                ["branch", "Tạo nhánh từ cuộc hội thoại của node cha"],
              ].map(([mode, label]) => <label className="toggle" key={mode}>
                <input type="checkbox" checked={tabMode === mode} disabled={sending || saving || branching}
                  onChange={(e) => {
                    setError("");
                    setConfig((old) => ({ ...old, tab_mode: e.target.checked ? mode : "existing", new_chat: false }));
                  }} />
                {label}
              </label>)}
              <p className="muted">Chỉ chọn một chế độ. Khi bật hoặc mở lại hộp tạo node con với chế độ đã lưu,
                app tự mở và chọn tab mới. Bỏ cả hai để chọn tab thủ công.</p>
              {automaticTab && preparedMode !== tabMode && !branching && (tabMode !== "branch" || hasSource) &&
                <button type="button" disabled={sending || saving} onClick={() => openChatGPTTab(tabMode, true)}>Thử tạo tab lại</button>}
            </div>
            <div className="branch-save-settings">
              <button type="button" onClick={() => openChatGPTTab("new")} disabled={automaticTab || sending || saving || branching}>
                <Plus size={15} />{tabAction === "new" ? "Đang mở tab ChatGPT…" : "Mở tab ChatGPT mới"}
              </button>
              <button type="button" onClick={() => openChatGPTTab("branch")} disabled={automaticTab || !hasSource || sending || saving || branching}>
                <GitBranch size={15} />{tabAction === "branch" ? "Đang tạo nhánh ChatGPT…" : "Tạo nhánh ChatGPT"}
              </button>
              <span role="status">
                {tabAction === "new" ? "Đang mở ChatGPT trên Chrome 9222…"
                  : tabNotice || (!hasSource ? "Hãy bổ sung link và số thứ tự câu trả lời trong Nguồn ChatGPT của node cha để tạo nhánh ChatGPT."
                  : branching ? `Đang mở nhánh từ câu trả lời thứ ${draft.parent.response_position}…`
                  : `Tạo nhánh ChatGPT để giữ ngữ cảnh đến câu trả lời thứ ${draft.parent.response_position} của node cha.`)}
              </span>
            </div>
            <ChatOptions
              value={config}
              onChange={(patch) => setConfig((old) => ({ ...old, ...patch }))}
              tabs={availableTabs}
              scanTabs={async () => {
                setError("");
                const failure = await scanTabs();
                if (failure) setError(failure);
              }}
              loadingTabs={loadingTabs}
              prompts={prompts}
              editPrompt={() => setEditingPrompt(true)}
              scope="research"
              disabled={sending || saving || branching}
              tabDisabled={automaticTab}
              showNewChat={false}
            />
            <div className="branch-ancestor-options">
              <label className="toggle">
                <input type="checkbox" checked={ancestorDepth !== 0} disabled={sending || saving || branching}
                  onChange={(e) => setConfig((old) => ({ ...old, ancestor_depth: e.target.checked ? 1 : 0 }))} />
                Đính kèm ngữ cảnh tổ tiên
              </label>
              {ancestorDepth !== 0 && <>
                <label className="field">
                  <span>Lấy ngữ cảnh ngược tới</span>
                  <select aria-label="Lấy ngữ cảnh ngược tới" value={ancestorDepth} disabled={sending || saving || branching}
                    onChange={(e) => setConfig((old) => ({ ...old, ancestor_depth: Number(e.target.value) }))}>
                    {[...ancestors].reverse().map((node, index) => <option key={node.id} value={index + 1}>
                      {index === 0 ? "Cha" : index === 1 ? "Ông/bà" : index === 2 ? "Cụ" : `Tổ tiên cấp ${index + 1}`} — {node.title} ({index + 1} cặp)
                    </option>)}
                    <option value={-1}>Toàn bộ về node gốc ({ancestors.length} cặp)</option>
                  </select>
                </label>
                <details>
                  <summary>Sẽ đính kèm {selectedAncestors.length} cặp câu hỏi – câu trả lời</summary>
                  <ol>{selectedAncestors.map((node) => <li key={node.id}>{node.title}</li>)}</ol>
                  <p className="muted">Toàn bộ câu hỏi và câu trả lời của các node trên được đính kèm thành một file, từ cũ đến mới.</p>
                </details>
              </>}
            </div>
            <div className="branch-save-settings">
              <button type="button" onClick={saveSettings} disabled={sending || saving || branching}>
                <Save size={15} />
                {saving ? "Đang lưu…" : "Lưu cài đặt"}
              </button>
              <span role="status">
                {savedConfig === JSON.stringify(config)
                  ? "Đã lưu vào database. Áp dụng cho các nhánh tạo sau."
                  : "Lưu các tùy chọn để dùng lại khi tạo nhánh tiếp theo."}
              </span>
            </div>
            {draft.excerpt && (
              <details className="research-excerpt">
                <summary>Đoạn đã chọn từ node cha</summary>
                <blockquote>{draft.excerpt}</blockquote>
              </details>
            )}
            <div className="composer">
              <AttachmentList files={files} cid={cid} disabled={sending}
                onRemove={(file) => setFiles((old) => old.filter((item) => item.path !== file.path))} />
              <textarea
                ref={area}
                aria-label="Câu hỏi nhánh"
                value={question}
                disabled={sending}
                onChange={(e) => setQuestion(e.target.value)}
                rows="4"
                placeholder={
                  batch
                    ? "Mỗi dòng là một câu hỏi → một nhánh"
                    : "Nhập câu hỏi… Enter để gửi, Shift + Enter xuống dòng"
                }
                onKeyDown={(e) => {
                  if (
                    e.key === "Enter" &&
                    !e.shiftKey &&
                    !batch &&
                    !e.nativeEvent.isComposing
                  ) {
                    e.preventDefault();
                    submit();
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
                  <button
                    type="button"
                    onClick={() => input.current.click()}
                    disabled={!!uploads || sending}
                  >
                    <Paperclip size={16} />
                    Attach files
                  </button>
                  <button
                    type="button"
                    onClick={screenshot}
                    disabled={!!uploads || sending}
                  >
                    <Camera size={16} />
                    Screenshot
                  </button>
                  <input
                    type="file"
                    multiple
                    hidden
                    ref={input}
                    onChange={(e) => {
                      upload([...e.target.files]);
                      e.target.value = "";
                    }}
                  />
                </div>
              </div>
            </div>
            <label className="toggle">
              <input
                type="checkbox"
                checked={batch}
                disabled={sending}
                onChange={(e) => setBatch(e.target.checked)}
              />
              Tạo nhiều nhánh: mỗi dòng là một câu hỏi
            </label>
            <p className="branch-explanation">
              {automaticTab
                ? "Gửi vào tab mới do chế độ đã chọn tạo ra. Nếu tạo nhiều nhánh cùng lúc, các câu hỏi dùng chung tab này."
                : "Gửi vào cuộc hội thoại đang mở trong tab ChatGPT đã chọn."}{" "}
              Dùng mẫu prompt của Chat. Các node dùng tab ChatGPT khác nhau chạy song song;
              cùng một tab thì gửi lần lượt. Ngữ cảnh
              tổ tiên {ancestorDepth !== 0 ? `được đính kèm từ ${selectedAncestors.length} cặp câu hỏi – câu trả lời đã chọn.` : "chưa được đính kèm."}
            </p>
            {error && (
              <p role="alert" className="alert">
                {error}
              </p>
            )}
          </div>
          <div className="modal-foot">
            <span>{questions.length} nhánh · tối đa 20 mỗi lần</span>
            <button type="submit" className="primary" disabled={!canSend}>
              <Send size={15} />
              {sending
                ? "Đang xếp hàng…"
                : uploads
                  ? "Đang tải file…"
                  : `Xếp hàng ${questions.length} nhánh`}
            </button>
          </div>
        </form>
      </div>
      {editingPrompt && (
        <PromptModal
          kind="chat"
          selected={config.prompt}
          api={api}
          post={post}
          close={() => setEditingPrompt(false)}
          done={async (name) => {
            await refreshPrompts();
            setConfig((old) => ({ ...old, prompt: name }));
            setEditingPrompt(false);
          }}
        />
      )}
    </>
  );
}

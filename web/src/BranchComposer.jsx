import React, { useEffect, useRef, useState } from "react";
import { Camera, Paperclip, Send, X } from "lucide-react";
import ChatOptions from "./ChatOptions";
import PromptModal from "./PromptModal";

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
  const [config, setConfig] = useState(draft.config);
  const [batch, setBatch] = useState(false);
  const [files, setFiles] = useState([]);
  const [uploads, setUploads] = useState(0);
  const [sending, setSending] = useState(false);
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
    !sending &&
    !uploads;
  useEffect(() => {
    area.current?.focus();
  }, []);

  async function upload(items) {
    uploadsRef.current += 1;
    setUploads(uploadsRef.current);
    setError("");
    try {
      for (const file of items) {
        const body = new FormData();
        body.append("file", file);
        const result = await post("/conversations/" + cid + "/files", body);
        setFiles((old) => [...old, result]);
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
    if (!canSend || sendingRef.current || uploadsRef.current || editingPrompt)
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
              disabled={sending || !!uploads}
              onClick={close}
            >
              <X size={18} />
            </button>
          </div>
          <div className="modal-body">
            <p className="muted">Node cha: {draft.parent.title}</p>
            <ChatOptions
              value={config}
              onChange={(patch) => setConfig((old) => ({ ...old, ...patch }))}
              tabs={tabs}
              scanTabs={async () => {
                setError("");
                const failure = await scanTabs();
                if (failure) setError(failure);
              }}
              loadingTabs={loadingTabs}
              prompts={prompts}
              editPrompt={() => setEditingPrompt(true)}
              scope="research"
              disabled={sending}
            />
            {draft.excerpt && (
              <details className="research-excerpt">
                <summary>Đoạn đã chọn từ node cha</summary>
                <blockquote>{draft.excerpt}</blockquote>
              </details>
            )}
            <div className="composer">
              <div className="composer-files">
                {files.map((f) => (
                  <span key={f.path}>
                    {f.name}
                    <button
                      type="button"
                      aria-label={"Bỏ " + f.name}
                      disabled={sending}
                      onClick={() =>
                        setFiles((old) => old.filter((x) => x.path !== f.path))
                      }
                    >
                      <X size={13} />
                    </button>
                  </span>
                ))}
              </div>
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
              {config.new_chat
                ? "Bạn đã bật tạo cuộc chat mới cho mỗi câu hỏi."
                : "Gửi vào cuộc hội thoại đang mở trong tab ChatGPT đã chọn."}{" "}
              Dùng mẫu prompt của Chat; các câu hỏi được gửi lần lượt. Ngữ cảnh
              tổ tiên không được tự thêm vào prompt.
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

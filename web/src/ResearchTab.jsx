import React, { useEffect, useRef, useState } from "react";
import {
  GitBranch,
  Plus,
  RefreshCw,
  X,
  Check,
  ChevronRight,
  Copy,
  Settings2,
  Send,
  Paperclip,
  Camera,
  Search,
  LoaderCircle,
} from "lucide-react";
import "./research.css";
import ResearchGraph from "./ResearchGraph";

const labels = {
  done: "Đã trả lời",
  queued: "Đang chờ",
  working: "Đang trả lời",
  error: "Lỗi",
  cancelled: "Đã hủy",
  interrupted: "Bị gián đoạn",
};
const defaultInstructions =
  "Giải thích bằng tiếng Việt, từ trực giác đến chi tiết. Định nghĩa các thuật ngữ mới, đưa ví dụ cụ thể và giữ công thức toán khi cần. Tập trung vào câu hỏi hiện tại.";
const statusOf = (n) => (n.understood ? "Đã hiểu" : labels[n.status]);
function readSaved(key, fallback) {
  try {
    return JSON.parse(localStorage.getItem(key)) ?? fallback;
  } catch {
    return fallback;
  }
}
function pathTo(nodes, id) {
  const map = new Map(nodes.map((n) => [n.id, n])),
    path = [],
    seen = new Set();
  while (map.has(id) && !seen.has(id)) {
    seen.add(id);
    const n = map.get(id);
    path.unshift(n);
    id = n.parent_id;
  }
  return path;
}

function TabSelect({ value, onChange, tabs }) {
  return (
    <select
      aria-label="Research ChatGPT tab"
      value={value || ""}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">Chọn tab ChatGPT…</option>
      {tabs.map((t) => (
        <option key={t.key} value={t.key}>
          {t.title}
        </option>
      ))}
      {value && !tabs.some((t) => t.key === value) && (
        <option value={value}>Tab đã lưu — làm mới để xem tên</option>
      )}
    </select>
  );
}
function Power({ value, onChange }) {
  return (
    <select
      aria-label="Research Power"
      value={value}
      onChange={(e) => onChange(+e.target.value)}
    >
      {["Giữ nguyên", "Instant", "Medium", "High"].map((v, i) => (
        <option key={v} value={i - 1}>
          {v}
        </option>
      ))}
    </select>
  );
}

export default function ResearchTab({
  cid,
  api,
  post,
  Md,
  settings,
  changeSettings,
  chromeTabs,
  scanTabs,
  loadingTabs,
  focus,
  goChat,
}) {
  const storageKey = "rtc-research-" + cid;
  const [nodes, setNodes] = useState([]),
    [selected, setSelected] = useState(() => readSaved(storageKey, ""));
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const [detail, setDetail] = useState(null),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [query, setQuery] = useState("");
  const [graphExpanded, setGraphExpanded] = useState(false);
  const [menu, setMenu] = useState(null),
    [draft, setDraft] = useState(null),
    [defaultsOpen, setDefaultsOpen] = useState(false);
  const [defaults, setDefaults] = useState(null),
    [savingDefaults, setSavingDefaults] = useState(false);
  const answer = useRef(null),
    contextButton = useRef(null),
    focusSeen = useRef(null);
  const base = "/conversations/" + cid + "/research";
  const selectedMeta = nodes.find((n) => n.id === selected);
  const roots = nodes.filter((n) => !n.parent_id);
  const rootId = selectedMeta?.root_id || roots.at(-1)?.id;
  const treeNodes = nodes.filter((n) => n.root_id === rootId);
  const trail = pathTo(nodes, selected);
  const currentTab =
    settings.research_selected_tab || settings.chatgpt_selected_tab || "";
  const currentDefaults = () => ({
    selected_tab: currentTab,
    power: settings.research_power ?? -1,
    attach_transcript: !!settings.research_attach_transcript,
    max_lines: settings.research_max_lines ?? 300,
    screenshot: !!settings.research_screenshot,
    instructions: settings.research_instructions || defaultInstructions,
  });

  function select(id, all = nodes) {
    selectedRef.current = id;
    setSelected(id);
    localStorage.setItem(storageKey, JSON.stringify(id));
    setMenu(null);
  }
  async function refresh() {
    const list = await api(base);
    setNodes(list);
    let id = selectedRef.current;
    if (!list.some((n) => n.id === id)) {
      id = list.filter((n) => !n.parent_id).at(-1)?.id || "";
      select(id, list);
    }
    if (id) {
      const value = await api(base + "/" + id);
      if (selectedRef.current === id) setDetail(value);
    } else setDetail(null);
  }
  useEffect(() => {
    let live = true,
      timer;
    const poll = async () => {
      try {
        await refresh();
      } catch (e) {
        if (live) setError(e.message);
      }
      if (live) timer = setTimeout(poll, 1000);
    };
    poll();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [cid]);
  useEffect(() => {
    setDetail(null);
    if (selected)
      api(base + "/" + selected)
        .then((value) => {
          if (selectedRef.current === selected) setDetail(value);
        })
        .catch((e) => setError(e.message));
  }, [selected]);
  useEffect(() => {
    if (
      focus?.id &&
      focusSeen.current !== focus.nonce &&
      nodes.some((n) => n.id === focus.id)
    ) {
      focusSeen.current = focus.nonce;
      select(focus.id);
    }
  }, [focus, nodes]);
  useEffect(() => {
    if (!menu) return;
    contextButton.current?.focus();
    const close = () => setMenu(null);
    const key = (e) => {
      if (e.key === "Escape") close();
    };
    window.addEventListener("click", close);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("keydown", key);
    };
  }, [menu]);
  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(""), 2500);
    return () => clearTimeout(timer);
  }, [notice]);

  async function action(fn) {
    setError("");
    try {
      await fn();
      await refresh();
    } catch (e) {
      setError(e.message);
    }
  }
  function createDraft(excerpt = "") {
    if (detail?.status !== "done") return;
    if (excerpt.length > 20000) {
      setMenu(null);
      setError("Chọn một đoạn ngắn hơn 20.000 ký tự để tạo nhánh.");
      return;
    }
    setMenu(null);
    setDraft({
      parent: detail,
      excerpt,
      question: excerpt ? "Giải thích giúp tôi đoạn này: " + excerpt : "",
      config: currentDefaults(),
    });
  }
  function selectionMenu(e) {
    const selection = window.getSelection();
    if (
      detail?.status !== "done" ||
      !selection ||
      selection.isCollapsed ||
      !answer.current?.contains(selection.anchorNode) ||
      !answer.current?.contains(selection.focusNode)
    )
      return;
    const excerpt = selection.toString().trim();
    if (!excerpt) return;
    e.preventDefault();
    setMenu({
      x: Math.max(8, Math.min(e.clientX, window.innerWidth - 282)),
      y: Math.max(8, Math.min(e.clientY, window.innerHeight - 76)),
      excerpt,
    });
  }
  async function saveDefaults() {
    setSavingDefaults(true);
    try {
      await changeSettings(
        Object.fromEntries(
          Object.entries(defaults).map(([k, v]) => ["research_" + k, v]),
        ),
      );
      setDefaultsOpen(false);
    } catch (e) {
      setError(e.message);
    } finally {
      setSavingDefaults(false);
    }
  }
  const reading = detail?.id === selected ? detail : null;

  return (
    <section className="research">
      <div className="research-toolbar">
        <div className="research-heading">
          <GitBranch size={21} />
          <div>
            <h2>Nghiên cứu theo nhánh</h2>
            <span>
              {treeNodes.filter((n) => n.understood).length}/{treeNodes.length}{" "}
              node đã hiểu ·{" "}
              {
                nodes.filter((n) => ["queued", "working"].includes(n.status))
                  .length
              }{" "}
              đang xử lý
            </span>
          </div>
        </div>
        <div className="research-controls">
          <TabSelect
            value={currentTab}
            tabs={chromeTabs}
            onChange={(v) =>
              action(() => changeSettings({ research_selected_tab: v }))
            }
          />
          <button
            title="Làm mới tab ChatGPT"
            onClick={scanTabs}
            disabled={loadingTabs}
          >
            <RefreshCw size={15} />
          </button>
          <Power
            value={settings.research_power ?? -1}
            onChange={(v) =>
              action(() => changeSettings({ research_power: v }))
            }
          />
          <button
            title="Tùy chỉnh nghiên cứu"
            onClick={() => {
              setDefaults(currentDefaults());
              setDefaultsOpen((v) => !v);
            }}
          >
            <Settings2 size={16} />
          </button>
        </div>
      </div>
      {defaultsOpen && (
        <div className="research-defaults">
          <strong>Mặc định cho nhánh mới</strong>
          <p>
            Mỗi nhánh dùng một cuộc chat mới, với ngữ cảnh từ gốc đến node cha.
            Nên chọn tab ChatGPT riêng cho nghiên cứu.
          </p>
          <div className="inline">
            <label className="toggle">
              <input
                type="checkbox"
                checked={defaults.attach_transcript}
                onChange={(e) =>
                  setDefaults({
                    ...defaults,
                    attach_transcript: e.target.checked,
                  })
                }
              />
              Đính kèm transcript
            </label>
            <label>
              Số dòng{" "}
              <input
                aria-label="Default research transcript lines"
                type="number"
                min="0"
                value={defaults.max_lines}
                onChange={(e) =>
                  setDefaults({ ...defaults, max_lines: +e.target.value })
                }
              />
            </label>
            <label className="toggle">
              <input
                type="checkbox"
                checked={defaults.screenshot}
                onChange={(e) =>
                  setDefaults({ ...defaults, screenshot: e.target.checked })
                }
              />
              Chụp màn hình lúc xếp hàng
            </label>
          </div>
          <label className="field">
            <span>Yêu cầu cách giải thích</span>
            <textarea
              rows="3"
              value={defaults.instructions}
              onChange={(e) =>
                setDefaults({ ...defaults, instructions: e.target.value })
              }
            />
          </label>
          <button
            className="primary"
            onClick={saveDefaults}
            disabled={savingDefaults}
          >
            Lưu mặc định
          </button>
        </div>
      )}
      {error && (
        <div role="alert" className="alert">
          {error}
          <button aria-label="Đóng lỗi nghiên cứu" onClick={() => setError("")}>
            <X size={14} />
          </button>
        </div>
      )}
      {notice && (
        <div className="research-notice" role="status">
          {notice}
        </div>
      )}
      {!roots.length ? (
        <div className="research-welcome">
          <GitBranch size={42} />
          <h3>Một câu trả lời. Nhiều hướng tìm hiểu.</h3>
          <p>
            Ở tab Chat, bấm “Nghiên cứu theo nhánh” dưới câu trả lời. Sau đó bôi
            đen một khái niệm và chuột phải để tạo nhánh con.
          </p>
          <button className="primary" onClick={goChat}>
            Mở Chat
          </button>
        </div>
      ) : (
        <div className={"research-body " + (graphExpanded ? "full-graph" : "")}>
          <div className="research-graph">
            <div className="research-map-toolbar">
              <select
                aria-label="Cây nghiên cứu"
                value={rootId || ""}
                onChange={(e) => select(e.target.value)}
              >
                {roots.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.title}
                  </option>
                ))}
              </select>
              <div className="research-search">
                <Search size={14} />
                <input
                  aria-label="Tìm node"
                  placeholder="Tìm khái niệm…"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>
            </div>
            {query && (
              <div className="research-search-results">
                {nodes
                  .filter((n) =>
                    (n.question + " " + n.excerpt)
                      .toLowerCase()
                      .includes(query.toLowerCase()),
                  )
                  .slice(0, 20)
                  .map((n) => (
                    <button
                      key={n.id}
                      onClick={() => {
                        select(n.id);
                        setQuery("");
                      }}
                    >
                      {n.title}
                      <small>{statusOf(n)}</small>
                    </button>
                  ))}
              </div>
            )}
            <ResearchGraph
              key={rootId}
              nodes={treeNodes}
              rootId={rootId}
              selected={selected}
              onSelect={(id) => {
                select(id);
                setGraphExpanded(false);
              }}
              statusOf={statusOf}
              api={api}
              base={base}
              expanded={graphExpanded}
              onExpand={() => setGraphExpanded((v) => !v)}
            />
          </div>
          <div className="research-reader">
            <div className="research-breadcrumb">
              {trail.map((n, i) => (
                <React.Fragment key={n.id}>
                  {i > 0 && <ChevronRight size={11} />}
                  <button title={n.title} onClick={() => select(n.id)}>
                    {i === 0 ? "Gốc" : n.title}
                  </button>
                </React.Fragment>
              ))}
            </div>
            {reading ? (
              <>
                <div className="research-reader-head">
                  <span className={"research-status " + reading.status}>
                    {statusOf(reading)}
                  </span>
                  <div className="inline">
                    <button
                      title="Đổi tên node"
                      onClick={() => {
                        const title = window.prompt(
                          "Tên hiển thị trên cây",
                          reading.title,
                        );
                        if (title?.trim())
                          action(() =>
                            api(base + "/" + reading.id, {
                              method: "PATCH",
                              body: { title: title.trim() },
                            }),
                          );
                      }}
                    >
                      Đổi tên
                    </button>
                    <label className="toggle">
                      <input
                        type="checkbox"
                        checked={reading.understood}
                        disabled={reading.status !== "done"}
                        onChange={(e) => {
                          const understood = e.target.checked;
                          setDetail((old) => ({ ...old, understood }));
                          action(() =>
                            api(base + "/" + reading.id, {
                              method: "PATCH",
                              body: { understood },
                            }),
                          );
                        }}
                      />
                      Đã hiểu
                    </label>
                  </div>
                </div>
                <div className="research-reading scroll">
                  <div className="research-question">
                    <small>CÂU HỎI</small>
                    <h3>{reading.question}</h3>
                  </div>
                  {reading.excerpt && (
                    <details className="research-excerpt">
                      <summary>Đoạn trích từ node cha</summary>
                      <blockquote>{reading.excerpt}</blockquote>
                    </details>
                  )}
                  <div
                    ref={answer}
                    className="research-answer"
                    onContextMenu={selectionMenu}
                  >
                    {reading.answer ? (
                      <Md text={reading.markdown} />
                    ) : reading.partial ? (
                      <>
                        <small className="muted">
                          Câu trả lời đang được tạo
                        </small>
                        <pre className="research-stream">{reading.partial}</pre>
                      </>
                    ) : (
                      <div className="research-wait">
                        {reading.status === "working" && (
                          <LoaderCircle className="spin" size={25} />
                        )}
                        <p>
                          {reading.status === "queued"
                            ? "Đã xếp hàng. Bạn có thể tiếp tục tạo những nhánh khác."
                            : reading.detail || "Đang chuẩn bị…"}
                        </p>
                      </div>
                    )}
                  </div>
                  {["working", "error", "cancelled", "interrupted"].includes(
                    reading.status,
                  ) && (
                    <p className={"research-detail " + reading.status}>
                      {reading.detail}
                    </p>
                  )}
                  {reading.parent_id && (
                    <details className="research-request">
                      <summary>Tùy chỉnh đã dùng cho node này</summary>
                      <p>
                        Power:{" "}
                        {
                          ["Giữ nguyên", "Instant", "Medium", "High"][
                            (reading.config.power ?? -1) + 1
                          ]
                        }{" "}
                        ·{" "}
                        {reading.config.attach_transcript
                          ? "Có transcript"
                          : "Không kèm transcript"}{" "}
                        ·{" "}
                        {reading.config.screenshot
                          ? "Có ảnh chụp lúc xếp hàng"
                          : "Không tự chụp màn hình"}
                      </p>
                      <p>
                        Ngữ cảnh: {trail.length - 1} node tổ tiên, không gồm
                        nhánh anh em.
                      </p>
                    </details>
                  )}
                </div>
                <div className="research-reader-actions">
                  {reading.status === "done" ? (
                    <>
                      <button className="primary" onClick={() => createDraft()}>
                        <Plus size={15} />
                        Tạo nhánh con
                      </button>
                      <button
                        onClick={() =>
                          action(async () => {
                            await navigator.clipboard.writeText(reading.answer);
                            setNotice("Đã copy câu trả lời");
                          })
                        }
                      >
                        <Copy size={14} />
                        Copy
                      </button>
                      <small>Bôi đen + chuột phải để hỏi về một đoạn</small>
                    </>
                  ) : ["queued", "working"].includes(reading.status) ? (
                    <button
                      onClick={() =>
                        action(() => post(base + "/" + reading.id + "/cancel"))
                      }
                    >
                      Hủy nhánh này
                    </button>
                  ) : (
                    <button
                      className="primary"
                      onClick={() =>
                        action(() =>
                          post(base + "/" + reading.id + "/retry", {
                            selected_tab: currentTab || undefined,
                          }),
                        )
                      }
                    >
                      <RefreshCw size={14} />
                      Thử lại với tab đã chọn
                    </button>
                  )}
                </div>
              </>
            ) : (
              <div className="research-wait">Đang tải node…</div>
            )}
          </div>
        </div>
      )}
      {menu && (
        <div
          className="research-context-menu"
          role="menu"
          style={{ left: menu.x, top: menu.y }}
        >
          <button
            ref={contextButton}
            role="menuitem"
            onClick={() => createDraft(menu.excerpt)}
          >
            <GitBranch size={16} />
            Tạo nhánh từ đoạn đã chọn
          </button>
        </div>
      )}
      {draft && (
        <BranchComposer
          key={draft.parent.id}
          draft={draft}
          tabs={chromeTabs}
          api={api}
          post={post}
          cid={cid}
          close={() => setDraft(null)}
          created={async (children) => {
            setDraft(null);
            setNotice(`Đã xếp hàng ${children.length} nhánh độc lập`);
            await refresh();
          }}
        />
      )}
    </section>
  );
}

function BranchComposer({ draft, tabs, api, post, cid, close, created }) {
  const [question, setQuestion] = useState(draft.question),
    [excerpt, setExcerpt] = useState(draft.excerpt),
    [config, setConfig] = useState(draft.config);
  const [batch, setBatch] = useState(false),
    [files, setFiles] = useState([]),
    [uploading, setUploading] = useState(false),
    [sending, setSending] = useState(false),
    [error, setError] = useState("");
  const input = useRef(null),
    area = useRef(null);
  const questions = batch
    ? question
        .split("\n")
        .map((q) => q.trim())
        .filter(Boolean)
    : [question.trim()].filter(Boolean);
  useEffect(() => {
    area.current?.focus();
  }, []);
  const set = (key, value) => setConfig((old) => ({ ...old, [key]: value }));
  async function upload(items) {
    setUploading(true);
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
      setUploading(false);
    }
  }
  async function screenshot() {
    let stream;
    setUploading(true);
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
      await upload([
        new File([blob], "research-screen.png", { type: "image/png" }),
      ]);
    } catch (e) {
      if (e.name !== "NotAllowedError") setError(e.message);
    } finally {
      stream?.getTracks().forEach((t) => t.stop());
      setUploading(false);
    }
  }
  async function submit(e) {
    e.preventDefault();
    setSending(true);
    setError("");
    try {
      const result = await post(
        "/conversations/" + cid + "/research/" + draft.parent.id + "/children",
        { questions, excerpt, config, files: files.map((f) => f.path) },
      );
      await created(result);
    } catch (e) {
      setError(e.message);
      setSending(false);
    }
  }
  return (
    <div className="modal-backdrop">
      <form
        className="modal branch-composer"
        onSubmit={submit}
        role="dialog"
        aria-modal="true"
        aria-labelledby="branch-title"
      >
        <div className="modal-head">
          <div>
            <span className="eyebrow">ĐÀO SÂU TỪ NODE ĐANG ĐỌC</span>
            <h2 id="branch-title">Tạo nhánh con</h2>
          </div>
          <button
            type="button"
            aria-label="Đóng tạo nhánh"
            disabled={sending}
            onClick={close}
          >
            <X size={18} />
          </button>
        </div>
        <div className="modal-body">
          <p className="muted">Node cha: {draft.parent.title}</p>
          {excerpt && (
            <label className="field">
              <span>Đoạn đang tìm hiểu</span>
              <textarea
                aria-label="Đoạn trích"
                value={excerpt}
                onChange={(e) => setExcerpt(e.target.value)}
                rows="3"
                maxLength="20000"
              />
            </label>
          )}
          <label className="field">
            <span>
              {batch
                ? "Mỗi dòng là một câu hỏi → một nhánh độc lập"
                : "Câu hỏi của nhánh mới"}
            </span>
            <textarea
              ref={area}
              aria-label="Câu hỏi nhánh"
              value={question}
              onChange={(e) => setQuestion(e.target.value)}
              rows="4"
              placeholder="Khái niệm này là gì? Cho tôi một ví dụ đơn giản…"
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
          </label>
          <label className="toggle">
            <input
              type="checkbox"
              checked={batch}
              onChange={(e) => setBatch(e.target.checked)}
            />
            Tạo nhiều nhánh: mỗi dòng là một câu hỏi
          </label>
          <div className="branch-options">
            <label className="field">
              <span>Tab ChatGPT</span>
              <TabSelect
                tabs={tabs}
                value={config.selected_tab}
                onChange={(v) => set("selected_tab", v)}
              />
            </label>
            <label className="field">
              <span>Power</span>
              <Power value={config.power} onChange={(v) => set("power", v)} />
            </label>
          </div>
          <details>
            <summary>Tùy chỉnh riêng và đính kèm</summary>
            <div className="branch-attachments">
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={config.attach_transcript}
                  onChange={(e) => set("attach_transcript", e.target.checked)}
                />
                Đính kèm transcript
              </label>
              <label className="field">
                <span>Số dòng transcript (0 = tất cả)</span>
                <input
                  type="number"
                  min="0"
                  value={config.max_lines}
                  onChange={(e) => set("max_lines", +e.target.value)}
                />
              </label>
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={config.screenshot}
                  onChange={(e) => set("screenshot", e.target.checked)}
                />
                Chụp màn hình ngay khi xếp hàng
              </label>
              <label className="field">
                <span>Yêu cầu cách giải thích</span>
                <textarea
                  value={config.instructions}
                  onChange={(e) => set("instructions", e.target.value)}
                  rows="3"
                  maxLength="20000"
                />
              </label>
              <div className="inline">
                <button
                  type="button"
                  disabled={uploading || sending}
                  onClick={() => input.current.click()}
                >
                  <Paperclip size={14} />
                  Đính kèm file
                </button>
                <button
                  type="button"
                  disabled={uploading || sending}
                  onClick={screenshot}
                >
                  <Camera size={14} />
                  Chọn ảnh chụp
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
              <div className="composer-files">
                {files.map((f) => (
                  <span key={f.path}>
                    {f.name}
                    <button
                      type="button"
                      onClick={() =>
                        setFiles((old) => old.filter((x) => x.path !== f.path))
                      }
                    >
                      <X size={12} />
                    </button>
                  </span>
                ))}
              </div>
            </div>
          </details>
          <p className="branch-explanation">
            Mỗi nhánh có một cuộc chat mới. App gửi câu hỏi cùng đường dẫn ngữ
            cảnh từ gốc; các nhánh được xử lý lần lượt trong tab đã chọn. Tùy
            chỉnh và file được giữ nguyên khi xếp hàng.
          </p>
          {error && (
            <p role="alert" className="alert">
              {error}
            </p>
          )}
        </div>
        <div className="modal-foot">
          <span>{questions.length} nhánh · tối đa 20 mỗi lần</span>
          <button
            type="submit"
            className="primary"
            disabled={
              !questions.length ||
              questions.length > 20 ||
              !config.selected_tab ||
              sending ||
              uploading
            }
          >
            <Send size={15} />
            {sending
              ? "Đang xếp hàng…"
              : uploading
                ? "Đang tải file…"
                : `Xếp hàng ${questions.length} nhánh`}
          </button>
        </div>
      </form>
    </div>
  );
}

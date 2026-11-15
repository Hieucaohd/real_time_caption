import React, { useEffect, useRef, useState } from "react";
import {
  GitBranch,
  Plus,
  RefreshCw,
  X,
  Check,
  ChevronRight,
  Copy,
  Search,
  LoaderCircle,
  Maximize2,
  Minimize2,
  Eye,
  EyeOff,
  Trash2,
  Highlighter,
} from "lucide-react";
import "./research.css";
import ResearchGraph from "./ResearchGraph";
import BranchComposer from "./BranchComposer";
import ResponseSource from "./ResponseSource";
import { selectedReadingMark, readingMarkRange } from "./readingMark";

const labels = {
  done: "Đã trả lời",
  queued: "Đang chờ",
  working: "Đang trả lời",
  error: "Lỗi",
  cancelled: "Đã hủy",
  interrupted: "Bị gián đoạn",
};
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

export default function ResearchTab({
  cid,
  api,
  post,
  Md,
  settings,
  prompts,
  refreshPrompts,
  chatAttach,
  chromeTabs,
  scanTabs,
  loadingTabs,
  focus,
  goChat,
  readingFocus = false,
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
  const [showHidden, setShowHidden] = useState(false);
  const [changingBranch, setChangingBranch] = useState(false);
  const [savingMark, setSavingMark] = useState(false);
  const [graphExpanded, setGraphExpanded] = useState(false);
  const [readerExpanded, setReaderExpanded] = useState(false);
  const [menu, setMenu] = useState(null),
    [draft, setDraft] = useState(null);
  const answer = useRef(null),
    contextButton = useRef(null),
    focusSeen = useRef(null);
  const base = "/conversations/" + cid + "/research";
  const selectedMeta = nodes.find((n) => n.id === selected);
  const visibleNodes = nodes.filter((n) => showHidden || !n.hidden);
  const roots = visibleNodes.filter((n) => !n.parent_id);
  const rootId = selectedMeta?.root_id || roots.at(-1)?.id;
  const treeNodes = visibleNodes.filter((n) => n.root_id === rootId);
  const trail = pathTo(nodes, selected);
  const currentTab = settings.chatgpt_selected_tab || "";
  const currentDefaults = () => ({
    selected_tab: currentTab,
    prompt: settings.chat_prompt || prompts.at(-1) || "",
    power: settings.chat_chatgpt_power ?? -1,
    attach_transcript: !!chatAttach,
    attach_history: !!settings.chat_attach_history,
    max_lines: settings.chat_max_lines ?? 300,
    screenshot: !!settings.chat_screenshot,
    new_chat: false,
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
    if (!list.some((n) => n.id === id && (showHidden || !n.hidden))) {
      id = list.filter((n) => !n.parent_id && (showHidden || !n.hidden)).at(-1)?.id || "";
      select(id, list);
    }
    if (id) {
      try {
        const value = await api(base + "/" + id);
        if (selectedRef.current === id) setDetail(value);
      } catch (e) {
        if (selectedRef.current === id) throw e;
      }
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
  }, [cid, showHidden]);
  useEffect(() => {
    setDetail(null);
    if (selected)
      api(base + "/" + selected)
        .then((value) => {
          if (selectedRef.current === selected) setDetail(value);
        })
        .catch((e) => { if (selectedRef.current === selected) setError(e.message); });
  }, [selected]);
  useEffect(() => {
    if (
      focus?.id &&
      focusSeen.current !== focus.nonce &&
      nodes.some((n) => n.id === focus.id)
    ) {
      focusSeen.current = focus.nonce;
      if (nodes.find((n) => n.id === focus.id)?.hidden) setShowHidden(true);
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
  async function changeBranch(node, operation) {
    if (changingBranch) return;
    const ids = new Set([node.id]);
    let size;
    do {
      size = ids.size;
      nodes.forEach((n) => { if (ids.has(n.parent_id)) ids.add(n.id); });
    } while (size !== ids.size);
    if (operation === "delete" && !window.confirm(
      `Xóa vĩnh viễn “${node.title}” và ${ids.size - 1} node con? Câu trả lời và ảnh của nhánh sẽ bị xóa. Lịch sử Chat gốc vẫn được giữ.`,
    )) return;
    setChangingBranch(true);
    await action(async () => {
      if (operation === "delete") {
        await api(base + "/" + node.id, { method: "DELETE" });
        select(node.parent_id || "");
      } else {
        await api(base + "/" + node.id + "/visibility", {
          method: "PUT", body: { hidden: operation === "hide" },
        });
        if (operation === "hide" && !showHidden) select(node.parent_id || "");
      }
      setNotice(operation === "delete" ? "Đã xóa nhánh và các node con." : operation === "hide"
        ? "Đã ẩn nhánh. Bật Hiện node đã ẩn để khôi phục." : "Đã hiện lại nhánh và các node con.");
    });
    setChangingBranch(false);
  }
  async function createDraft(excerpt = "") {
    if (detail?.status !== "done") return;
    if (excerpt.length > 20000) {
      setMenu(null);
      setError("Chọn một đoạn ngắn hơn 20.000 ký tự để tạo nhánh.");
      return;
    }
    setMenu(null);
    setError("");
    try {
      const saved = await api("/research/defaults");
      if (selectedRef.current !== detail.id) return;
      setDraft({
        parent: detail,
        excerpt,
        question: excerpt,
        config: { ...currentDefaults(), ...saved },
      });
    } catch (e) {
      setError(e.message);
    }
  }
  async function saveReadingMark(clear = false) {
    if (savingMark || detail?.id !== selected || detail.status !== "done") return;
    const mark = clear ? null : selectedReadingMark(answer.current);
    if (!clear && !mark) {
      setNotice("Bôi đen đoạn đã đọc tới, rồi bấm Đánh dấu.");
      return;
    }
    setSavingMark(true);
    await action(async () => {
      await api(base + "/" + detail.id + "/reading-mark", { method: "PUT", body: { mark } });
      window.getSelection()?.removeAllRanges();
      setNotice(clear ? "Đã bỏ dấu." : "Đã tô vàng và lưu dấu đọc của node này.");
    });
    setSavingMark(false);
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
      y: Math.max(8, Math.min(e.clientY, window.innerHeight - 118)),
      excerpt,
    });
  }
  const reading = detail?.id === selected && (showHidden || !detail.hidden) ? detail : null;
  useEffect(() => {
    const range = readingMarkRange(answer.current, reading?.reading_mark);
    if (range) CSS.highlights.set("rtc-reading-mark", new Highlight(range));
    else CSS.highlights.delete("rtc-reading-mark");
    return () => CSS.highlights.delete("rtc-reading-mark");
  }, [reading?.id, reading?.answer, reading?.reading_mark]);

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
        <label className="toggle research-show-hidden">
          <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} />
          Hiện node đã ẩn ({nodes.filter((n) => n.hidden).length})
        </label>
        {!readingFocus && roots.length > 0 && (
          <button
            className="reader-expand"
            aria-pressed={readerExpanded}
            onClick={() => {
              setReaderExpanded((v) => !v);
              setGraphExpanded(false);
            }}
          >
            {readerExpanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
            {readerExpanded ? "Hiện đồ thị" : "Mở rộng câu trả lời"}
          </button>
        )}
      </div>
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
        <div
          className={
            "research-body " +
            (readerExpanded || readingFocus
              ? "full-reader"
              : graphExpanded
                ? "full-graph"
                : "")
          }
        >
          <div className="research-graph">
            <div className="research-map-toolbar">
              <select
                aria-label="Cây nghiên cứu"
                value={rootId || ""}
                onChange={(e) => select(e.target.value)}
              >
                {roots.map((n) => (
                  <option key={n.id} value={n.id}>
                    {n.hidden ? "[Ẩn] " : ""}{n.title}
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
                {visibleNodes
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
                    {statusOf(reading)}{reading.hidden ? " · Đã ẩn" : ""}
                  </span>
                  <div className="inline">
                    <button disabled={changingBranch} onClick={() => changeBranch(reading, reading.hidden ? "show" : "hide")}
                      title="Áp dụng cho node này và toàn bộ node con">
                      {reading.hidden ? <Eye size={13} /> : <EyeOff size={13} />}
                      {reading.hidden ? "Hiện lại nhánh" : "Ẩn nhánh"}
                    </button>
                    <button disabled={changingBranch} onClick={() => changeBranch(reading, "delete")}
                      title="Xóa node này và toàn bộ node con">
                      <Trash2 size={13} />Xóa nhánh
                    </button>
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
                  <p className="research-response-tab" title={reading.response_tab_title || ""}>
                    Tab ChatGPT trả lời: <strong>{reading.response_tab_title || "Chưa có tên tab được lưu"}</strong>
                  </p>
                  <ResponseSource response={reading} />
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
                        Prompt:{" "}
                        {reading.config.prompt ||
                          "Yêu cầu đã lưu từ phiên bản cũ"}{" "}
                        ·{" "}
                        {reading.config.new_chat
                          ? "Tạo cuộc chat mới"
                          : "Tiếp tục tab ChatGPT đã chọn"}
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
                      <button disabled={savingMark} onMouseDown={(e) => e.preventDefault()}
                        onClick={() => saveReadingMark()} title="Bôi đen đoạn đã đọc tới rồi bấm để tô vàng. Mỗi node lưu một dấu đọc.">
                        <Highlighter size={14} />Đánh dấu
                      </button>
                      {reading.reading_mark && <>
                        <button onClick={() => {
                          const range = readingMarkRange(answer.current, reading.reading_mark);
                          const pane = answer.current?.closest(".research-reading");
                          if (range && pane) pane.scrollTop += range.getBoundingClientRect().top - pane.getBoundingClientRect().top - 20;
                        }}>Tới dấu</button>
                        <button disabled={savingMark} onClick={() => saveReadingMark(true)}>Bỏ dấu</button>
                      </>}
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
                        action(() => post(base + "/" + reading.id + "/retry"))
                      }
                    >
                      <RefreshCw size={14} />
                      Thử lại với tab đã lưu
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
          <button role="menuitem" onMouseDown={(e) => e.preventDefault()} onClick={() => saveReadingMark()}>
            <Highlighter size={16} />Đánh dấu đã đọc tới đây
          </button>
        </div>
      )}
      {draft && (
        <BranchComposer
          key={draft.parent.id}
          draft={draft}
          tabs={chromeTabs}
          scanTabs={scanTabs}
          loadingTabs={loadingTabs}
          prompts={prompts}
          refreshPrompts={refreshPrompts}
          api={api}
          post={post}
          cid={cid}
          close={() => setDraft(null)}
          created={async (children) => {
            setDraft(null);
            setNotice(`Đã xếp hàng ${children.length} nhánh`);
            await refresh();
          }}
        />
      )}
    </section>
  );
}

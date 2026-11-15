import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  MousePointer2,
  Hand,
  Undo2,
  Redo2,
  LayoutList,
  Plus,
  Minus,
  Maximize,
  LocateFixed,
  Expand,
  HelpCircle,
  Check,
  ChevronRight,
  ChevronDown,
  LoaderCircle,
} from "lucide-react";
import {
  WIDTH,
  HEIGHT,
  clamp,
  worldPoint,
  zoomAt,
  descendants,
  visibleNodes,
  autoLayout,
  addMissingPositions,
  bounds,
  fitCamera,
  edgePath,
} from "./graphGeometry";
import "./graph.css";

const initial = {
  positions: {},
  camera: { x: 40, y: 40, zoom: 1 },
  collapsed: [],
  snap: true,
  wheel_zoom: true,
  mode: "select",
  move_subtree: false,
};
function mergePatch(a, b) {
  return {
    ...a,
    ...b,
    ...(a.positions || b.positions
      ? { positions: { ...a.positions, ...b.positions } }
      : {}),
  };
}
function changedPoints(before, after) {
  return Object.fromEntries(
    Object.entries(after).filter(
      ([id, p]) => !before[id] || p.x !== before[id].x || p.y !== before[id].y,
    ),
  );
}
const point = (event, el) => {
  const r = el.getBoundingClientRect();
  return { x: event.clientX - r.left, y: event.clientY - r.top };
};

export default function ResearchGraph({
  nodes,
  rootId,
  selected,
  onSelect,
  statusOf,
  api,
  base,
  expanded,
  onExpand,
}) {
  const url = base + "/" + rootId + "/layout";
  const [data, setData] = useState(initial),
    [ready, setReady] = useState(false),
    [loadError, setLoadError] = useState("");
  const dataRef = useRef(data),
    nodesRef = useRef(nodes),
    selectedRef = useRef(selected),
    alive = useRef(true);
  nodesRef.current = nodes;
  selectedRef.current = selected;
  const canvas = useRef(null),
    gesture = useRef(null),
    space = useRef(false);
  const [dragging, setDragging] = useState(""),
    [spaceHeld, setSpaceHeld] = useState(false),
    [picked, setPicked] = useState(new Set([selected]));
  const pickedRef = useRef(picked),
    [marquee, setMarquee] = useState(null);
  const history = useRef({ undo: [], redo: [] }),
    [historyVersion, setHistoryVersion] = useState(0);
  const pending = useRef({}),
    saving = useRef(false),
    timer = useRef(null),
    [saveState, setSaveState] = useState("saved"),
    [saveError, setSaveError] = useState("");
  const loaded = useRef(false),
    lastSelected = useRef(selected);
  const visible = useMemo(
    () => visibleNodes(nodes, new Set(data.collapsed)),
    [nodes, data.collapsed],
  );
  const visibleIds = new Set(visible.map((n) => n.id));
  const parentsWithChildren = useMemo(
    () => new Set(nodes.map((n) => n.parent_id)),
    [nodes],
  );
  const box = useMemo(
    () => bounds(visible, data.positions),
    [visible, data.positions],
  );
  const structure = nodes.map((n) => n.id + ":" + n.parent_id).join("|");

  function paint(next) {
    dataRef.current = next;
    if (alive.current) setData(next);
  }
  function pick(ids) {
    pickedRef.current = ids;
    setPicked(ids);
  }
  function size() {
    return {
      width: canvas.current?.clientWidth || 600,
      height: canvas.current?.clientHeight || 400,
    };
  }
  async function flush() {
    clearTimeout(timer.current);
    if (saving.current || !Object.keys(pending.current).length) return;
    const patch = pending.current;
    pending.current = {};
    saving.current = true;
    if (alive.current) setSaveState("saving");
    let failed = false;
    try {
      await api(url, {
        method: "PATCH",
        body: patch,
        keepalive: JSON.stringify(patch).length < 55000,
      });
      if (alive.current) {
        setSaveError("");
        setSaveState(Object.keys(pending.current).length ? "saving" : "saved");
      }
    } catch (e) {
      failed = true;
      pending.current = mergePatch(patch, pending.current);
      if (alive.current) {
        setSaveState("error");
        setSaveError(e.message);
      }
    } finally {
      saving.current = false;
      if (!failed && Object.keys(pending.current).length) flush();
    }
  }
  function queue(patch) {
    pending.current = mergePatch(pending.current, patch);
    setSaveState("saving");
    clearTimeout(timer.current);
    timer.current = setTimeout(flush, 350);
  }
  function change(patch, save = true) {
    paint({ ...dataRef.current, ...patch });
    if (save) queue(patch);
  }
  async function load() {
    setLoadError("");
    try {
      const saved = await api(url);
      if (!alive.current) return;
      const positions = addMissingPositions(
        nodesRef.current,
        rootId,
        saved.positions,
      );
      const camera =
        saved.camera ||
        fitCamera(
          bounds(
            visibleNodes(nodesRef.current, new Set(saved.collapsed)),
            positions,
          ),
          size(),
        );
      paint({ ...initial, ...saved, positions, camera });
      loaded.current = true;
      setReady(true);
      const missing = changedPoints(saved.positions, positions);
      if (Object.keys(missing).length || !saved.camera)
        queue({
          ...(!saved.camera ? { camera } : {}),
          ...(Object.keys(missing).length ? { positions: missing } : {}),
        });
    } catch (e) {
      if (alive.current) setLoadError(e.message);
    }
  }
  useEffect(() => {
    alive.current = true;
    load();
    const keyup = (e) => {
      if (e.code === "Space") {
        space.current = false;
        setSpaceHeld(false);
      }
    };
    const blur = () => {
      space.current = false;
      setSpaceHeld(false);
      finish(null, true);
    };
    const unload = () => flush();
    window.addEventListener("keyup", keyup);
    window.addEventListener("blur", blur);
    window.addEventListener("pagehide", unload);
    return () => {
      alive.current = false;
      clearTimeout(timer.current);
      flush();
      window.removeEventListener("keyup", keyup);
      window.removeEventListener("blur", blur);
      window.removeEventListener("pagehide", unload);
    };
  }, [url]);
  useEffect(() => {
    if (!loaded.current) return;
    const positions = addMissingPositions(
        nodesRef.current,
        rootId,
        dataRef.current.positions,
      ),
      missing = changedPoints(dataRef.current.positions, positions);
    if (Object.keys(missing).length) {
      paint({ ...dataRef.current, positions });
      queue({ positions: missing });
    }
  }, [structure, ready]);
  useEffect(() => {
    if (!ready || selected === lastSelected.current) return;
    lastSelected.current = selected;
    pick(new Set([selected]));
    reveal(selected, true);
  }, [selected, ready]);
  useEffect(() => {
    const el = canvas.current;
    if (!el) return;
    const wheel = (e) => {
      e.preventDefault();
      if (!loaded.current || gesture.current) return;
      const d = dataRef.current,
        scale =
          e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? el.clientHeight : 1;
      if (
        e.ctrlKey ||
        e.metaKey ||
        (!e.altKey && !e.shiftKey && d.wheel_zoom && Math.abs(e.deltaX) < 1)
      ) {
        change({
          camera: zoomAt(
            d.camera,
            point(e, el),
            d.camera.zoom * Math.exp(-e.deltaY * scale * 0.0015),
          ),
        });
      } else {
        change({
          camera: {
            ...d.camera,
            x: clamp(
              d.camera.x - (e.shiftKey ? e.deltaY : e.deltaX) * scale,
              -1e7,
              1e7,
            ),
            y: clamp(
              d.camera.y - (e.shiftKey ? 0 : e.deltaY) * scale,
              -1e7,
              1e7,
            ),
          },
        });
      }
    };
    el.addEventListener("wheel", wheel, { passive: false });
    return () => el.removeEventListener("wheel", wheel);
  }, []);

  function remember(before, after) {
    const changed = changedPoints(before, after);
    if (!Object.keys(changed).length) return;
    history.current.undo.push({
      before: Object.fromEntries(
        Object.keys(changed).map((id) => [id, before[id]]),
      ),
      after: changed,
    });
    if (history.current.undo.length > 100) history.current.undo.shift();
    history.current.redo = [];
    setHistoryVersion((v) => v + 1);
    queue({ positions: changed });
  }
  function historyMove(redo = false) {
    if (gesture.current) return;
    const h = history.current,
      from = redo ? h.redo : h.undo,
      to = redo ? h.undo : h.redo,
      entry = from.pop();
    if (!entry) return;
    const patch = redo ? entry.after : entry.before;
    paint({
      ...dataRef.current,
      positions: { ...dataRef.current.positions, ...patch },
    });
    to.push(entry);
    setHistoryVersion((v) => v + 1);
    queue({ positions: patch });
  }
  function fit() {
    if (ready)
      change({
        camera: fitCamera(
          bounds(
            visibleNodes(nodesRef.current, new Set(dataRef.current.collapsed)),
            dataRef.current.positions,
          ),
          size(),
        ),
      });
  }
  function zoom(factor) {
    const s = size();
    change({
      camera: zoomAt(
        dataRef.current.camera,
        { x: s.width / 2, y: s.height / 2 },
        factor === 0 ? 1 : dataRef.current.camera.zoom * factor,
      ),
    });
  }
  function reveal(id, onlyOutside = false) {
    const ancestors = new Set(),
      map = new Map(nodesRef.current.map((n) => [n.id, n]));
    let n = map.get(id);
    while (n) {
      ancestors.add(n.id);
      n = map.get(n.parent_id);
    }
    const d = dataRef.current,
      collapsed = d.collapsed.filter((nid) => !ancestors.has(nid)),
      p = d.positions[id],
      s = size();
    const patch = {};
    if (collapsed.length !== d.collapsed.length) patch.collapsed = collapsed;
    if (p) {
      const left = p.x * d.camera.zoom + d.camera.x,
        top = p.y * d.camera.zoom + d.camera.y;
      if (
        !onlyOutside ||
        left < 0 ||
        top < 0 ||
        left + WIDTH * d.camera.zoom > s.width ||
        top + HEIGHT * d.camera.zoom > s.height
      )
        patch.camera = {
          ...d.camera,
          x: s.width / 2 - (p.x + WIDTH / 2) * d.camera.zoom,
          y: s.height / 2 - (p.y + HEIGHT / 2) * d.camera.zoom,
        };
    }
    if (Object.keys(patch).length) change(patch);
  }
  function arrange() {
    const before = dataRef.current.positions,
      after = autoLayout(nodesRef.current, rootId);
    paint({ ...dataRef.current, positions: after });
    remember(before, after);
    fit();
  }
  function toggleCollapse(id) {
    const collapsed = new Set(dataRef.current.collapsed);
    collapsed.has(id) ? collapsed.delete(id) : collapsed.add(id);
    change({ collapsed: [...collapsed] });
  }

  function down(e) {
    if (
      !ready ||
      gesture.current ||
      e.target.closest("[data-graph-control]") ||
      ![0, 1, 2].includes(e.button)
    )
      return;
    e.preventDefault();
    const el = canvas.current;
    el.focus({ preventScroll: true });
    el.setPointerCapture(e.pointerId);
    const node = e.target.closest("[data-node-id]")?.dataset.nodeId,
      local = point(e, el),
      d = dataRef.current;
    const isPan =
      e.button !== 0 ||
      space.current ||
      d.mode === "hand" ||
      (!node && !e.shiftKey);
    const g = {
      pointer: e.pointerId,
      start: local,
      camera: { ...d.camera },
      node,
      beforePicked: new Set(pickedRef.current),
      moved: false,
      modifier: e.shiftKey || e.ctrlKey || e.metaKey,
    };
    if (isPan) {
      g.type = "pan";
    } else if (node) {
      g.type = "nodes";
      const ids = g.modifier
        ? new Set([...pickedRef.current, node])
        : pickedRef.current.has(node)
          ? new Set(pickedRef.current)
          : new Set([node]);
      pick(ids);
      const moving = d.move_subtree ? descendants(nodesRef.current, ids) : ids;
      g.before = Object.fromEntries(
        [...moving]
          .filter((id) => d.positions[id])
          .map((id) => [id, { ...d.positions[id] }]),
      );
      g.anchor = d.positions[node];
    } else {
      g.type = "marquee";
      g.world = worldPoint(local, d.camera);
      setMarquee({ x: g.world.x, y: g.world.y, width: 0, height: 0 });
    }
    gesture.current = g;
    setDragging(g.type);
  }
  function move(e) {
    const g = gesture.current;
    if (!g || g.pointer !== e.pointerId) return;
    const local = point(e, canvas.current),
      dx = local.x - g.start.x,
      dy = local.y - g.start.y;
    if (!g.moved && Math.hypot(dx, dy) < 4) return;
    g.moved = true;
    const d = dataRef.current;
    if (g.type === "pan")
      paint({
        ...d,
        camera: {
          ...g.camera,
          x: clamp(g.camera.x + dx, -1e7, 1e7),
          y: clamp(g.camera.y + dy, -1e7, 1e7),
        },
      });
    else if (g.type === "nodes") {
      let wx = dx / g.camera.zoom,
        wy = dy / g.camera.zoom;
      if (d.snap && !e.altKey) {
        wx = Math.round((g.anchor.x + wx) / 10) * 10 - g.anchor.x;
        wy = Math.round((g.anchor.y + wy) / 10) * 10 - g.anchor.y;
      }
      const moved = Object.fromEntries(
        Object.entries(g.before).map(([id, p]) => [
          id,
          { x: clamp(p.x + wx, -1e6, 1e6), y: clamp(p.y + wy, -1e6, 1e6) },
        ]),
      );
      paint({ ...d, positions: { ...d.positions, ...moved } });
    } else {
      const end = worldPoint(local, g.camera),
        r = {
          x: Math.min(g.world.x, end.x),
          y: Math.min(g.world.y, end.y),
          width: Math.abs(end.x - g.world.x),
          height: Math.abs(end.y - g.world.y),
        };
      setMarquee(r);
      const ids = visibleNodes(nodesRef.current, new Set(d.collapsed))
        .filter((n) => {
          const p = d.positions[n.id];
          return (
            p &&
            p.x >= r.x &&
            p.y >= r.y &&
            p.x + WIDTH <= r.x + r.width &&
            p.y + HEIGHT <= r.y + r.height
          );
        })
        .map((n) => n.id);
      pick(new Set([...g.beforePicked, ...ids]));
    }
  }
  function finish(e, cancel = false) {
    const g = gesture.current;
    if (!g || (e && e.pointerId !== g.pointer)) return;
    gesture.current = null;
    setDragging("");
    setMarquee(null);
    if (canvas.current?.hasPointerCapture(g.pointer))
      canvas.current.releasePointerCapture(g.pointer);
    if (cancel) {
      if (g.type === "nodes")
        paint({
          ...dataRef.current,
          positions: { ...dataRef.current.positions, ...g.before },
        });
      if (g.type === "pan") paint({ ...dataRef.current, camera: g.camera });
      pick(g.beforePicked);
      return;
    }
    if (g.moved) {
      if (g.type === "nodes")
        remember(
          g.before,
          Object.fromEntries(
            Object.keys(g.before).map((id) => [
              id,
              dataRef.current.positions[id],
            ]),
          ),
        );
      else if (g.type === "pan") queue({ camera: dataRef.current.camera });
    } else if (g.type === "nodes") {
      if (g.modifier) {
        const ids = new Set(g.beforePicked);
        ids.has(g.node) ? ids.delete(g.node) : ids.add(g.node);
        pick(ids);
      } else {
        pick(new Set([g.node]));
        onSelect(g.node);
      }
    } else if (
      g.type === "pan" &&
      !g.node &&
      !space.current &&
      dataRef.current.mode !== "hand"
    )
      pick(new Set());
  }
  function key(e) {
    if (!ready || e.target.closest("input,textarea,select,summary")) return;
    const ctrl = e.ctrlKey || e.metaKey;
    if (e.code === "Space") {
      e.preventDefault();
      space.current = true;
      setSpaceHeld(true);
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      if (gesture.current) finish(null, true);
      else pick(new Set());
      return;
    }
    if (gesture.current) return;
    if (ctrl && e.key.toLowerCase() === "z") {
      e.preventDefault();
      historyMove(e.shiftKey);
      return;
    }
    if (ctrl && e.key.toLowerCase() === "y") {
      e.preventDefault();
      historyMove(true);
      return;
    }
    if (ctrl && e.key.toLowerCase() === "a") {
      e.preventDefault();
      pick(
        new Set(
          visibleNodes(
            nodesRef.current,
            new Set(dataRef.current.collapsed),
          ).map((n) => n.id),
        ),
      );
      return;
    }
    if (ctrl || e.altKey) return;
    if (e.key.startsWith("Arrow") && pickedRef.current.size) {
      e.preventDefault();
      const d = dataRef.current,
        step = e.shiftKey ? 10 : 1,
        dx = e.key === "ArrowLeft" ? -step : e.key === "ArrowRight" ? step : 0,
        dy = e.key === "ArrowUp" ? -step : e.key === "ArrowDown" ? step : 0;
      const ids = d.move_subtree
        ? descendants(nodesRef.current, pickedRef.current)
        : pickedRef.current;
      const before = Object.fromEntries(
          [...ids]
            .filter((id) => d.positions[id])
            .map((id) => [id, d.positions[id]]),
        ),
        after = Object.fromEntries(
          Object.entries(before).map(([id, p]) => [
            id,
            { x: clamp(p.x + dx, -1e6, 1e6), y: clamp(p.y + dy, -1e6, 1e6) },
          ]),
        );
      paint({ ...d, positions: { ...d.positions, ...after } });
      remember(before, after);
      return;
    }
    const k = e.key.toLowerCase();
    if (k === "f") {
      e.preventDefault();
      fit();
    } else if (k === "c") {
      e.preventDefault();
      reveal(selectedRef.current);
    } else if (k === "0") {
      e.preventDefault();
      zoom(0);
    } else if (k === "+" || k === "=") {
      e.preventDefault();
      zoom(1.2);
    } else if (k === "-") {
      e.preventDefault();
      zoom(1 / 1.2);
    } else if (k === "h" || k === "v") {
      e.preventDefault();
      change({ mode: k === "h" ? "hand" : "select" });
    }
  }
  const miniBox = {
    x: box.x - 80,
    y: box.y - 80,
    width: box.width + 160,
    height: box.height + 160,
  };
  function miniMove(e) {
    const r = e.currentTarget.getBoundingClientRect(),
      s = size(),
      d = dataRef.current;
    change({
      camera: {
        ...d.camera,
        x:
          s.width / 2 -
          (miniBox.x + ((e.clientX - r.left) / r.width) * miniBox.width) *
            d.camera.zoom,
        y:
          s.height / 2 -
          (miniBox.y + ((e.clientY - r.top) / r.height) * miniBox.height) *
            d.camera.zoom,
      },
    });
  }

  return (
    <div className="graph-editor" data-history={historyVersion}>
      <div className="graph-tools">
        <div className="graph-tool-group">
          <button
            aria-label="Chọn và kéo node (V)"
            aria-pressed={data.mode === "select"}
            disabled={!ready}
            onClick={() => change({ mode: "select" })}
          >
            <MousePointer2 size={15} />
          </button>
          <button
            aria-label="Bàn tay: kéo khung nhìn (H)"
            aria-pressed={data.mode === "hand"}
            disabled={!ready}
            onClick={() => change({ mode: "hand" })}
          >
            <Hand size={15} />
          </button>
        </div>
        <div className="graph-tool-group">
          <button
            title="Hoàn tác bố cục (Ctrl+Z)"
            disabled={!history.current.undo.length}
            onClick={() => historyMove()}
          >
            <Undo2 size={15} />
          </button>
          <button
            title="Làm lại (Ctrl+Shift+Z)"
            disabled={!history.current.redo.length}
            onClick={() => historyMove(true)}
          >
            <Redo2 size={15} />
          </button>
          <button
            title="Tự sắp xếp cây (có thể hoàn tác)"
            disabled={!ready}
            onClick={arrange}
          >
            <LayoutList size={15} />
          </button>
        </div>
        <label>
          <input
            type="checkbox"
            checked={data.snap}
            disabled={!ready}
            onChange={(e) => change({ snap: e.target.checked })}
          />
          Bám lưới
        </label>
        <label>
          <input
            type="checkbox"
            checked={data.move_subtree}
            disabled={!ready}
            onChange={(e) => change({ move_subtree: e.target.checked })}
          />
          Kéo cả nhánh
        </label>
        <button
          title={expanded ? "Hiện khung đọc" : "Mở rộng đồ thị"}
          onClick={onExpand}
        >
          <Expand size={15} />
        </button>
        <details className="graph-help">
          <summary title="Hướng dẫn thao tác">
            <HelpCircle size={16} />
          </summary>
          <div>
            <strong>Di chuyển như một trình vẽ sơ đồ</strong>
            <p>Kéo node: đổi vị trí. Bấm node: đọc câu trả lời.</p>
            <p>
              Space + kéo / chuột giữa / chuột phải / kéo nền: di chuyển khung
              nhìn.
            </p>
            <p>
              Shift hoặc Ctrl + bấm: chọn nhiều. Shift + kéo nền: khoanh vùng.
              Kéo một node đã chọn để di chuyển cả nhóm.
            </p>
            <p>
              Lăn chuột: zoom quanh con trỏ. Alt + lăn: di chuyển; Shift + lăn:
              ngang. Tắt “Lăn để zoom” nếu dùng trackpad để cuộn.
            </p>
            <p>
              Giữ Alt khi kéo để bỏ bám lưới. Phím mũi tên: dịch 1 px; Shift: 10
              px.
            </p>
            <p>
              Ctrl+Z / Ctrl+Shift+Z: hoàn tác/làm lại vị trí. Esc: hủy thao tác
              đang kéo. F: vừa khung; C: tới node đang đọc; 0: zoom 100%.
            </p>
            <small>
              Kéo chỉ đổi bố cục; quan hệ cha–con và nội dung được giữ nguyên.
            </small>
          </div>
        </details>
      </div>
      <div
        className={`research-map graph-canvas ${data.mode === "hand" || spaceHeld ? "hand-mode" : ""} ${dragging ? "is-dragging " + dragging : ""}`}
        ref={canvas}
        tabIndex={0}
        aria-label="Đồ thị nghiên cứu tương tác"
        onPointerDown={down}
        onPointerMove={move}
        onPointerUp={(e) => finish(e)}
        onPointerCancel={(e) => finish(e, true)}
        onLostPointerCapture={(e) => finish(e, true)}
        onKeyDown={key}
        onContextMenu={(e) => e.preventDefault()}
        onDragStart={(e) => e.preventDefault()}
        style={{
          backgroundSize: `${20 * data.camera.zoom}px ${20 * data.camera.zoom}px`,
          backgroundPosition: `${data.camera.x}px ${data.camera.y}px`,
        }}
      >
        {!ready ? (
          <div className="graph-loading">
            {loadError ? (
              <>
                <p>{loadError}</p>
                <button onClick={load} data-graph-control>
                  Thử tải lại bố cục
                </button>
              </>
            ) : (
              <>
                <LoaderCircle className="spin" size={22} />
                Đang tải bố cục…
              </>
            )}
          </div>
        ) : (
          <>
            <div
              className="graph-world"
              data-camera={JSON.stringify(data.camera)}
              style={{
                transform: `translate(${data.camera.x}px, ${data.camera.y}px) scale(${data.camera.zoom})`,
              }}
            >
              <svg
                className="research-edges"
                width="1"
                height="1"
                aria-hidden="true"
              >
                {visible
                  .filter(
                    (n) =>
                      n.parent_id &&
                      visibleIds.has(n.parent_id) &&
                      data.positions[n.id] &&
                      data.positions[n.parent_id],
                  )
                  .map((n) => (
                    <path
                      key={n.id}
                      d={edgePath(
                        data.positions[n.parent_id],
                        data.positions[n.id],
                      )}
                      className={n.understood ? "understood" : ""}
                    />
                  ))}
              </svg>
              {visible
                .filter((n) => data.positions[n.id])
                .map((n) => (
                  <div
                    key={n.id}
                    className="research-node-wrap"
                    data-node-id={n.id}
                    data-x={data.positions[n.id].x}
                    data-y={data.positions[n.id].y}
                    style={{
                      left: data.positions[n.id].x,
                      top: data.positions[n.id].y,
                      width: WIDTH,
                      height: HEIGHT,
                      zIndex: selected === n.id ? 4 : picked.has(n.id) ? 3 : 1,
                    }}
                  >
                    <button
                      aria-label={"Đọc: " + n.title}
                      title={n.question}
                      className={`research-node ${selected === n.id ? "selected" : ""} ${picked.has(n.id) ? "picked" : ""} ${!n.parent_id ? "root" : ""} ${n.understood ? "understood" : ""}`}
                      onFocus={() => reveal(n.id, true)}
                      onClick={(e) => {
                        if (e.detail === 0) {
                          pick(new Set([n.id]));
                          onSelect(n.id);
                        }
                      }}
                    >
                      <strong>{n.title}</strong>
                      <span className={"research-status " + n.status}>
                        {n.understood ? (
                          <Check size={12} />
                        ) : n.status === "working" ? (
                          <LoaderCircle className="spin" size={12} />
                        ) : (
                          <span className="research-status-dot" />
                        )}
                        {statusOf(n)}
                      </span>
                    </button>
                    {parentsWithChildren.has(n.id) && (
                      <button
                        data-graph-control
                        className="research-collapse"
                        aria-label={
                          (data.collapsed.includes(n.id) ? "Mở" : "Thu") +
                          " nhánh " +
                          n.title
                        }
                        onClick={() => toggleCollapse(n.id)}
                      >
                        {data.collapsed.includes(n.id) ? (
                          <ChevronRight size={12} />
                        ) : (
                          <ChevronDown size={12} />
                        )}
                      </button>
                    )}
                  </div>
                ))}
              {marquee && (
                <div
                  className="graph-marquee"
                  style={{
                    left: marquee.x,
                    top: marquee.y,
                    width: marquee.width,
                    height: marquee.height,
                  }}
                />
              )}
            </div>
            <svg
              data-graph-control
              className="graph-minimap"
              aria-label="Bản đồ thu nhỏ"
              viewBox={`${miniBox.x} ${miniBox.y} ${miniBox.width} ${miniBox.height}`}
              preserveAspectRatio="none"
              onPointerDown={(e) => {
                e.stopPropagation();
                e.currentTarget.setPointerCapture(e.pointerId);
                miniMove(e);
              }}
              onPointerMove={(e) => {
                if (e.currentTarget.hasPointerCapture(e.pointerId)) miniMove(e);
              }}
              onPointerUp={(e) => {
                e.currentTarget.releasePointerCapture(e.pointerId);
                flush();
              }}
            >
              {visible
                .filter((n) => data.positions[n.id])
                .map((n) => (
                  <rect
                    key={n.id}
                    x={data.positions[n.id].x}
                    y={data.positions[n.id].y}
                    width={WIDTH}
                    height={HEIGHT}
                    fill={
                      picked.has(n.id)
                        ? "#b7b5ff"
                        : n.understood
                          ? "#66af90"
                          : "#768499"
                    }
                  />
                ))}
              <rect
                className="minimap-viewport"
                x={-data.camera.x / data.camera.zoom}
                y={-data.camera.y / data.camera.zoom}
                width={size().width / data.camera.zoom}
                height={size().height / data.camera.zoom}
              />
            </svg>
          </>
        )}
      </div>
      <div className="graph-footer">
        <span className={"graph-save " + saveState} title={saveError}>
          {!ready ? (
            "Đang tải bố cục…"
          ) : saveState === "error" ? (
            <button onClick={flush}>Chưa lưu · Thử lại</button>
          ) : saveState === "saving" ? (
            "Đang lưu…"
          ) : (
            <>
              <Check size={12} />
              Đã lưu DB
            </>
          )}
        </span>
        <span className="graph-picked">{picked.size} node chọn</span>
        <label title="Tắt để cuộn bằng trackpad; Ctrl + lăn vẫn zoom">
          <input
            type="checkbox"
            checked={data.wheel_zoom}
            disabled={!ready}
            onChange={(e) => change({ wheel_zoom: e.target.checked })}
          />
          Lăn để zoom
        </label>
        <div className="graph-zoom">
          <button
            title="Thu nhỏ"
            disabled={!ready}
            onClick={() => zoom(1 / 1.2)}
          >
            <Minus size={14} />
          </button>
          <button
            title="Zoom 100% (0)"
            disabled={!ready}
            onClick={() => zoom(0)}
          >
            {Math.round(data.camera.zoom * 100)}%
          </button>
          <button title="Phóng to" disabled={!ready} onClick={() => zoom(1.2)}>
            <Plus size={14} />
          </button>
          <button title="Vừa khung" disabled={!ready} onClick={fit}>
            <Maximize size={14} />
          </button>
          <button
            title="Tới node đang đọc"
            disabled={!ready}
            onClick={() => reveal(selectedRef.current)}
          >
            <LocateFixed size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}

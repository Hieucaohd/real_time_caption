import React, { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import RealtimeChat from "./RealtimeChat";

export default function RealtimePopup({ cid, api, post }) {
  const [meta, setMeta] = useState(null), [opacity, setOpacity] = useState(null), [error, setError] = useState('');
  const [fontSize, setFontSize] = useState(null);
  const savedFont = useRef(null);
  const saved = useRef(null), writes = useRef(Promise.resolve());
  const base = `/conversations/${cid}/realtime-popup`;
  useEffect(() => {
    let alive = true;
    api(base).then(value => {
      if (!alive) return;
      setMeta(value); saved.current = value.opacity; setOpacity(value.opacity);
      savedFont.current = value.font_size ?? 16; setFontSize(savedFont.current);
      document.title = 'Chat realtime · ' + value.title;
      document.documentElement.dataset.theme = value.theme === 'dark' ? 'dark' : 'light';
    }).catch(e => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [cid, api]);
  useEffect(() => {
    if (opacity === null || opacity === saved.current) return;
    const timer = setTimeout(() => {
      // Serialize slider writes so a slower old request cannot overwrite the last drag.
      writes.current = writes.current.then(async () => {
        try {
          await api(base, { method: 'PUT', body: { opacity } });
          saved.current = opacity; setError('');
        } catch (e) { setError(e.message); }
      });
    }, 200);
    return () => clearTimeout(timer);
  }, [opacity, api, base]);
  useEffect(() => {
    if (fontSize === null || fontSize === savedFont.current) return;
    const timer = setTimeout(() => {
      writes.current = writes.current.then(async () => {
        try {
          await api(base + '/font-size', { method: 'PUT', body: { font_size: fontSize } });
          savedFont.current = fontSize; setError('');
        } catch (e) { setError(e.message); }
      });
    }, 200);
    return () => clearTimeout(timer);
  }, [fontSize, api, base]);
  return <main className="realtime-popup" style={{ '--rt-popup-font-size': `${fontSize ?? 16}px` }}>
    <header className="rt-popup-header">
      <strong title={meta?.title}>{meta?.title || 'Chat realtime'}</strong>
      <label className="rt-font-size">Cỡ chữ
        <input aria-label="Cỡ chữ popup" type="range" min="1" max="32" step="1"
          disabled={!meta} value={fontSize ?? 16} onChange={e => setFontSize(Number(e.target.value))} />
        <span>{fontSize ?? 16}px</span>
      </label>
      <label className="rt-opacity">Độ trong suốt
        <input aria-label="Độ trong suốt popup" type="range" min="0" max="60" step="5"
          disabled={!meta?.visible} value={100 - (opacity ?? 100)} onChange={e => setOpacity(100 - Number(e.target.value))} />
        <span>{100 - (opacity ?? 100)}%</span>
      </label>
      <button title="Đóng popup (tiếp tục phiên chat)" aria-label="Đóng popup Chat realtime"
        onClick={() => api(base, { method: 'DELETE' }).catch(e => setError(e.message))}><X size={17} /></button>
    </header>
    {error && <p className="rt-error" role="alert">{error}</p>}
    {meta && !meta.visible && <p className="rt-notice">Mở lại popup bằng nút Popup Chat realtime ở cửa sổ chính để kết nối điều khiển độ trong suốt.</p>}
    <RealtimeChat key={cid} cid={cid} api={api} post={post} popup />
  </main>;
}

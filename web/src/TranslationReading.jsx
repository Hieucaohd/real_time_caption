import React, { useLayoutEffect, useMemo, useRef, useState } from "react";

export default function TranslationReading({ cid, lines, reading, capture, showRealtime, follow, source, target }) {
  const scroll = useRef(null);
  const [hovered, setHovered] = useState(null);
  const segments = reading?.segments || [];
  const memberIds = (segment) => segment.line_ids || [segment.line_id];
  const sourceGroups = useMemo(() => {
    const groups = new Map();
    for (const segment of segments) {
      const ids = memberIds(segment);
      for (const id of ids) groups.set(id, groups.has(id) ? [...new Set([...groups.get(id), ...ids])] : ids);
    }
    return groups;
  }, [segments]);
  const highlighted = hovered?.cid === cid ? hovered.ids.filter((id) => sourceGroups.has(id)) : [];
  const sentenceProps = (ids) => ids.length ? ({
    className: `translation-sentence${ids.some((id) => highlighted.includes(id)) ? " is-highlighted" : ""}`,
    tabIndex: 0,
    onMouseEnter: () => setHovered({ cid, ids }),
    onMouseLeave: () => setHovered(null),
    onFocus: () => setHovered({ cid, ids }),
    onBlur: () => setHovered(null),
  }) : { className: "translation-sentence" };
  const original = lines.map((line) => line.source_text).join(" ");
  const translated = (reading?.segments || []).map((segment) => segment.text).join(" ");
  const realtime = reading?.realtime || [];
  const liveTranslation = realtime.map((segment) => segment.text).join(" ");
  const liveSource = capture?.active && capture.conversation_id === cid ? capture.partial : "";
  useLayoutEffect(() => {
    if (!follow) return;
    const panel = scroll.current;
    if (panel) panel.scrollTop = panel.scrollHeight;
  }, [cid, original, translated, liveSource, liveTranslation, showRealtime, follow]);
  return (
    <div className={`translation-reading${showRealtime ? " with-realtime" : ""}`} aria-label="Đọc song song">
      <div className="translation-reading-head">
        <header><h3>Nguyên văn <small>{source}</small></h3><span>{lines.length} câu</span></header>
        {showRealtime && <header><h3>Dịch realtime <small>{target}</small></h3><span>{realtime.length} đoạn</span></header>}
        <header><h3>{reading?.paragraph_sync ? "Bản dịch đồng bộ" : "Dịch đã chốt"} <small>{target}</small></h3><span>{reading?.translated_lines || 0}/{lines.length} câu</span></header>
      </div>
      <div className="translation-reading-scroll scroll" ref={scroll} tabIndex={0} aria-label="Cuộn nguyên văn và bản dịch">
        <div className="translation-reading-columns">
        <section className="translation-reading-text" aria-label="Đoạn nguyên văn">
          <p lang={source}>{lines.map((line, index) => <React.Fragment key={line.id}>
            {index > 0 && " "}<span data-line-id={line.id} {...sentenceProps(sourceGroups.get(line.id) || [])}>{line.source_text}</span>
          </React.Fragment>)}{liveSource && <> <span className="translation-draft" title="Câu chưa kết thúc">{liveSource}</span></>}</p>
          {!original && !liveSource && <p className="muted">Nội dung sẽ xuất hiện khi có transcript.</p>}
        </section>
        {showRealtime && <section className="translation-reading-text" aria-label="Đoạn dịch realtime">
          <p lang={target}>{realtime.map((segment, index) => <React.Fragment key={segment.id}>
            {index > 0 && " "}<span data-realtime-id={segment.id}>{segment.text}</span>
          </React.Fragment>)}</p>
          {!liveTranslation && <p className="muted">Bật Auto translate để dịch ngay khi đang nói. Nội dung realtime được lưu riêng, không bị bản dịch đã chốt thay thế.</p>}
        </section>}
        <section className="translation-reading-text" aria-label="Đoạn bản dịch">
          <p lang={target}>{segments.map((segment, index) => <React.Fragment key={segment.line_id}>
            {index > 0 && " "}<span data-line-id={segment.line_id} {...sentenceProps(memberIds(segment))}>{segment.text}</span>
          </React.Fragment>)}</p>
          {!translated && <p className="muted">Bản dịch được thêm vào đây khi câu đã chốt và dịch xong. Bật Auto translate để dịch liên tục.</p>}
        </section>
        </div>
      </div>
    </div>
  );
}

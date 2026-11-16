import React, { useLayoutEffect, useRef } from "react";

export default function TranslationReading({ cid, lines, reading, capture, preview, follow, source, target }) {
  const left = useRef(null), right = useRef(null);
  const original = lines.map((line) => line.source_text).join(" ");
  const translated = (reading?.segments || []).map((segment) => segment.text).join(" ");
  const liveSource = capture?.active && capture.conversation_id === cid ? capture.partial : "";
  useLayoutEffect(() => {
    if (!follow) return;
    for (const panel of [left.current, right.current]) {
      if (panel) panel.scrollTop = panel.scrollHeight;
    }
  }, [cid, original, translated, liveSource, preview, follow]);
  return (
    <div className="translation-reading" aria-label="Đọc song song">
      <section className="translation-reading-column">
        <header><h3>Nguyên văn <small>{source}</small></h3><span>{lines.length} câu</span></header>
        <div className="translation-reading-text scroll" ref={left} tabIndex={0} aria-label="Đoạn nguyên văn">
          <p lang={source}>{original}{liveSource && <> <span className="translation-draft" title="Câu chưa kết thúc">{liveSource}</span></>}</p>
          {!original && !liveSource && <p className="muted">Nội dung sẽ xuất hiện khi có transcript.</p>}
        </div>
      </section>
      <section className="translation-reading-column">
        <header><h3>Bản dịch <small>{target}</small></h3><span>{reading?.translated_lines || 0}/{lines.length} câu</span></header>
        <div className="translation-reading-text scroll" ref={right} tabIndex={0} aria-label="Đoạn bản dịch">
          <p lang={target}>{translated}{preview && <> <span className="translation-draft" aria-label="Bản dịch tạm" title="Bản dịch tạm · Câu chưa kết thúc">{preview}</span></>}</p>
          {!translated && !preview && <p className="muted">Bản dịch được thêm vào đây khi hoàn tất. Bật Auto translate để dịch liên tục.</p>}
        </div>
      </section>
    </div>
  );
}

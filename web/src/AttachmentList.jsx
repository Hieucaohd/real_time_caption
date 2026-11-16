import React, { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Download, ImageOff, Paperclip, X } from "lucide-react";
import "./attachments.css";

function fileUrl(cid, path) {
  return `/api/conversations/${encodeURIComponent(cid)}/files/` + path.replaceAll("\\", "/").split("/").map(encodeURIComponent).join("/");
}

function ImageViewer({ file, url, close }) {
  const dialog = useRef(null);
  const [actual, setActual] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const previous = document.activeElement;
    const element = dialog.current;
    element.showModal();
    return () => {
      element.close();
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, []);
  return createPortal(<dialog ref={dialog} className="image-viewer" aria-label="Xem ảnh đính kèm"
    onCancel={(e) => { e.preventDefault(); close(); }}
    onClick={(e) => { e.stopPropagation(); if (e.target === e.currentTarget) close(); }}>
    <header className="image-viewer-header">
      <strong title={file.name}>{file.name}</strong>
      <button type="button" disabled={failed} onClick={() => setActual((value) => !value)}>
        {actual ? "Vừa khung" : "Kích thước gốc"}
      </button>
      <a href={url} download={file.name} aria-label="Tải ảnh xuống"><Download size={18} /></a>
      <button type="button" onClick={close} aria-label="Đóng xem ảnh"><X size={20} /></button>
    </header>
    <div className={`image-viewer-stage${actual ? " actual-size" : ""}`}>
      {failed ? <p>Không hiển thị được ảnh này. Bạn có thể tải file xuống để xem.</p>
        : <img src={url} alt={file.name} onError={() => setFailed(true)} />}
    </div>
  </dialog>, document.body);
}

function ImageAttachment({ file, url, onOpen, onRemove, disabled }) {
  const [failed, setFailed] = useState(false);
  return <div className="attachment-card">
    <button type="button" className="attachment-preview" onClick={onOpen} aria-label={"Xem ảnh " + file.name}>
      {failed ? <span className="attachment-preview-error"><ImageOff size={24} />Không tải được ảnh</span>
        : <img src={url} alt={file.name} loading="lazy" onError={() => setFailed(true)} />}
    </button>
    <span className="attachment-name" title={file.name}>{file.name}</span>
    {onRemove && <button type="button" className="attachment-remove" aria-label={"Bỏ " + file.name}
      disabled={disabled} onClick={onRemove}><X size={14} /></button>}
  </div>;
}

export default function AttachmentList({ files, cid, onRemove, disabled = false, className = "composer-files" }) {
  const [preview, setPreview] = useState(null);
  const shown = preview && cid === preview.cid && files.find((file) => file.path === preview.path);
  return <>
    <div className={className}>
      {files.map((file) => {
        const url = fileUrl(cid, file.path);
        const image = file.type?.startsWith("image/") || /\.(png|jpe?g|webp|gif|bmp|avif|svg|ico)$/i.test(file.path);
        return image ? <ImageAttachment key={file.path} file={file} url={url} disabled={disabled}
          onOpen={() => setPreview({ path: file.path, cid })}
          onRemove={onRemove ? () => onRemove(file) : undefined} />
          : <span className="attachment-file" key={file.path}>
            {onRemove ? <><Paperclip size={14} />{file.name}</> : <a href={url} download><Paperclip size={14} />{file.name}</a>}
            {onRemove && <button type="button" aria-label={"Bỏ " + file.name} disabled={disabled}
              onClick={() => onRemove(file)}><X size={13} /></button>}
          </span>;
      })}
    </div>
    {shown && <ImageViewer key={cid + shown.path} file={shown} url={fileUrl(cid, shown.path)} close={() => setPreview(null)} />}
  </>;
}

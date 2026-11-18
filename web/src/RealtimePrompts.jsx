import React, { useEffect, useState } from "react";
import { Pencil, X } from "lucide-react";

const labels = { actions: "Gợi ý hành động", summary: "Tóm tắt" };

export default function RealtimePrompts({ config, onChange, api, post, disabled }) {
  const [templates, setTemplates] = useState(null), [error, setError] = useState("");
  const [editor, setEditor] = useState(null), [saving, setSaving] = useState(false);
  useEffect(() => {
    let alive = true;
    api('/realtime-prompts').then(value => { if (alive) setTemplates(value); })
      .catch(e => { if (alive) setError(e.message); });
    return () => { alive = false; };
  }, [api]);
  useEffect(() => { if (disabled) setEditor(null); }, [disabled]);
  function apply(kind, value) {
    onChange({ [kind + '_template']: value.name, [kind + '_prompt']: value.text });
  }
  async function save(overwrite) {
    setSaving(true); setError("");
    try {
      const value = await post('/realtime-prompts/' + editor.kind,
        { name: overwrite ? editor.original : editor.name, text: editor.text, overwrite });
      setTemplates(old => ({ ...old, [editor.kind]: [...old[editor.kind].filter(p => p.name !== value.name), value] }));
      apply(editor.kind, value); setEditor(null);
    } catch (e) { setError(e.message); }
    finally { setSaving(false); }
  }
  return <>
    <fieldset className="options chat-options rt-prompt-options" disabled={disabled || !templates}>
      {Object.entries(labels).map(([kind, label]) => {
        const selected = config[kind + '_template'] || '';
        const choices = templates?.[kind] || [];
        return <label className="field" key={kind}>
          <span>Prompt · {label}</span>
          <div className="inline">
            <select aria-label={'Prompt ' + label} value={selected} onChange={e => {
              const value = choices.find(p => p.name === e.target.value);
              if (value) apply(kind, value);
            }}>
              {choices.map(value => <option key={value.name} value={value.name}>{value.name || 'Mặc định'}</option>)}
              {selected && !choices.some(p => p.name === selected) && <option value={selected}>{selected} (đã lưu trong hội thoại)</option>}
            </select>
            <button type="button" aria-label={'Sửa prompt ' + label} title={'Sửa prompt ' + label}
              onClick={() => { setError(''); setEditor({ kind, name: selected, original: selected, text: config[kind + '_prompt'] || '' }); }}><Pencil size={16} /></button>
          </div>
        </label>;
      })}
      {!templates && <small>{error || 'Đang tải mẫu prompt…'}</small>}
    </fieldset>
    <p className="muted rt-help">Mẫu gợi ý cần trả JSON gồm actions, mỗi phần tử có label và prompt. Transcript mới được app tự thêm vào cuối. Bấm Lưu cài đặt để lưu lựa chọn cho cuộc hội thoại.</p>
    {editor && <div className="modal-backdrop">
      <section className="modal" role="dialog" aria-modal="true" aria-label={'Sửa prompt ' + labels[editor.kind]}>
        <div className="modal-head"><h2>Prompt · {labels[editor.kind]}</h2>
          <button disabled={saving} aria-label="Đóng sửa prompt realtime" onClick={() => setEditor(null)}><X size={18} /></button></div>
        <div className="modal-body">
          <label className="field"><span>Tên mẫu</span><input aria-label="Tên mẫu realtime" disabled={saving} value={editor.name}
            onChange={e => setEditor(old => ({ ...old, name: e.target.value }))} /></label>
          <textarea className="prompt-editor" aria-label="Nội dung prompt realtime" disabled={saving} value={editor.text}
            onChange={e => setEditor(old => ({ ...old, text: e.target.value }))} />
          <p className="muted">Nội dung được dùng nguyên văn; không cần chèn transcript hay biến thay thế. Lưu mẫu dùng chung không thay đổi prompt đã lưu ở các cuộc hội thoại khác.</p>
          {error && <p className="alert" role="alert">{error}</p>}
        </div>
        <div className="modal-foot">
          <button disabled={saving || !editor.original || !templates[editor.kind].some(p => p.name === editor.original) || !editor.text.trim()}
            onClick={() => save(true)}>Cập nhật mẫu</button>
          <button className="primary" disabled={saving || !editor.name.trim() || !editor.text.trim()}
            onClick={() => save(false)}>Lưu thành mẫu mới</button>
        </div>
      </section>
    </div>}
  </>;
}

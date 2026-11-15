import React, { useState, useEffect } from "react";
import { X } from "lucide-react";

function Button({ children, icon: Icon, className = "", ...props }) {
  return (
    <button className={className} {...props}>
      {Icon && <Icon size={16} />} {children}
    </button>
  );
}
function Field({ label, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}
export default function PromptModal({
  kind,
  selected,
  close,
  done,
  api,
  post,
}) {
  const [text, setText] = useState(""),
    [name, setName] = useState(""),
    [loaded, setLoaded] = useState(""),
    [error, setError] = useState(""),
    [saving, setSaving] = useState(false);
  useEffect(() => {
    api("/prompts/" + kind + "?name=" + encodeURIComponent(selected || ""))
      .then((p) => {
        setText(p.text);
        setName(p.name || "");
        setLoaded(p.name || "");
      })
      .catch((e) => setError(e.message));
  }, [kind, selected]);
  async function save(overwrite) {
    if (saving) return;
    setSaving(true);
    setError("");
    try {
      const value = await post("/prompts/" + kind, {
        name: overwrite ? loaded : name,
        text,
        overwrite,
      });
      await done(value.name);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <div className="modal-backdrop">
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label="Edit prompt"
      >
        <div className="modal-head">
          <h2>Edit {kind} prompt</h2>
          <Button
            icon={X}
            aria-label="Close prompt editor"
            disabled={saving}
            onClick={close}
          />
        </div>
        <div className="modal-body">
          <Field label="Tên phiên bản mới">
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <textarea
            className="prompt-editor"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <p className="muted">
            {"{message} · {file_name} · {source} · {started} · {now}"}
          </p>
          {error && <p className="alert">{error}</p>}
        </div>
        <div className="modal-foot">
          <Button
            disabled={saving || !loaded || !text.trim()}
            onClick={() => save(true)}
          >
            Update current version
          </Button>
          <Button
            className="primary"
            disabled={saving || !text.trim()}
            onClick={() => save(false)}
          >
            Save new version
          </Button>
        </div>
      </section>
    </div>
  );
}

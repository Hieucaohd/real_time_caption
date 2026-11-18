import React from "react";
import { Pencil, RefreshCw, Save } from "lucide-react";

// Chat and research deliberately share the same controls and prompt catalogue.
export default function ChatOptions({
  value,
  onChange,
  tabs,
  scanTabs,
  loadingTabs,
  prompts,
  editPrompt,
  scope = "chat",
  disabled = false,
  tabDisabled = false,
  showNewChat = true,
  onSave,
  saving = false,
  managedContext = false,
  children,
}) {
  const set = (key, next) => onChange({ [key]: next });
  return (
    <fieldset className="options chat-options" disabled={disabled}>
      {!managedContext && <label className="field">
        <span>ChatGPT tab</span>
        <div className="inline">
          <select
            aria-label={scope + " ChatGPT tab"}
            value={value.selected_tab || ""}
            disabled={tabDisabled}
            onChange={(e) => set("selected_tab", e.target.value)}
          >
            <option value="">Chọn tab Chrome…</option>
            {tabs.map((t) => (
              <option key={t.key} value={t.key}>
                {t.title}
              </option>
            ))}
            {value.selected_tab &&
              !tabs.some((t) => t.key === value.selected_tab) && (
                <option value={value.selected_tab}>
                  {value.selected_tab_title || "Tab đã lưu — Refresh để xem"}
                </option>
              )}
          </select>
          <button
            type="button"
            title="Refresh Chrome tabs"
            aria-label="Refresh Chrome tabs"
            onClick={scanTabs}
            disabled={loadingTabs}
          >
            <RefreshCw size={16} className={loadingTabs ? "spin" : ""} />
          </button>
        </div>
      </label>}
      <label className="field">
        <span>{managedContext ? "Prompt · Hỏi đáp" : "Prompt"}</span>
        <div className="inline">
          <select
            aria-label={scope + " Prompt"}
            value={value.prompt || prompts.at(-1) || ""}
            onChange={(e) => set("prompt", e.target.value)}
          >
            {prompts.map((name) => (
              <option key={name}>{name}</option>
            ))}
          </select>
          <button
            type="button"
            title="Edit prompt"
            aria-label="Edit prompt"
            onClick={editPrompt}
          >
            <Pencil size={16} />
          </button>
        </div>
      </label>
      {(managedContext ? [["power", "Power · Gợi ý / hỏi đáp"], ["summary_power", "Power · Tóm tắt"]] : [["power", "Power"]]).map(([key, label]) => <label className="field" key={key}>
        <span>{label}</span>
        <select
          aria-label={scope + (key === "power" ? " Power" : " Summary Power")}
          value={value[key] ?? value.power ?? -1}
          onChange={(e) => set(key, +e.target.value)}
        >
          {["Giữ nguyên", "Instant", "Medium", "High"].map((p, i) => (
            <option key={p} value={i - 1}>
              {p}
            </option>
          ))}
        </select>
      </label>)}
      {!managedContext && <label className="field">
        <span>Số dòng transcript (0 = tất cả)</span>
        <input
          aria-label={scope + " transcript lines"}
          type="number"
          min="0"
          max="1000000"
          value={value.max_lines ?? 300}
          onChange={(e) => set("max_lines", Math.max(0, +e.target.value))}
        />
      </label>}
      {[
        ["attach_transcript", "Đính kèm transcript"],
        ["attach_history", "Đính kèm lịch sử chat"],
        ["screenshot", "Auto screenshot"],
        ["new_chat", "New ChatGPT chat"],
      ].filter(([key]) => managedContext ? key === "screenshot" : key !== "new_chat" || showNewChat).map(([key, label]) => (
        <label className="toggle" key={key}>
          <input
            type="checkbox"
            checked={!!value[key]}
            onChange={(e) => set(key, e.target.checked)}
          />
          {label}
        </label>
      ))}
      {children}
      {onSave && <button type="button" onClick={onSave} disabled={saving || loadingTabs}>
        <Save size={16} /> {saving ? "Đang lưu…" : "Lưu cài đặt Chat"}
      </button>}
    </fieldset>
  );
}

const fields = {
  selected_tab: "chatgpt_selected_tab",
  selected_tab_title: "chatgpt_selected_tab_title",
  prompt: "chat_prompt",
  power: "chat_chatgpt_power",
  max_lines: "chat_max_lines",
  attach_transcript: "chat_attach_transcript",
  attach_history: "chat_attach_history",
  screenshot: "chat_screenshot",
  new_chat: "chat_new_chat",
};
const defaults = {
  selected_tab: "", selected_tab_title: "", prompt: "", power: -1,
  max_lines: 300, attach_transcript: true, attach_history: false,
  screenshot: true, new_chat: false,
};
export function chatPreferences(settings) {
  return Object.fromEntries(Object.entries(fields).map(([key, setting]) =>
    [key, settings[setting] ?? defaults[key]]));
}
export function savedChatPreferences(value) {
  return Object.fromEntries(Object.entries(fields).map(([key, setting]) => [setting, value[key]]));
}

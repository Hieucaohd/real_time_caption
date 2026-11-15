import React from "react";

function conversationLink(value) {
  try {
    const url = new URL(value);
    if (url.protocol === "https:" && ["chatgpt.com", "www.chatgpt.com", "chat.openai.com"].includes(url.hostname)
      && /\/c\/[^/]+\/?$/.test(url.pathname)) return url.href;
  } catch { /* Older answers have no recorded source. */ }
  return null;
}

export default function ResponseSource({ response }) {
  const url = conversationLink(response.response_conversation_url);
  return (
    <details className="response-source">
      <summary>Nguồn ChatGPT</summary>
      {url ? <a href={url} target="_blank" rel="noopener noreferrer">{url}</a>
        : <p>Chưa lưu link cuộc hội thoại cho câu trả lời này.</p>}
      <dl>
        <dt>Vị trí response (Message ID)</dt>
        <dd>{response.response_message_id || "Chưa có mã response"}</dd>
        <dt>Lượt hội thoại (Turn ID)</dt>
        <dd>{response.response_turn_id || "Chưa có mã lượt hội thoại"}</dd>
      </dl>
    </details>
  );
}

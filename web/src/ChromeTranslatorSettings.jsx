import React, { useEffect, useState } from "react";
import { chromeTranslator } from "./chromeTranslator";

const errors = {
  unsupported: "Trình duyệt này chưa hỗ trợ Translator API. Hãy mở app bằng Chrome desktop qua launch_chrome.bat.",
  languages: "Hãy chọn hai ngôn ngữ nguồn và đích khác nhau.",
  unavailable: "Chrome chưa hỗ trợ cặp ngôn ngữ này trên thiết bị.",
  prepare: "Chưa khởi tạo được mô hình. Kiểm tra kết nối tải gói ngôn ngữ và dung lượng máy, rồi thử lại.",
};

export default function ChromeTranslatorSettings({ source, target }) {
  const [, render] = useState(0);
  const [availability, setAvailability] = useState({});
  const pair = `${source}:${target}`;
  useEffect(() => chromeTranslator.subscribe(() => render((n) => n + 1)), []);
  useEffect(() => {
    let active = true;
    chromeTranslator.availability(source, target).then(
      (value) => active && setAvailability({ pair, value }),
      (error) => active && setAvailability({ pair, error: error.code || "prepare" }),
    );
    return () => { active = false; };
  }, [source, target, pair]);
  const state = chromeTranslator.state(source, target);
  const current = availability.pair === pair ? availability : {};
  const error = current.error || (current.value === "unavailable" ? "unavailable" : "") || state.code;
  const busy = state.status === "preparing";
  return <section className="card" aria-label="Chrome Translator API">
    <p>Dịch ngay trên máy, không cần API key và không tính phí theo ký tự.
      Giữ tab ứng dụng mở trong Chrome được chạy bằng launch_chrome.bat.</p>
    <p>Chọn ngôn ngữ nguồn/đích rồi khởi tạo. Lần đầu có thể cần tải gói ngôn ngữ qua Internet.</p>
    <button type="button" disabled={busy || !!current.error || current.value === "unavailable" || state.status === "ready"}
      onClick={() => chromeTranslator.prepare(source, target).catch(() => {})}>
      {busy ? "Đang khởi tạo…" : state.status === "ready" ? "Đã sẵn sàng" : "Tải / khởi tạo gói ngôn ngữ"}
    </button>
    <p role="status">{state.status === "ready" ? `Sẵn sàng dịch ${source} → ${target}. Bấm Save để lưu lựa chọn engine.`
      : busy ? `Đang chuẩn bị ${source} → ${target}: ${state.progress}%`
      : current.value === "available" ? "Gói ngôn ngữ đã có trên máy; có thể khởi tạo ngay."
      : "Sau khi khởi tạo, bấm Save rồi bật Auto translate trong tab Translation."}</p>
    {busy && <progress max="100" value={state.progress} aria-label="Tiến trình tải gói ngôn ngữ" />}
    {error && <p role="alert" className="alert">{errors[error] || errors.prepare}</p>}
    <small>Để cập nhật nhanh: đặt Số câu mỗi nhóm = 1; bật Dịch cả câu đang nói nếu cần bản dịch tạm.</small>
  </section>;
}

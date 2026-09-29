# Real-time Caption Web (standalone)

Ứng dụng này chạy bằng tiến trình và kho dữ liệu riêng trong `web_data/`. Native app không cần mở. Chat/Summary điều khiển Chrome đang đăng nhập trên **máy backend**, qua cổng remote debugging 9222.

## Chạy lần đầu

1. Chạy `setup_web.bat`.
2. Mở Chrome bằng `packaging\Open Chrome for ChatGPT.bat` nếu cần Chat/Summary.
3. Chạy `run_web.bat`. Giữ cửa sổ này mở và chép URL có `?token=...`.
4. iPhone 11 và máy backend phải chung mạng Wi-Fi. Nếu Windows Firewall hỏi, chỉ cho phép **Private networks**.

## Cài HTTPS trên iPhone

Safari chỉ cấp microphone cho website HTTPS. Chứng chỉ LAN được tạo trong `web_data/certs/`.

1. Trên iPhone, mở URL `http://<IP máy backend>:8080/ca.crt` được in trong cửa sổ server để tải chứng chỉ.
2. Vào **Settings → General → VPN & Device Management**, cài profile chứng chỉ.
3. Vào **Settings → General → About → Certificate Trust Settings**, bật tin cậy cho **Real-time Caption Web Local CA**.
4. Mở lại URL HTTPS có token, bấm **Bắt đầu microphone**, rồi chọn Allow.
5. Có thể dùng **Share → Add to Home Screen** để mở như một app.

## Phạm vi bản đầu

- Thu microphone của iPhone/desktop browser, stream PCM về máy backend và chạy Whisper tại đó.
- Tạo/continue conversation, xem transcript trực tiếp, ChatGPT Chat và Summary.
- Mọi dữ liệu web nằm trong `web_data/`, không dùng database/folder của native app.
- Một backend chỉ chạy một microphone web tại một thời điểm.

Website trên iPhone không được quyền đọc trực tiếp âm thanh nội bộ của ứng dụng khác. Để caption video/app khác ở bản web hiện tại, phát bằng loa để microphone thu lại. Capture system audio thật cần một ứng dụng iOS native dùng API screen/broadcast capture; đây là phần riêng cho giai đoạn sau.

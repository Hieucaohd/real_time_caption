# Real-time Caption Web — React + FastAPI

Bản web chạy trên chính máy Windows có native app. Chỉ **Windows Live Captions** được dùng để lấy transcript; không có Whisper, upload audio hay microphone streaming trong bản web.

## Khởi chạy

1. Cài Python 3.10+ và Node.js 22.12+.
2. Chạy `setup_web.bat` một lần để cài thư viện Python, npm và build React.
3. Chạy `launch_chrome.bat`. Script khởi động FastAPI nền tại **http://127.0.0.1:8765**, đợi server sẵn sàng rồi mở tab ứng dụng trong cùng Chrome có remote debugging **9222**. Các tab ChatGPT/Gemini/NotebookLM/Claude/Translate vẫn được mở như trước.
4. Chọn/tạo cuộc hội thoại, bấm **Start / Continue**. Mở Windows Live Captions bằng `Win + Ctrl + L`, hoàn tất cài ngôn ngữ của Windows nếu cần rồi phát âm thanh có lời nói.

`run_web.bat` cũng mở qua launcher này. Nếu chưa có bản build hoặc thiếu backend, launcher gọi setup. Chạy lại launcher sử dụng server đang chạy, không tạo thêm server. Đóng tab không dừng capture; dùng **Pause** trước khi đóng nếu muốn dừng ghi.

Port 9222 dành cho Chrome automation; giao diện/API dùng port 8765. Server chỉ bind `127.0.0.1`, không cung cấp truy cập từ máy khác trên LAN.

## Chức năng

- **Transcript:** Start/Pause/Continue, chữ tạm, lịch sử, tìm/chọn/đổi tên conversation, mở folder, copy, clear view, tải toàn bộ hoặc N dòng cuối, auto-scroll.
- **Chat:** chọn tab ChatGPT, prompt có phiên bản, Power, giới hạn transcript, đính kèm transcript/lịch sử, tự chụp màn hình, chọn màn hình/cửa sổ để chụp, upload file/ảnh và Ctrl+V ảnh. Kết quả hỗ trợ Markdown, bảng, code, công thức, copy và tải MD/DOCX/PDF. Có streaming preview, Cancel và Retry.
- **Nghiên cứu theo nhánh:** chuyển một câu trả lời Chat thành cây hỏi–đáp; bôi đen/chuột phải để hỏi sâu hơn, tạo nhiều nhánh độc lập, thu gọn, tìm node, zoom, đọc Markdown/công thức và đánh dấu đã hiểu.
- **Summary:** tab Chrome và cài đặt riêng, chọn prompt/version, bật/tắt transcript, tạo chat mới, xem lịch sử và xuất kết quả.
- **Translation:** Azure, Google API, Gemini, Google Translate web, VinAI offline; tự dịch câu đã chốt, tùy chọn dịch tạm, nhóm câu, dịch phần còn thiếu hoặc câu được chọn, Stop/Retry, lịch sử theo engine, kiểm tra cấu hình và tải model offline. VinAI cần `requirements-translation.txt`, giống native.
- **New words:** tìm ngữ cảnh trong transcript, dịch bằng ChatGPT, chọn Power, lưu vào Voca và chọn collection. Lịch sử thẻ từ được lưu chung trong SQLite và hiển thị ở hai giao diện.
- **Prompt editor / Settings:** sửa phiên bản hiện tại hoặc lưu phiên bản mới, dùng chung file với native app. API key được lưu tại `settings.json` trên máy này.
- **Caption overlay:** dùng lại overlay Windows của native app để luôn nổi trên các ứng dụng khác, kéo vị trí, chỉnh cỡ chữ, số dòng, nền trong suốt và click-through. Đây là cửa sổ Windows riêng; tab Chrome không thể tự áp dụng click-through.

Automatic screenshot chụp màn hình chính và tạm thu nhỏ cửa sổ Chrome nếu cửa sổ đang ở phía trước là tab Real-time Caption. Khi đã đính kèm ảnh, ảnh đó được ưu tiên. Nút Screenshot dùng bộ chọn màn hình/cửa sổ của Chrome để bạn chọn nội dung muốn gửi.

## Nghiên cứu theo nhánh

1. Trong **Chat**, bấm **Nghiên cứu theo nhánh** dưới một câu trả lời. Node gốc giữ nguyên câu hỏi và câu trả lời tại thời điểm chuyển. Bấm lại cùng câu trả lời sẽ mở lại cây đã có.
2. Chọn tab ChatGPT bằng thanh trên của tab nghiên cứu; dùng nút làm mới để lấy các tab Chrome 9222. Nên dùng một tab riêng vì mỗi node sẽ bắt đầu một cuộc chat mới trong tab này.
3. Bấm node để đọc bên phải. Bôi đen nội dung trong câu trả lời, chuột phải, chọn **Tạo nhánh từ đoạn đã chọn**. Hoặc bấm **Tạo nhánh con** để viết câu hỏi tự do.
4. Muốn hỏi đồng thời về bốn khái niệm, bật **Tạo nhiều nhánh**, nhập bốn câu hỏi trên bốn dòng rồi bấm **Xếp hàng 4 nhánh**. App tạo ngay bốn node; bạn vẫn có thể tạo thêm nhánh trong lúc ChatGPT đang trả lời.
5. Mỗi câu hỏi nhận đúng đường đi từ gốc đến node cha, không kèm câu hỏi/câu trả lời của nhánh anh em. App mở chat mới cho từng câu hỏi để tránh trộn ngữ cảnh. Các yêu cầu **xử lý lần lượt** trên tab Chrome đã chọn, không phát sinh bốn yêu cầu đồng thời vào cùng một tab.
6. Power và tab Chrome nằm trên thanh chung. Nút tùy chỉnh lưu mặc định riêng cho nghiên cứu: hướng dẫn cách giải thích, transcript, số dòng và tự chụp màn hình. Hộp tạo nhánh cho phép đổi riêng các lựa chọn này, upload file, dán ảnh hoặc chọn ảnh chụp. Mặc định không đính kèm transcript/ảnh; ngữ cảnh tổ tiên luôn được gửi.
7. File, transcript, hướng dẫn và Power được chụp lại khi xếp hàng; tự chụp màn hình cũng chạy ngay lúc đó, không chụp một màn hình khác sau thời gian chờ. Retry dùng lại yêu cầu này; nút thử lại cho phép dùng tab Chrome đang được chọn nếu tab cũ đã đóng.

Kéo nền để di chuyển cây; dùng +/−, vừa khung và thu gọn nhánh để điều hướng. Mỗi node có trạng thái chờ/đang trả lời/lỗi và checkbox **Đã hiểu**. Có thể đổi tên node mà không đổi câu hỏi. Tìm kiếm tìm theo câu hỏi hoặc đoạn trích.

Cây và hàng đợi được lưu trong bảng `research_nodes` của `conversations.sqlite3`: câu hỏi, câu trả lời, đoạn trích, quan hệ cha–con, tùy chỉnh từng node, yêu cầu đã chuẩn bị, trạng thái, tên node và dấu “Đã hiểu”. Nội dung đang stream và tiến độ được ghi vào database theo mỗi cập nhật nhận được, để còn đọc được phần đã nhận khi server bị ngắt. File/ảnh, bản chụp transcript và ngữ cảnh dài được lưu dạng BLOB trong `research_assets`, liên kết qua `research_node_assets`; các file cache chỉ là bản tạm để upload và tự dựng lại từ database. Node và file đính kèm được ghi trong cùng một transaction; các nhánh dùng cùng file không lưu lặp nội dung file.

Giao diện cây hiện có ở bản web. Đóng tab không hủy hàng đợi. Sau restart, các node chưa bắt đầu tiếp tục chạy; node bị ngắt khi đang gửi phải **Thử lại** thủ công để tránh gửi trùng. Hủy một node không hủy nhánh khác. Lỗi không tự gửi lại. Khi native app đang dùng ChatGPT, hàng đợi nghiên cứu chờ khóa được nhả. Khi nâng cấp, snapshot của nhánh cũ được nhập vào database nếu file cũ còn tồn tại.

## Dữ liệu và đồng bộ

Web sử dụng trực tiếp `conversations.sqlite3`, `transcripts/conversations/`, `prompts/` và `settings.json` từ `caption.paths`. Không có database hoặc bản sao transcript riêng cho web. File đính kèm nằm trong conversation; snapshots gửi ChatGPT nằm trong `cache/web-uploads/` để Retry dùng lại đúng nội dung đã chuẩn bị.

Hai app có thể cùng mở. Web cập nhật dữ liệu mỗi giây; desktop cập nhật dữ liệu bên ngoài khoảng mỗi hai giây. Khởi động lại native app sau khi cập nhật code để nhận đồng bộ và khóa liên tiến trình mới. Mỗi thời điểm chỉ một giao diện được ghi transcript; pause ở giao diện đang ghi trước khi chuyển sang giao diện kia. Rename bị chặn trong lúc capture/ChatGPT đang sử dụng file. ChatGPT request cũng được khóa giữa desktop và web để tránh thao tác cùng tab đồng thời.

Settings chỉ ghi những trường thay đổi kể từ lúc tải, dưới khóa file và bằng thay thế nguyên tử. Mỗi tác vụ dịch có PID chủ sở hữu để mở thêm giao diện không hủy nhầm tác vụ còn chạy. Lịch sử đã lưu tồn tại sau restart; trạng thái Cancel/Retry của Chat/Summary web nằm trong bộ nhớ server. Riêng nghiên cứu lưu cả trạng thái/yêu cầu và nội dung file vào SQLite, không cần giữ cache để tiếp tục hàng đợi hoặc Retry. Cài đặt mặc định chung của ứng dụng vẫn dùng `settings.json`; từng node giữ bản tùy chỉnh của chính nó trong database.

## Phát triển và kiểm tra

```powershell
cd web
npm ci
npm run build
# hoặc npm run dev để build lại khi sửa source; refresh tab sau khi build
cd ..
.venv\Scripts\python.exe web_main.py
.venv\Scripts\python.exe -m unittest discover -s tests
```

Backend: `web_api/server.py`, `web_api/service.py`. Frontend: `web/src/main.jsx`, `web/src/style.css`; React được bundle bằng esbuild, không cần Node server khi sử dụng.

Log backend: `caption-web.log`. Sửa backend cần restart tiến trình `web_main.py`; sửa frontend cần build lại và refresh tab. Để chạy foreground và dừng bằng Ctrl+C, dùng `python web_main.py` khi port 8765 chưa được dùng.

API mutation yêu cầu header `X-RTC-Client: web` và cùng origin. Upload tối đa 50 MB/file; API phục vụ attachment chỉ đọc file nằm trong conversation được chọn. Trang có thể đọc các cài đặt nhạy cảm trên localhost, vì vậy không mở port backend ra Internet.

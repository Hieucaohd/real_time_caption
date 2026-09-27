# Real-time Caption

Phụ đề tiếng Anh thời gian thực cho Windows: nghe âm thanh **đang phát từ các app** (YouTube, Zoom, Teams, game…) hoặc từ **micro**, hiện phụ đề trên một thanh nổi luôn nằm trên cùng và **giữ lại toàn bộ lịch sử** để xem lại hoặc lưu ra file.

## Cài đặt

Yêu cầu Windows 10/11, Python 3.10 trở lên. Nếu có GPU NVIDIA thì app chạy nhanh hơn nhiều, nhưng không bắt buộc.

```bat
setup.bat      :: chỉ chạy lần đầu, tạo .venv và cài thư viện
run.bat        :: mở app
```

Lần đầu bấm **Start**, app sẽ tải model Whisper vào thư mục `models/` (small.en khoảng 480 MB).

## Sử dụng

| Mục | Ý nghĩa |
| --- | --- |
| **Source** | `System audio: …` là âm thanh các app phát ra loa/tai nghe đó (WASAPI loopback). `Microphone: …` là giọng nói qua micro. `Windows Live Captions` đọc chữ từ app Live Captions có sẵn của Windows 11. |
| **Model** | `small.en` là mặc định, cân bằng tốt. `base.en`/`tiny.en` nhanh hơn, hợp với máy không có GPU. `medium.en`, `distil-large-v3`, `large-v3-turbo` chính xác hơn nhưng cần GPU. |
| **Run on** | `auto` thử CUDA trước rồi mới dùng CPU. |
| **Caption overlay** | Bật/tắt thanh phụ đề nổi. |
| **Window → Always on top** | Cửa sổ chính luôn nằm trên các ứng dụng khác, kể cả khi bạn bấm sang app khác. Tiện khi vừa xem, vừa chat với ChatGPT. |

Chữ **xám** là phần đang nghe và có thể còn thay đổi. Chữ **trắng** là câu đã chốt. Toàn bộ câu được ghi kèm giờ trong cửa sổ chính.

**Tự động lưu:** mỗi lần bấm **Start**, app tạo một file mới trong thư mục `transcripts/`. Tên file gồm nguồn và thời điểm bắt đầu, ví dụ `System-audio-Speakers-Xiaomi-Desktop-Speaker-8153_2026-09-26_21-17-26.txt`. Câu nào được chốt là ghi ngay vào file, nên app có tắt đột ngột cũng không mất. Phiên nào không có chữ nào thì file tự bị xoá khi Stop. Nút **📂 Saved files** mở thư mục này. Nút **⭳ Export text…** vẫn dùng được khi muốn lưu những gì đang hiện trong cửa sổ ra một chỗ khác.

### Tóm tắt bằng ChatGPT

Nút **🤖 Summarize (ChatGPT)** chỉ bấm được khi đang có một nguồn chạy. Khi bấm, app gửi file caption của phiên hiện tại lên ChatGPT và yêu cầu tóm tắt, ưu tiên từ đoạn gần nhất tới đoạn xa nhất về thời gian.

- Cần có Chrome mở sẵn với remote debugging ở cổng 9222 và đã đăng nhập ChatGPT. Ví dụ, mở bằng `C:\Users\ADMIN\ai-orchestrator\launch_chrome.bat`.
- App làm mọi thứ **ở nền**: Chrome không bị kéo lên trước màn hình, và tab mới (nếu cần tạo) cũng được mở ở nền. Chrome chỉ cần đang chạy, có thể nằm khuất sau các cửa sổ khác.
- App dùng một **tab riêng** để không đụng vào tab ChatGPT mà công cụ khác đang dùng. Tab này được đánh dấu qua `window.name`. Mỗi lần bấm, app đính kèm file `.txt`, điền prompt rồi gửi. Sau đó app chờ ChatGPT trả lời xong (tối đa 10 phút) và lấy câu trả lời dạng markdown về:
  - Câu trả lời hiện ở tab **ChatGPT summary** trong app. Mỗi câu trả lời mới **thay thế** câu trả lời cũ. Nút **Copy** chép câu trả lời đang hiện vào clipboard.
  - Câu trả lời cũng được lưu cạnh file caption, ví dụ `…_2026-09-26_21-50-48.summary.md`. Mỗi lần tóm tắt được ghi nối thêm vào file này.
  - App lấy câu trả lời bằng cách bấm nút Copy của ChatGPT nhưng chặn lệnh ghi clipboard của trang, nên clipboard thật của bạn không bị ghi đè. Đây cũng là cách ai-orchestrator làm.
- **Send the last N caption lines** (hàng ChatGPT, mặc định 300): chỉ gửi N dòng caption mới nhất cho cả Summarize lẫn Chat, để file gửi đi không quá nặng. `0` nghĩa là gửi cả file. File gốc trong `transcripts/` vẫn đầy đủ; app tạo một bản rút gọn trong thư mục tạm (`%TEMP%\real_time_caption\…_last300.txt`) rồi gửi bản đó. Đầu bản rút gọn có ghi chú "the last N of M caption lines" để ChatGPT biết đây chỉ là phần cuối.
- Ô **ChatGPT → New chat for each Summarize** (mặc định bật) quyết định việc tạo chat mới:
  - Bật: mỗi lần gửi là một cuộc trò chuyện mới.
  - Tắt: gửi tiếp vào cuộc trò chuyện đang mở trong tab riêng. Nếu ChatGPT còn đang trả lời tin trước, app sẽ chờ nó trả lời xong rồi mới gửi. Nếu tab riêng chưa có (lần đầu, hoặc bạn đã đóng tab), app vẫn tạo chat mới.
- **Prompt tóm tắt có nhiều phiên bản**, nằm trong [prompts/summarize/](prompts/summarize/), mỗi phiên bản là một file `v<số> - <tên>.txt`:
  - Hàng **Summary prompt** trong cửa sổ chính cho chọn phiên bản dùng khi bấm Summarize. Lựa chọn được nhớ cho lần sau.
  - **Edit / new version…** mở phiên bản đang chọn trong một cửa sổ sửa. **Save as new version** lưu nội dung đã sửa thành phiên bản tiếp theo (ví dụ `v2 - ngắn gọn`) và chọn luôn phiên bản đó. Phiên bản cũ không bao giờ bị ghi đè, nên luôn chọn lại được.
  - Nút 📂 mở thư mục prompt. Bạn cũng có thể tự thêm hoặc sửa file trong đó; danh sách được đọc lại mỗi khi mở ô chọn.
  - Các biến có thể dùng trong prompt: `{file_name}`, `{source}`, `{started}`, `{now}`.
- Nếu ChatGPT đổi giao diện khiến app không gửi được, chỉnh các selector ở đầu [caption/chatgpt.py](caption/chatgpt.py).

### Chat với ChatGPT về nội dung caption

Tab **Chat** hoạt động như một app chat: câu hỏi của bạn nằm bên phải (xanh), câu trả lời của ChatGPT nằm bên trái (xám, có định dạng markdown).

- Gõ câu hỏi vào ô dưới cùng rồi bấm **Send ➤** hoặc Enter. Shift+Enter để xuống dòng.
- Khi ô **Attach the running caption file** được chọn, mỗi tin nhắn sẽ đính kèm file caption **mới nhất** của nguồn đang chạy, nên ChatGPT luôn thấy những gì vừa được nói. Câu bạn gõ được bọc bởi prompt trong [prompts/chat_with_caption.txt](prompts/chat_with_caption.txt), sửa được; biến `{message}` là chỗ đặt câu bạn gõ. Bỏ chọn ô này thì chỉ gửi đúng câu bạn gõ.
- Chat và Summarize **dùng chung một cuộc trò chuyện ChatGPT**, trên cùng một tab Chrome của app, nên bạn hỏi tiếp dựa trên bản tóm tắt được. Mỗi lần Summarize, yêu cầu tóm tắt và câu trả lời cũng hiện trong khung Chat. Nếu Summarize mở chat mới (ô "Start a new chat every time" đang được tích), khung Chat cũng được làm mới theo.
- **New conversation**: xoá khung Chat, và tin nhắn tiếp theo (kể cả Summarize) sẽ mở cuộc trò chuyện mới.
- Mỗi lúc chỉ chạy một yêu cầu ChatGPT (Chat hoặc Summarize). Trong lúc chờ, các nút gửi bị khoá.
- Nội dung cuộc chat được lưu vào `transcripts/chat_<ngày-giờ>.md`.

Thao tác với thanh phụ đề nổi:
- Kéo chuột trái để di chuyển.
- Lăn chuột để đổi cỡ chữ, giữ Ctrl + lăn chuột để đổi độ rộng.
- Chuột phải để mở menu: trong suốt, số dòng, cỡ chữ, ẩn thanh.

Các tuỳ chọn ở dòng **Overlay** trong cửa sổ chính:
- **Transparent (text only)**: bỏ nền, chỉ còn chữ có viền tối để đọc được trên mọi nền. Ở chế độ này chỉ bấm/kéo được vào chính chữ, vùng trống thì chuột đi xuyên qua.
- **Click-through (lock position)**: cả thanh phụ đề bỏ qua chuột, bấm vào đâu cũng rơi xuống app bên dưới. Muốn di chuyển thanh thì bỏ chọn ô này trước.

Thanh phụ đề luôn nằm trên cùng. Bấm sang app khác hay minimize cửa sổ chính cũng không làm nó biến mất, và bấm vào nó cũng không lấy mất focus của app bạn đang dùng. Riêng các game chạy *exclusive fullscreen* thì sẽ che mất nó; hãy chuyển game sang *borderless/windowed fullscreen*.

Vị trí, cỡ chữ và nguồn âm thanh được lưu vào `settings.json`.

### Dùng chữ từ Windows Live Captions

Chọn nguồn **Windows Live Captions (read its text)** rồi bấm Start. App sẽ tự mở Live Captions nếu nó chưa chạy (có thể tự mở bằng `Win + Ctrl + L`), sau đó liên tục đọc chữ qua UI Automation và ghi thành lịch sử. Cửa sổ Live Captions phải luôn mở, nhưng có thể nằm khuất sau các cửa sổ khác. Ngôn ngữ và nguồn âm (loa/micro) của cách này do phần cài đặt của Live Captions quyết định.

## Cách hoạt động

- `caption/audio.py`: thu âm qua WASAPI (loopback hoặc micro), trộn về mono, resample về 16 kHz.
- `caption/transcriber.py`: faster-whisper chạy kiểu streaming. Cứ khoảng 0,5 giây giải mã lại bộ đệm để hiện chữ tạm. Khi người nói ngừng khoảng 0,7 giây thì giải mã lần cuối bằng beam search và chốt câu. Nói liên tục quá lâu thì chốt dần từng đoạn để độ trễ không tăng.
- `caption/livecaptions.py`: đọc `CaptionsTextBlock` của Live Captions. Hai câu cuối được coi là chữ tạm vì Live Captions còn sửa chúng; các câu phía trước được chốt, có chống trùng khi chữ cuộn khỏi màn hình.
- `caption/gui.py`: giao diện Tkinter, gồm cửa sổ chính và overlay.
- `caption/markdown_html.py`: hiển thị câu trả lời markdown của ChatGPT. markdown-it-py (CommonMark + bảng GFM) chuyển markdown sang HTML, rồi tkinterweb hiển thị HTML đó. Nhờ vậy bảng, khối code, trích dẫn và danh sách lồng đều hiện đúng; link được mở bằng trình duyệt.
- `caption/math_render.py`: vẽ công thức toán. chatgpt.com dùng **KaTeX** (thư viện JavaScript) để hiển thị công thức, nhưng engine HTML trong app không chạy được KaTeX. Vì vậy app tách công thức `\( … \)`, `\[ … \]`, `$ … $`, `$$ … $$` ra trước khi đọc markdown (giống cách remark-math làm trên ChatGPT), rồi vẽ từng công thức thành ảnh bằng matplotlib mathtext. Các lệnh mathtext không hỗ trợ (`\boxed`, `\dfrac`, `\underbrace`, `\xrightarrow`, công thức nhiều dòng `\\`/`aligned`, viết tắt `\frac12`) được chuyển sang dạng tương đương trước khi vẽ. Nếu vẫn có công thức không vẽ được, app hiện mã LaTeX gốc trong khung màu cam thay vì làm vỡ câu trả lời.

Nhật ký lỗi được ghi vào `caption.log`.

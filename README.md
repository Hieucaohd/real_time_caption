# Real-time Caption

Phụ đề tiếng Anh thời gian thực cho Windows: nghe âm thanh **đang phát từ các app** (YouTube, Zoom, Teams, game…) hoặc từ **micro**, hiện phụ đề trên một thanh nổi luôn nằm trên cùng và **giữ lại toàn bộ lịch sử** để xem lại hoặc lưu ra file.

## Cài đặt

Yêu cầu Windows 10/11, Python 3.10 trở lên. Nếu có GPU NVIDIA thì app chạy nhanh hơn nhiều, nhưng không bắt buộc.

```bat
setup.bat      :: chỉ chạy lần đầu, tạo .venv và cài thư viện
run.bat        :: mở app
```

Lần đầu bấm **Start**, app sẽ tải model Whisper vào thư mục `models/` (small.en khoảng 480 MB).

## Đóng gói thành .exe cho máy khác

Máy đích không cần cài Python hay thư viện nào. Trên máy dev (đã chạy `setup.bat`):

```bat
build_exe.bat                       :: bản CPU, khoảng 380 MB, chạy trên mọi máy Windows 10/11
build_exe.bat --gpu                 :: kèm cuBLAS/cuDNN của NVIDIA, khoảng 2,4 GB, phiên âm bằng GPU
build_exe.bat --with-models         :: kèm model Whisper đã tải trong models/, máy đích khỏi phải tải
build_exe.bat --gpu --with-models --zip   :: kết hợp tuỳ ý; --zip tạo thêm dist\RealTimeCaption-<cpu|gpu>.zip
```

Kết quả là thư mục `dist\RealTimeCaption\`. Copy nguyên thư mục (hoặc file zip) sang máy khác rồi chạy `RealTimeCaption.exe`. Thư mục này có sẵn `HUONG-DAN.txt` cho người dùng, và `Open Chrome for ChatGPT.bat` để mở Chrome ở cổng 9222 cho các tính năng ChatGPT.

- Đây là bản PyInstaller dạng thư mục (`onedir`), không phải một file exe duy nhất. Dạng một file phải giải nén hàng trăm MB ra thư mục tạm ở mỗi lần mở, nên rất chậm.
- Cài đặt, prompt, transcript, model và log nằm **cạnh file .exe**, nên cả thư mục mang đi đâu cũng được. Nếu thư mục đó không ghi được (ví dụ nằm trong `Program Files`), app dùng `%LOCALAPPDATA%\RealTimeCaption`. Lần đầu chạy, các prompt trong `prompts/` được chép ra cạnh exe để sửa được.
- Bản CPU chạy trên máy có GPU NVIDIA vẫn an toàn: app chỉ thử CUDA khi nạp được cuBLAS/cuDNN, nếu không thì dùng CPU.
- Kiểm tra một bản build: `RealTimeCaption.exe --self-test [--wav file.wav --model tiny.en]` ghi kết quả vào `selftest.txt` cạnh exe (exit code 0 = tất cả đều ổn). Các mục được kiểm tra: model Whisper, thiết bị âm thanh, UI Automation, Playwright/Chrome, vẽ công thức, hiển thị HTML, chụp màn hình.
- App chưa được ký số, nên lần đầu mở Windows SmartScreen có thể cảnh báo: bấm "More info" → "Run anyway".

Mã build nằm trong `packaging/`: `build_exe.py` (tham số PyInstaller), `make_icon.py` (vẽ `assets/icon.ico`), và các file đặt kèm vào gói.

## Sử dụng

| Mục | Ý nghĩa |
| --- | --- |
| **Source** | `System audio: …` là âm thanh các app phát ra loa/tai nghe đó (WASAPI loopback). `Microphone: …` là giọng nói qua micro. `Windows Live Captions` đọc chữ từ app Live Captions có sẵn của Windows 11. |
| **Model** | `small.en` là mặc định, cân bằng tốt. `base.en`/`tiny.en` nhanh hơn, hợp với máy không có GPU. `medium.en`, `distil-large-v3`, `large-v3-turbo` chính xác hơn nhưng cần GPU. |
| **Run on** | `auto` thử CUDA trước rồi mới dùng CPU. |
| **Caption overlay** | Bật/tắt thanh phụ đề nổi. |
| **Window → Always on top** | Cửa sổ chính luôn nằm trên các ứng dụng khác, kể cả khi bạn bấm sang app khác. Tiện khi vừa xem, vừa chat với ChatGPT. |

Chữ **xám** là phần đang nghe và có thể còn thay đổi. Chữ **trắng** là câu đã chốt. Toàn bộ câu được ghi kèm giờ trong cửa sổ chính.

### Conversation kéo dài qua nhiều ngày

- Activity bar ngoài cùng bên trái mở **Conversations** hoặc **Settings**. Bấm lại biểu tượng đang chọn, hoặc nút `×`, để ẩn sidebar. Các tuỳ chọn Overlay và Always on top nằm trong sidebar Settings; bấm **Save settings** để ghi ngay các lựa chọn xuống `settings.json`.
- **+ New & start** tạo một conversation mới từ nguồn đang chọn rồi bắt đầu nghe. **Pause** chỉ dừng thu âm; conversation vẫn còn trong danh sách bên trái.
- Chọn một conversation đã pause rồi bấm **Continue** để ghi nối transcript vào đúng conversation đó. Mỗi đoạn có mốc ngày/giờ nên phân biệt được nội dung của các ngày khác nhau.
- **Rename** đổi cả tên hiển thị lẫn tên folder vật lý. Tên folder luôn giữ thời điểm tạo và ID ngắn để không trùng nhau.
- Mỗi conversation có folder riêng trong `transcripts/conversations/`, chứa `transcript.txt` (và các part `transcript-0002.txt` nếu file vượt 5 MB), `summaries.md`, `chat_history.md`, `conversation.json` và folder `screenshots/`.
- `conversations.sqlite3` quản lý metadata và lịch sử ChatGPT. Transcript/summary/ảnh vẫn là file thường để dễ mở, sao lưu và phục hồi. Khi cần transcript, app tự ghép toàn bộ các part trước khi gửi.
- Lần chạy đầu sau khi nâng cấp, các transcript `.txt` cũ ở ngay trong `transcripts/` được **sao chép** vào cấu trúc conversation mới; file gốc không bị xoá hay di chuyển.

Câu nào được chốt là ghi ngay xuống đĩa, nên app bị tắt đột ngột cũng không mất phần đã chốt. Trong tab **Transcript**, **📂 Saved files** mở kho dữ liệu, **⭳ Export text…** lưu nội dung đang xem ra một nơi khác và **Clear** xoá phần transcript đang hiển thị. **Open conversation folder** mở đúng folder đang chọn.

### Tóm tắt bằng ChatGPT

Nút **🤖 Summarize (ChatGPT)** dùng được cho conversation đang chọn, kể cả khi đã pause. App chỉ gửi transcript cùng prompt tóm tắt; `chat_history.md` không được đính kèm. Nội dung chat và các summary cũ vẫn được lưu cục bộ để xem lại.

- Cần có Chrome mở sẵn với remote debugging ở cổng 9222 và đã đăng nhập ChatGPT. Ví dụ, mở bằng `C:\Users\ADMIN\ai-orchestrator\launch_chrome.bat`.
- App làm mọi thứ **ở nền**: Chrome không bị kéo lên trước màn hình, và tab mới (nếu cần tạo) cũng được mở ở nền. Chrome chỉ cần đang chạy, có thể nằm khuất sau các cửa sổ khác.
- App dùng một **tab riêng** để không đụng vào tab ChatGPT mà công cụ khác đang dùng. Tab này được đánh dấu qua `window.name`. Mỗi lần bấm, app đính kèm file `.txt`, điền prompt rồi gửi. Sau đó app chờ ChatGPT trả lời xong (tối đa 10 phút) và lấy câu trả lời dạng markdown về:
  - Câu trả lời hiện ở tab **ChatGPT summary** và được thêm vào **Saved summaries**. Chọn một bản trong danh sách để xem lại; **Copy selected** chép bản đang xem vào clipboard.
  - Câu trả lời được lưu vào SQLite và ghi nối thêm vào `summaries.md` trong folder conversation.
  - App lấy câu trả lời bằng cách bấm nút Copy của ChatGPT nhưng chặn lệnh ghi clipboard của trang, nên clipboard thật của bạn không bị ghi đè. Đây cũng là cách ai-orchestrator làm.
- Tab Summary có activity bar con: **History** mở danh sách các summary đã lưu; **Settings** mở Power, giới hạn số dòng, lựa chọn prompt và các nút quản lý prompt. Sidebar con cũng có thể ẩn bằng nút `×` hoặc bấm lại biểu tượng.
- Mỗi tab **ChatGPT summary**, **Chat**, **New words** có **Power riêng**: **Instant**, **Medium**, **High**, hoặc **Keep ChatGPT's**. Summary và Chat cũng có giới hạn **Send last N lines riêng**; `0` nghĩa là gửi toàn bộ transcript.
- Ô **New ChatGPT thread for each summary** quyết định việc tạo chat mới:
  - Bật: mỗi lần gửi là một cuộc trò chuyện mới.
  - Tắt: gửi tiếp vào cuộc trò chuyện đang mở trong tab riêng. Nếu ChatGPT còn đang trả lời tin trước, app sẽ chờ nó trả lời xong rồi mới gửi. Nếu tab riêng chưa có (lần đầu, hoặc bạn đã đóng tab), app vẫn tạo chat mới.
- **Prompt tóm tắt có nhiều phiên bản**, nằm trong [prompts/summarize/](prompts/summarize/), mỗi phiên bản là một file `v<số> - <tên>.txt`:
  - Phần **Summary settings** cho chọn phiên bản dùng khi bấm Summarize. Lựa chọn được nhớ cho lần sau.
  - **Edit / new version…** mở phiên bản đang chọn trong một cửa sổ sửa. **Update current version** ghi thay đổi vào đúng phiên bản hiện tại; **Save as new version** tạo phiên bản tiếp theo (ví dụ `v2 - ngắn gọn`).
  - Nút 📂 mở thư mục prompt. Bạn cũng có thể tự thêm hoặc sửa file trong đó; danh sách được đọc lại mỗi khi mở ô chọn.
  - Các biến có thể dùng trong prompt: `{file_name}`, `{source}`, `{started}`, `{now}`.
- Nếu ChatGPT đổi giao diện khiến app không gửi được, chỉnh các selector ở đầu [caption/chatgpt.py](caption/chatgpt.py).

### Chat với ChatGPT về nội dung caption

Tab **Chat** hoạt động như một app chat: câu hỏi của bạn nằm bên phải (xanh), câu trả lời của ChatGPT nằm bên trái (xám, có định dạng markdown).

- Gõ câu hỏi vào ô dưới cùng rồi bấm **Send ➤** hoặc Enter. Shift+Enter để xuống dòng.
- Hai nút **↑ Top** và **↓ Bottom** cuộn nhanh lịch sử chat lên đầu hoặc xuống cuối.
- **Chat prompt** cho chọn prompt mặc định trong [prompts/chat/](prompts/chat/). Bạn chỉ cần nhập câu hỏi; app chèn nó vào biến `{message}` của prompt đang chọn. Trong cửa sổ sửa, **Update current version** cập nhật đúng phiên bản hiện tại, còn **Save as new version** tạo phiên bản mới. Nút 📁 mở thư mục prompt. Có thể dùng thêm `{file_name}`, `{source}`, `{started}`, `{now}`.
- **Attach transcript** gửi transcript đã tự ghép các part. Prompt vẫn được áp dụng khi không đính kèm transcript. **Attach saved chat history** là lựa chọn riêng và mặc định tắt để phản hồi nhanh hơn; chỉ bật khi thực sự cần gửi lại lịch sử cục bộ cho ChatGPT.
- Chat và Summarize **dùng chung một cuộc trò chuyện ChatGPT của conversation đang chọn**, nên bạn hỏi tiếp dựa trên bản tóm tắt được mà không lẫn sang conversation khác. Mỗi lần Summarize, yêu cầu và câu trả lời cũng hiện trong lịch sử Chat cục bộ.
- **New ChatGPT thread**: tin nhắn tiếp theo (kể cả Summarize) mở một thread mới ở ChatGPT, nhưng lịch sử cục bộ trong app không bị xoá.
- **Attach a screenshot of the apps behind** (mặc định bật): mỗi tin nhắn chat gửi kèm một ảnh chụp màn hình nơi app đang nằm, **không có cửa sổ của app này** (cả cửa sổ chính lẫn thanh phụ đề). App dùng `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` của Windows 10 2004+ để ẩn cửa sổ của mình khỏi ảnh chụp chỉ trong khoảng 0,2 giây lúc chụp. Trên màn hình bạn không thấy gì thay đổi, và những lúc khác vẫn chụp hay chia sẻ màn hình app bình thường. Ảnh được thu về tối đa rộng 1920px, lưu trong `screenshots/` của conversation đang chọn, và hiện dạng thu nhỏ trong bong bóng tin nhắn.
- Mỗi lúc chỉ chạy một yêu cầu ChatGPT (Chat hoặc Summarize). Trong lúc chờ, các nút gửi bị khoá.
- Nếu gửi Chat, Summary hoặc New words tới ChatGPT bị lỗi, tab tương ứng hiện nút **↻ Retry**. Nút này gửi lại đúng yêu cầu vừa thất bại mà không tạo thêm tin nhắn người dùng hoặc bắt nhập lại nội dung.
- Nội dung chat được lưu trong SQLite và đồng thời xuất thành `chat_history.md` trong folder conversation. Mỗi conversation dùng một tab ChatGPT nền riêng để không lẫn context với conversation khác.

### Lưu từ mới vào Voca

Tab **New words** dùng để lưu những từ tiếng Anh bạn chưa biết khi xem video vào [Voca](https://voca-zeta-five.vercel.app/), app học từ vựng của bạn.

1. **Lần đầu**: trong Voca vào **Cài đặt → Ứng dụng kết nối**, tạo một ứng dụng và copy API key (chỉ hiện một lần). Dán key vào ô **Voca API key** rồi bấm **Save key**. Key chỉ được lưu trong `settings.json` trên máy bạn; file này không được đưa vào git.
2. Chọn bộ từ đích ở ô **Save to**, hoặc để **Default** để dùng bộ từ mặc định đã chọn trong Voca (nếu không chọn thì là Hộp thư từ mới). Nút ↻ tải lại danh sách bộ từ.
3. Dán một hoặc nhiều từ/cụm từ (mỗi dòng một từ) rồi bấm **Translate & save ➤** hoặc Enter.

App sẽ:
- **Tìm câu phụ đề gần nhất chứa từ** trong toàn bộ transcript của conversation đang chọn, khớp cả dạng biến đổi (`settle` tìm ra "It settles about here.") và cụm từ. Câu đó được dùng làm ngữ cảnh.
- Nhờ ChatGPT dịch cả danh sách trong một lần: nghĩa tiếng Việt đúng với câu, định nghĩa tiếng Anh, loại từ, phiên âm IPA, bản dịch câu. ChatGPT trả về JSON theo prompt trong [prompts/vocabulary.txt](prompts/vocabulary.txt). Bạn sửa được prompt này, nhưng cần giữ yêu cầu trả về mảng JSON với các trường như trong file. Tab dùng một cuộc trò chuyện ChatGPT riêng (tab Chrome `real-time-caption-vocab`) để không làm rối cuộc trò chuyện tóm tắt/chat. Nút **New ChatGPT chat** bắt đầu lại cuộc trò chuyện đó. Mức **Power** ở hàng ChatGPT cũng áp dụng cho tab này.
- Gửi cả danh sách lên Voca bằng API batch, với `source = realtime_caption`. Ngữ cảnh gồm câu, bản dịch, mốc giờ, và nguồn `Real-time caption · <ngày giờ bắt đầu phiên>`, nên trong Voca bạn lọc được các từ theo từng buổi xem. Voca tự chống trùng: gửi lại một từ đã có chỉ bổ sung nghĩa hoặc ngữ cảnh mới.
- Hiện mỗi từ thành một thẻ kèm trạng thái: **Saved to Voca** (mới hoặc đã bổ sung), **Already in Voca**, hoặc **Not saved** kèm lý do. Chưa có API key thì app vẫn dịch, chỉ không lưu.

Code: `caption/vocab_tab.py` (giao diện), `caption/vocab.py` (tìm ngữ cảnh, prompt, đọc JSON), `caption/voca_client.py` (client chép nguyên từ `voca/docs/external-api/`).

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
- `caption/mathjax_render.py`: hiển thị công thức bằng bản MathJax đóng gói sẵn, chạy hoàn toàn offline. App tách `\( … \)`, `\[ … \]`, `$ … $`, `$$ … $$` trước khi đọc markdown; MathJax dựng LaTeX (gồm `array`, `aligned`, `cases`, phân số lồng nhau…) thành SVG rồi resvg chuyển thành ảnh cho TkinterWeb. `caption/math_render.py` vẫn được giữ làm phương án dự phòng nếu MathJax không khởi tạo được.

Nhật ký lỗi được ghi vào `caption.log`.

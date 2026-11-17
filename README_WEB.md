# Real-time Caption Web — React + FastAPI

Bản web chạy trên chính máy Windows có native app. Chỉ **Windows Live Captions** được dùng để lấy transcript; không có Whisper, upload audio hay microphone streaming trong bản web.

## Khởi chạy

1. Cài Python 3.10+ và Node.js 22.12+.
2. Chạy `setup_web.bat` một lần để cài thư viện Python, npm và build React.
3. Chạy `launch_chrome.bat`. Script khởi động FastAPI nền tại **http://127.0.0.1:8765**, đợi server sẵn sàng rồi mở tab ứng dụng trong cùng Chrome có remote debugging **9222**. Các tab ChatGPT/Gemini/NotebookLM/Claude/Translate vẫn được mở như trước.
4. Chọn/tạo cuộc hội thoại, bấm **Start / Continue**. Mở Windows Live Captions bằng `Win + Ctrl + L`, hoàn tất cài ngôn ngữ của Windows nếu cần rồi phát âm thanh có lời nói.

`run_web.bat` cũng mở qua launcher này. Nếu chưa có bản build hoặc thiếu backend, launcher gọi setup. Chạy lại launcher sử dụng server đang chạy, không tạo thêm server. Đóng tab không dừng capture; dùng **Pause** trước khi đóng nếu muốn dừng ghi.

Port 9222 dành cho Chrome automation; giao diện/API dùng port 8765. Server chỉ bind `127.0.0.1`, không cung cấp truy cập từ máy khác trên LAN.

## Chuyển hội thoại sang thiết bị khác

1. Chọn hội thoại ở máy nguồn, bấm **Export** bên dưới **New conversation**. App tải file `tên-hội-thoại.rtc-conversation.zip`.
2. Chép file ZIP sang thiết bị khác (không cần giải nén).
3. Mở bản web của app trên thiết bị đó, bấm **Import** và chọn file ZIP. Hội thoại được thêm vào danh sách và tự mở.

Gói chứa transcript (kể cả nhiều phần), chat, summary, từ vựng đã lưu, lịch sử dịch, ảnh/file đính kèm, toàn bộ cây nghiên cứu, nguồn ChatGPT/link/số thứ tự response, dấu đọc/dấu nhánh, trạng thái ẩn/đã hiểu và bố cục đồ thị. File ngữ cảnh đã đóng băng của node cũng được mang theo để Retry vẫn dùng đúng nội dung. Dữ liệu import được lưu vào SQLite và thư mục hội thoại dùng chung với native app.

Mỗi lần import tạo một bản sao với ID mới, không ghi đè hội thoại hiện có. Hội thoại được đặt ở trạng thái tạm dừng; node đang chạy/xếp hàng chuyển sang chưa hoàn tất để thử lại thủ công, không tự gửi ChatGPT. Chọn lại tab ChatGPT trên máy mới trước khi Retry vì ID tab Chrome của máy cũ không còn dùng được. Cài đặt chung, mẫu prompt toàn cục và phiên đăng nhập Chrome không bị thay thế.

Export lấy dữ liệu đã lưu tại thời điểm đóng gói. Pause Live Captions trước khi export; chờ dấu **Đã lưu DB** nếu vừa kéo đồ thị. Giới hạn gói ZIP 512 MB, dữ liệu giải nén 2 GB. App kiểm tra đường dẫn, cấu trúc và mã kiểm tra của file; gói hỏng hoặc không hợp lệ bị từ chối và không tạo hội thoại dở dang. Hai thiết bị cần phiên bản app có chức năng export/import này.

## Đóng gói thành EXE

Bấm đúp **`build_web_exe.bat`** tại thư mục dự án, nhập version khi được hỏi, ví dụ **`1.2.3`** hoặc **`1.2.3.4`** (có thể nhập sẵn tiền tố `v`). Version gồm 3 hoặc 4 số từ 0 đến 65535, không thêm số 0 ở đầu mỗi phần. Script yêu cầu Git sạch (đã commit các thay đổi và xử lý file chưa được theo dõi) và có nhánh local `main`. Trước khi build, script tạo annotated tag `v<version>` tại commit hiện tại, tạo/checkout nhánh `build/<tên-tag>`, rồi cài dependency, chạy `npm ci` + build React, đóng gói và tự kiểm tra file EXE. Nếu tag hoặc nhánh build đã tồn tại, script dừng để bạn chạy lại với version khác, không ghi đè bản cũ. Kết thúc hoặc build lỗi sau khi đã chuyển nhánh đều checkout về `main`; nếu Git không cho checkout, script báo lỗi và không ép ghi đè file. Tag và nhánh build được giữ ở local để tra cứu, không tự push. Máy build cần Git, Python và Node.js/npm; máy chạy bản đóng gói chỉ cần Windows và Google Chrome.

Kết quả: **`dist\RealTimeCaptionWeb-<tên-tag>.exe`**, ví dụ `RealTimeCaptionWeb-v1.2.3.exe`, dạng [PyInstaller onefile](https://pyinstaller.org/en/stable/runtime-information.html). Tên file và các trường **File version / Product version** trong Properties → Details đều chứa đúng tag Git của bản build. Version dạng số bên trong Windows là `1.2.3.0` nếu bạn nhập 3 phần, hoặc giữ đủ 4 phần nếu đã nhập. Bấm vào EXE để khởi động FastAPI và mở app trong Chrome 9222. File JS/CSS, font, prompt mặc định và thư viện Python nằm trong EXE; không cần mang theo `.venv`, `node_modules` hay `web/dist`.

Dữ liệu thay đổi (`conversations.sqlite3`, `transcripts`, `settings.json`, `prompts`, `chrome-profile`) nằm cạnh EXE, không nằm trong thư mục giải nén tạm. Để dùng chung với native, đặt EXE cùng thư mục dữ liệu của native hoặc tạo shortcut với `--data-dir "D:\thu-muc-du-lieu-native"`. Hai bản dùng đúng cùng database, không sao chép database khi build. Khi thư mục EXE không ghi được, dùng `%LOCALAPPDATA%\RealTimeCaption` như native.

Nếu cổng 8765 đã phục vụ một thư mục dữ liệu khác, dùng `--port 8766` hoặc chọn đúng thư mục qua `--data-dir`. Đóng tab không dừng server; mở EXE lần nữa sẽ dùng lại server. Khi thay EXE, dừng các tiến trình `RealTimeCaptionWeb-<tên-tag>.exe` của bản cũ trong Task Manager trước.

Log build: `build\web-exe\build.log`; báo cáo kiểm tra: `build\web-exe\selftest.json`. Log runtime: `caption-web.log` trong thư mục dữ liệu. Bản chuẩn không kèm Whisper/CUDA; VinAI offline là tùy chọn: cài `requirements-translation.txt`, rồi chạy `build_web_exe.bat --with-translation` và mang theo model đã tải. Mặc định không đóng gói database, API key, lịch sử chat hay phiên đăng nhập Chrome.

## Chức năng

- **Transcript:** Start/Pause/Continue, chữ tạm, lịch sử, tìm/chọn/đổi tên conversation, mở folder, copy, clear view, tải toàn bộ hoặc N dòng cuối, auto-scroll.
- **Chat:** chọn tab ChatGPT, prompt có phiên bản, Power, giới hạn transcript, đính kèm transcript/lịch sử, tự chụp màn hình, chọn màn hình/cửa sổ để chụp, upload file/ảnh và Ctrl+V ảnh. Kết quả hỗ trợ Markdown, bảng, code, công thức, copy và tải MD/DOCX/PDF. Có streaming preview, Cancel và Retry.
- **Nghiên cứu theo nhánh:** chuyển một câu trả lời Chat thành cây hỏi–đáp; bôi đen/chuột phải để hỏi sâu hơn, tạo nhiều nhánh độc lập, thu gọn, tìm node, zoom, đọc Markdown/công thức và đánh dấu đã hiểu.
- **Summary:** tab Chrome và cài đặt riêng, chọn prompt/version, bật/tắt transcript, tạo chat mới, xem lịch sử và xuất kết quả.
- **Translation:** Azure, Google API, Chrome Translator API (trên máy), Gemini, Google Translate web, VinAI offline; tự dịch câu đã chốt, tùy chọn dịch tạm, nhóm câu, dịch phần còn thiếu hoặc câu được chọn, Stop/Retry, lịch sử theo engine, kiểm tra cấu hình và tải model offline. VinAI cần `requirements-translation.txt`, giống native.

Nếu gửi Chat gặp lỗi tab ChatGPT đã chọn không còn mở, app quét lại các tab đang mở, chọn và lưu tab đầu tiên vào cài đặt Chat, đồng thời thông báo tên tab mới. Câu hỏi vẫn ở trạng thái lỗi; không tự gửi lại. Chỉ khi người dùng bấm **Retry** mới gửi lại câu hỏi với prompt và file đã chuẩn bị, vào tab hiện được chọn. Nếu danh sách trống hoặc Chrome không kết nối được, app thông báo để người dùng mở/chọn tab; không tự tạo tab hoặc gửi câu hỏi trong bước khôi phục này.

Nút **Lấy lại câu trả lời** ở mỗi câu trả lời Chat và phần đọc node copy lại Markdown từ ChatGPT, không gửi câu hỏi hay yêu cầu tạo lại response. App dùng link/số thứ tự trong **Nguồn ChatGPT**, chọn response cuối cùng của cùng câu hỏi nếu ChatGPT bổ sung nhiều response liên tiếp, rồi cập nhật nội dung và vị trí trong SQLite. Chat và node gốc nhập từ Chat được cập nhật cùng nhau. Nếu câu trả lời thiếu nguồn, bổ sung thông tin ở **Nguồn ChatGPT** trước; node lỗi/cancel cũng có thể bổ sung nguồn và lấy lại. Copy rỗng, lỗi hoặc thay đổi nội dung đồng thời sẽ không ghi đè nội dung đang lưu. Khi lần copy ban đầu thất bại, app giữ một câu trả lời trống cùng thông tin nguồn đã đọc được để có thể lấy lại sau. Tab tạm dùng để copy sẽ đóng và app trở về tab ứng dụng.

Trong tab **Translation**, bật **Tự cuộn xuống cuối** để theo dõi realtime: danh sách câu cuộn xuống cuối và tự chọn câu mới nhất đã dịch xong; phần bản dịch cuộn theo kết quả/preview mới. Tắt tùy chọn để giữ câu đang chọn và đọc lại phía trên. Lựa chọn được lưu trong cài đặt và giữ sau khi F5.

Chế độ **Đọc song song** hiển thị **Nguyên văn → Dịch realtime → Dịch đã chốt**. Cột giữa chỉ hiện khi bật **Dịch ngay khi đang nói** với Chrome Translator API; tắt thì trở về hai cột. Các cột dùng chung một thanh cuộn và căn cuối đoạn xuống dưới. Câu nguồn và bản dịch đã chốt vẫn highlight tương ứng khi hover. Nội dung realtime được tích lũy riêng trong SQLite theo cuộc hội thoại và cặp ngôn ngữ, giữ sau F5, dừng thu, bật/tắt tùy chọn và export/import. Đoạn đang nói có thể được cập nhật khi thêm từ; các đoạn trước được giữ lại và không bị bản dịch chính thức thay thế.

### Chrome Translator API

Mở app bằng `launch_chrome.bat`. Trong **Settings → Translation**, chọn **Chrome Translator API (trên máy)**, chọn ngôn ngữ nguồn/đích, rồi bấm **Tải / khởi tạo gói ngôn ngữ**. Chờ báo **Sẵn sàng**, bấm **Save settings**, sau đó bật **Auto translate** trong tab Translation. Đặt số câu mỗi nhóm bằng 1 để dịch ngay khi câu được chốt; bật dịch preview nếu muốn dịch cả câu đang nói.

**Dịch ngay khi đang nói** chỉ hiện khi chọn Chrome Translator API, cả trong Settings và thanh công cụ Translation. Bật cùng **Auto translate** để dịch phần phụ đề chưa chốt; tùy chọn được lưu. App lấy nội dung tạm mới nhất mỗi khoảng 300 ms khi có thay đổi, giữ bản dịch trước trong lúc chờ kết quả mới và không hủy lượt đang dịch chỉ vì thêm từ. Preview có luồng riêng nên không phải chờ app xử lý hết hàng đợi câu đã chốt. Thời gian kết quả xuất hiện còn phụ thuộc Chrome và nhịp cập nhật giao diện, không phải cam kết dịch xong trong 300 ms. Mỗi đoạn realtime lưu kết quả thành công mới nhất, không lưu từng phiên bản theo mỗi từ. App chuyển sang đoạn tiếp theo sau khoảng ngừng nói hoặc khi đoạn dài khoảng 600 ký tự và nguồn của phần đã dịch được xác nhận, tránh lặp lại phần phụ đề đang trượt. Bản dịch chính thức lưu riêng như cũ. Đổi engine, tắt chế độ hoặc dừng thu sẽ hủy tác vụ preview đang đợi nhưng giữ lịch sử đã có. Các engine khác chỉ dịch câu đã chốt.

Với Chrome Translator, app xử lý tối đa **2 lượt dịch câu đã chốt + 1 lượt dịch tạm** đồng thời, giữ kết nối CDP riêng theo luồng để tránh kết nối lại mỗi câu. Các lượt trả về không cần theo thứ tự nhưng luôn lưu đúng dòng trong SQLite; Stop hủy cả các lượt đang chạy và đang chờ. Preview chờ được gộp thành nội dung mới nhất. Đây là xử lý đồng thời ở phía app: [Chrome hiện vẫn xử lý mô hình tuần tự](https://developer.chrome.com/docs/ai/translator-api#sequential_translations), nên không bảo đảm nhanh gấp ba hoặc không bao giờ phải đợi Chrome. Các engine khác giữ cách xử lý hiện tại.

Không cần API key, không tính phí theo ký tự. Lần đầu Chrome có thể cần tải mô hình qua Internet; tiến trình hiển thị trong Settings. Giữ tab ứng dụng mở trong Chrome 9222: mô hình chạy trong tab này, backend lấy kết quả qua CDP và lưu vào cùng SQLite/lịch sử dịch của native app. Stop/Retry và dịch phần còn thiếu vẫn dùng cơ chế hiện tại. Native cũng dùng được engine này khi tab web đã sẵn sàng. Sau khi tải lại trang, app dùng lại gói đã sẵn có nếu Chrome cho phép; nếu Chrome yêu cầu khởi tạo lại, mở Settings và bấm khởi tạo. App không tự chuyển sang dịch vụ trả phí khi Chrome gặp lỗi.

Khả năng hỗ trợ tùy phiên bản Chrome desktop và cặp ngôn ngữ; Settings kiểm tra trực tiếp trên trình duyệt. Tham khảo [tài liệu Translator API](https://developer.chrome.com/docs/ai/translator-api).
- **New words:** tìm ngữ cảnh trong transcript, dịch bằng ChatGPT, chọn Power, lưu vào Voca và chọn collection. Lịch sử thẻ từ được lưu chung trong SQLite và hiển thị ở hai giao diện.
- **Prompt editor / Settings:** sửa phiên bản hiện tại hoặc lưu phiên bản mới, dùng chung file với native app. API key được lưu tại `settings.json` trên máy này.
- **Caption overlay:** dùng lại overlay Windows của native app để luôn nổi trên các ứng dụng khác, kéo vị trí, chỉnh cỡ chữ, số dòng, nền trong suốt và click-through. Đây là cửa sổ Windows riêng; tab Chrome không thể tự áp dụng click-through.

Automatic screenshot chụp màn hình chính và tạm thu nhỏ cửa sổ Chrome nếu cửa sổ đang ở phía trước là tab Real-time Caption. Khi đã đính kèm ảnh, ảnh đó được ưu tiên. Nút Screenshot dùng bộ chọn màn hình/cửa sổ của Chrome để bạn chọn nội dung muốn gửi.

Ảnh dán bằng Ctrl+V, chọn từ thiết bị hoặc chụp bằng **Screenshot** hiện ảnh thu nhỏ trong Chat và hộp **Tạo nhánh con** sau khi tải lên. Bấm ảnh để xem lớn, chọn **Kích thước gốc** để đọc chi tiết hoặc **Vừa khung** để xem toàn ảnh. Đóng bằng **Esc** hoặc nút **×**; câu hỏi và file đang đính kèm được giữ nguyên. Mỗi ảnh có nút **×** riêng để bỏ trước khi gửi. Ảnh trong lịch sử Chat cũng có thể bấm để xem lớn và tải xuống.

Khi đọc một node, mục **Đính kèm khi hỏi** dưới câu hỏi hiển thị ảnh và các file đã đính kèm (kể cả transcript/lịch sử/ngữ cảnh tổ tiên nếu đã bật). Bấm ảnh để xem lớn, bấm tên file để tải xuống. Node con dùng bản file đã đóng băng trong SQLite lúc xếp hàng, nên vẫn xem được sau khi xóa cache, Retry hoặc export/import sang thiết bị khác. Node gốc chuyển từ Chat hiển thị file của câu hỏi Chat tương ứng. Nếu file của dữ liệu cũ không còn tồn tại, app báo rõ **Không còn file đã lưu** và vẫn hiển thị câu trả lời.

## Nghiên cứu theo nhánh

Các đoạn dùng để tạo node con có **dấu nhánh màu xanh, gạch chân** riêng với dấu đọc màu vàng. Di chuột lên đoạn để xem tên và làm sáng node con cùng đường nối trên đồ thị; nhánh đang thu gọn tạm hiện khi hover rồi trở lại trạng thái cũ khi rời chuột, không sửa bố cục đã lưu. Bấm đoạn để mở node con; nếu đoạn có nhiều node, app hiện danh sách để chọn (Esc để đóng). Trong **Các đoạn đã tạo nhánh** dưới câu trả lời, tên node cũng bấm được và hỗ trợ bàn phím. Bấm node đã ẩn sẽ bật **Hiện node đã ẩn** để đọc, không đổi trạng thái ẩn trong database. Kéo bôi đen vẫn dùng để chọn nội dung, không tự nhảy sang node con. Dấu nhánh không có thao tác sửa/xóa riêng và không dùng làm vị trí tự cuộn. Bỏ/đổi dấu đọc không xóa dấu nhánh. Vị trí trích dẫn lưu ở từng node con trong SQLite; tên/ẩn/xóa node con tự cập nhật danh sách. Nhánh cũ chỉ có đoạn trích sẽ tô xanh khi tìm được duy nhất một vị trí khớp; nếu trùng nhiều chỗ thì chỉ hiện trong danh sách để tránh đánh dấu nhầm.

Mỗi node có biểu tượng **Mở trên ChatGPT** ở góc phải; nút có chữ cũng nằm trên khung đọc. App mở một tab Chrome 9222 mới bằng link đã lưu, lấy lịch sử hội thoại để xác định câu trả lời theo số thứ tự rồi cuộn tới đúng response, kể cả khi câu trả lời cũ chưa được tải vào màn hình. Node thiếu link hoặc số thứ tự cần **Cập nhật nguồn** trước. Nếu không xác định được response, app báo lỗi và giữ tab mới để bạn kiểm tra; thao tác này không gửi câu hỏi hay tạo nhánh ChatGPT.

Trong cài đặt **Tạo nhánh con**, nút **Mở tab ChatGPT mới** mở sẵn `https://chatgpt.com/` trong Chrome 9222, refresh danh sách và tự chọn tab mới. Nút dùng được cả khi node cha chưa có nguồn ChatGPT. Câu hỏi đang soạn và các file đã đính kèm được giữ nguyên; bấm **Xếp hàng** khi muốn gửi.

Sau khi mở tab ChatGPT mới hoặc tạo nhánh ChatGPT (bằng nút hoặc checkbox tự động), Chrome quay lại đúng tab ứng dụng đã yêu cầu thao tác. Tab ChatGPT vẫn mở và được chọn trong dropdown; hộp tạo node con, câu hỏi và file đang soạn không bị tải lại. Nếu tạo nhánh lỗi, app cũng được đưa lên để hiển thị thông báo. Nếu tab ứng dụng đã đóng, app giữ tab ChatGPT mới và không tự mở lại ứng dụng.

Hai checkbox **Tạo cuộc hội thoại ChatGPT mới** và **Tạo nhánh từ cuộc hội thoại của node cha** loại trừ nhau. Tick một ô sẽ tự mở tab tương ứng, refresh và chọn tab vừa tạo; dropdown chọn tab bị khóa. Bấm **Lưu cài đặt** để ghi chế độ vào SQLite: mỗi lần mở lại hộp tạo node con sẽ tự tạo một tab mới theo chế độ đã lưu, dùng nguồn của node cha hiện tại. Nếu mở tab lỗi, có nút **Thử tạo tab lại** và không thể xếp hàng vào tab cũ. Chế độ tạo nhánh cần link và số thứ tự response của node cha. Bỏ tick cả hai để chọn tab hoặc dùng các nút mở tab thủ công. Nhiều câu hỏi trong cùng một lần xếp hàng dùng chung tab vừa tạo và chạy lần lượt; Retry node đã tạo tiếp tục dùng tab của node đó.

Trong **Tạo nhánh con**, bật **Đính kèm ngữ cảnh tổ tiên** và chọn **Lấy ngữ cảnh ngược tới**: chỉ cha, tới ông/bà, cụ, một tổ tiên cụ thể (hiện tên node), hoặc toàn bộ về node gốc. App đính kèm `research-ancestors.md` chứa đầy đủ các cặp câu hỏi–câu trả lời trên đúng đường dẫn đó, theo thứ tự cũ → mới; không lấy nhánh anh em. Mặc định tùy chọn này tắt. **Lưu cài đặt** ghi nhớ độ sâu cho lần sau. Khi xếp hàng, lựa chọn, danh sách node và nội dung file được chụp lại trong SQLite, nên retry vẫn dùng ngữ cảnh ban đầu dù node tổ tiên đã được sửa hoặc file cache mất. Tùy chọn này độc lập với **Đính kèm lịch sử chat**.

Trong hộp **Tạo nhánh con**, bấm **Tạo nhánh ChatGPT** để mở link node cha trong một tab Chrome 9222 mới. App dùng số thứ tự đã lưu để xác định đúng response, cuộn đến response đó rồi bấm menu **More actions → Branch conversation → Branch in new chat** (hoặc **Branch in new chat** trực tiếp trên giao diện cũ). Sau khi nhận xác nhận tạo nhánh thành công, app so sánh mã tab trước/sau thao tác, đối chiếu link hội thoại mới để không chọn nhầm tab nguồn hoặc tab khác vừa mở, rồi refresh danh sách và tự chọn tab nhánh. Tùy chọn **New ChatGPT chat** được tắt để câu hỏi tiếp theo tiếp tục nhánh vừa tạo. Nút này chỉ tạo nhánh ChatGPT; nhập câu hỏi và bấm **Xếp hàng** để tạo node con như bình thường. Nếu node cha thiếu nguồn, bổ sung tại **Nguồn ChatGPT → Cập nhật nguồn** trước. Khi không xác định được response hoặc tab nhánh, app báo lỗi và không tự bấm lại.

Mở **Nguồn ChatGPT → Cập nhật nguồn** để nhập đúng hai trường: **Link cuộc hội thoại ChatGPT** (giữ nguyên link đầy đủ, kể cả Project, query và fragment) và **Số thứ tự câu trả lời** (1, 2, 3… tính từ đầu cuộc hội thoại, chỉ đếm câu trả lời ChatGPT, không đếm tin nhắn người dùng). Bấm **Lưu nguồn** để lưu vào SQLite. Có thể để trống trường chưa biết hoặc sửa lại thông tin đã nhập. Chat và node gốc nhập từ chính response đó đồng bộ hai chiều, còn node con giữ nguồn riêng.

Tab nhánh mới có thể dùng đường dẫn tạm `local-chatgpt:…`. App vẫn tự chọn tab đó khi nó thuộc chính trang nhận xác nhận tạo nhánh thành công; không cần chờ link đổi sang mã hội thoại trên server hoặc gửi thêm câu hỏi.

Mỗi câu trả lời mới trong Chat và nghiên cứu lưu `response_conversation_url` và `response_position` cùng nội dung. App đọc lịch sử đầy đủ qua tab ChatGPT đã đăng nhập rồi đếm theo nhánh hội thoại dẫn đến response được Copy, bỏ qua suy luận, thao tác công cụ, tin nhắn ẩn và phiên bản trả lời khác. Không đếm số phần tử DOM đang hiển thị vì ChatGPT có thể chỉ dựng một phần lịch sử. Nếu không truy xuất được lịch sử hoặc không xác định chắc chắn thứ tự, vị trí để trống cho người dùng bổ sung. Các cột Message ID/Turn ID cũ được giữ nội bộ để tương thích dữ liệu; giao diện chỉ hiển thị link và số thứ tự, không chuyển mã định danh thành số thứ tự phỏng đoán.

Trong **Settings → Giao diện web → Chế độ hiển thị**, chọn **Sáng** hoặc **Tối** rồi bấm **Save settings**. Lựa chọn lưu vào bảng `web_preferences` trong SQLite và áp dụng cho toàn bộ web app, gồm Chat, nghiên cứu, công thức, code và các hộp thoại. Trình duyệt nhớ tạm lựa chọn để tránh chớp nền sáng khi tải lại; database là nguồn cài đặt chính.

Để nhớ chỗ đã đọc, bôi đen một đoạn trong câu trả lời rồi bấm **Đánh dấu** (hoặc chuột phải → **Đánh dấu đã đọc tới đây**). Đoạn đó được tô vàng và lưu vào SQLite riêng cho node, vẫn còn khi chuyển qua node con rồi quay lại hoặc tải lại trang. Mỗi node có một dấu đọc; đánh dấu đoạn mới thay thế đoạn cũ. **Tới dấu** cuộn đến đoạn đã tô, **Bỏ dấu** xóa dấu. Không tự theo dõi vị trí cuộn và không thay đổi Markdown gốc.

Mỗi câu trả lời mới lưu tên tab ChatGPT tại lúc nhận xong vào SQLite (`response_tab_title`), hiển thị trong khung đọc và tooltip node. Node gốc nhập từ Chat giữ tên tab của câu trả lời gốc (cả Chat web và native từ phiên bản này). Node cũ chưa ghi thông tin sẽ hiện “Chưa có tên tab được lưu”; tên hiện tại của tab không được dùng để đoán lại lịch sử.

Trong khung đọc, **Ẩn nhánh** ẩn node và toàn bộ node con, vẫn giữ câu trả lời và tiếp tục các yêu cầu đang chạy. Bật **Hiện node đã ẩn** để xem rồi chọn **Hiện lại nhánh**; trạng thái ẩn lưu trong database. **Xóa nhánh** hỏi xác nhận rồi xóa vĩnh viễn toàn bộ nhánh, dữ liệu bố cục liên quan và ảnh đính kèm không còn được node khác sử dụng. Yêu cầu đang chạy trong backend này được hủy và kết quả về trễ không tạo lại node; nếu backend khác đang xử lý nhánh, cần hủy ở đó trước. Thao tác xóa không xóa lịch sử Chat nguồn hay cuộc hội thoại trên ChatGPT.

Giao diện ưu tiên nội dung: thanh tiêu đề/Live Captions thu gọn, **Tùy chọn Chat** mặc định đóng (bấm để mở), ô nhập tự giãn theo nội dung. Nút **Tập trung đọc** ẩn danh sách hội thoại, điều khiển và ô soạn; **Thoát tập trung** khôi phục giao diện cùng bản nháp. Trong nghiên cứu, câu trả lời chiếm phần rộng hơn; **Mở rộng câu trả lời / Hiện đồ thị** chuyển giữa đọc toàn chiều rộng và xem cây. Các nút này không thay đổi dữ liệu hay bố cục node đã lưu.

Câu trả lời web dùng chung `MarkdownResponse` trong Chat, Summary và nghiên cứu: `react-markdown`, `remark-gfm`, `remark-math`/KaTeX và `rehype-highlight`. Kiểu chữ, dòng, tiêu đề và vùng đọc bám theo thông số đo từ tab ChatGPT ngày 15/11/2026 (nội dung 16px/26px, rộng tối đa 768px). Công thức `$$...$$` một dòng vẫn hiển thị dạng khối; code có tô màu và nút copy riêng, bảng/cuộn công thức không làm tràn khung. Markdown gốc trong database không thay đổi. Đây là bản dựng lại giao diện Markdown sáng, không nhúng toàn bộ giao diện ChatGPT: citation tương tác, Canvas và các widget riêng không có trong dữ liệu Copy nên không thể khôi phục chỉ từ Markdown.

Khi đọc câu trả lời, web và native cùng chuẩn hóa các dấu escape thừa đã biết từ ChatGPT Copy: chỉ số dưới, dấu `<`, dấu `*`, ngoặc tập hợp có kích thước và khoảng cách TeX. Chỉ áp dụng với vùng công thức có dấu phân cách Copy đã escape; giữ nguyên code, nội dung chữ trong công thức và dấu xuống dòng ma trận. Câu trả lời cũ cũng được xử lý khi mở lại, không cần sửa database. Công thức sai cú pháp ngay từ nguồn hoặc lệnh không được bộ dựng hỗ trợ vẫn có thể cần chỉnh riêng.

1. Trong **Chat**, bấm **Nghiên cứu theo nhánh** dưới một câu trả lời. Node gốc giữ nguyên câu hỏi và câu trả lời tại thời điểm chuyển. Bấm lại cùng câu trả lời sẽ mở lại cây đã có.
2. Mỗi lần mở hộp **Tạo nhánh con**, danh sách tab ChatGPT trên Chrome 9222 tự làm mới. Mặc định giữ tab đã chọn và gửi vào cuộc hội thoại đang mở ở tab đó. Nếu đã lưu một trong hai chế độ tạo tab tự động, app mở và chọn tab mới. Nút **Refresh Chrome tabs** vẫn dùng được nếu bạn mở thêm tab khi đang soạn câu hỏi.
3. Bấm node để đọc bên phải. Bôi đen nội dung trong câu trả lời, chuột phải, chọn **Tạo nhánh từ đoạn đã chọn** (hoặc bấm **Tạo nhánh con** khi đang bôi đen). Khi tạo node con thành công, đoạn đó tự được tô vàng thành dấu đọc mới của node cha, thay dấu cũ và lưu cùng node con trong SQLite. Đóng hộp soạn hoặc gửi thất bại không đổi dấu đọc. Không bôi đen thì **Tạo nhánh con** mở câu hỏi tự do như trước.
4. Muốn hỏi đồng thời về bốn khái niệm, bật **Tạo nhiều nhánh**, nhập bốn câu hỏi trên bốn dòng rồi bấm **Xếp hàng 4 nhánh**. App tạo ngay bốn node; bạn vẫn có thể tạo thêm nhánh trong lúc ChatGPT đang trả lời.
5. Các node chọn **tab ChatGPT khác nhau chạy song song** (tối đa 20 node đang chạy); các câu hỏi dùng chung một tab vẫn gửi lần lượt để không lẫn response. Dùng **Mở tab ChatGPT mới** hoặc **Tạo nhánh ChatGPT** khi muốn một node chạy độc lập. Tab đang bận không chặn các tab khác. Trong node đang chạy, bấm **Hủy chờ phản hồi** để dừng yêu cầu riêng của node đó; node và phần nội dung đã nhận vẫn được lưu trong SQLite, các node khác tiếp tục chạy. Node chưa gửi có nút **Hủy xếp hàng**; có thể thử lại sau khi yêu cầu cũ đã dừng. Khi tiếp tục cùng cuộc hội thoại, ChatGPT giữ lịch sử của cuộc hội thoại đó. Quan hệ cha–con dùng để tổ chức cây; app chỉ đính kèm ngữ cảnh tổ tiên khi bạn bật tùy chọn tương ứng. Đoạn được bôi đen điền vào ô câu hỏi để bạn sửa trước khi gửi.
6. Tùy chọn dùng chung component với Chat: tab Chrome + refresh, mẫu prompt + sửa/lưu phiên bản, Power, số dòng transcript, đính kèm transcript/lịch sử chat và Auto screenshot. Riêng nghiên cứu dùng hai checkbox tạo tab mới/tạo nhánh từ node cha thay cho **New ChatGPT chat**. Danh sách mẫu lấy từ `prompts/chat`; mẫu sửa ở một nơi dùng được ở cả hai. Bấm **Lưu cài đặt** để lưu các tùy chọn vào bảng `research_preferences` trong SQLite và dùng lại cho các nhánh tạo sau, ở mọi hội thoại, kể cả sau khi khởi động lại. Khi chưa lưu, nhánh lấy mặc định từ Chat và không bật tạo tab tự động. Nút lưu không gửi câu hỏi, không lưu câu hỏi/ảnh thành mặc định và không đổi cài đặt Chat. Mỗi node vẫn giữ bản cài đặt riêng tại thời điểm xếp hàng. Không còn prompt nghiên cứu dựng sẵn.
7. Có thể dán ảnh trực tiếp vào ô câu hỏi, chọn file hoặc bấm **Screenshot**, và bỏ file trước khi gửi. Auto screenshot không chụp thêm khi đã có ảnh đính kèm. Enter gửi một câu hỏi; Shift+Enter xuống dòng. Khi bật nhiều nhánh, Enter xuống dòng để nhập câu hỏi kế tiếp.
7. File, transcript, hướng dẫn và Power được chụp lại khi xếp hàng; tự chụp màn hình cũng chạy ngay lúc đó, không chụp một màn hình khác sau thời gian chờ. Retry dùng lại yêu cầu này; nút thử lại cho phép dùng tab Chrome đang được chọn nếu tab cũ đã đóng.

Đồ thị hỗ trợ kéo/thả và điều hướng theo các thao tác quen thuộc của [draw.io](https://www.drawio.com/docs/reference/shortcuts/modifier-shortcuts-in-diagrams/):

- **Kéo node** để đổi vị trí; bấm node để đọc. Kéo chỉ đổi bố cục, không đổi quan hệ cha–con hay ngữ cảnh câu hỏi.
- Khi mở node có dấu đọc, khung câu trả lời tự cuộn tới đoạn tô vàng. Bấm lại node đang đọc cũng đưa bạn về dấu đó; cập nhật trạng thái định kỳ không kéo vị trí cuộn khi bạn đang đọc tiếp.
- **Kéo nền**, **Space + kéo**, **chuột giữa** hoặc **chuột phải + kéo** để di chuyển khung nhìn tự do. Công cụ bàn tay (**H**) cho phép kéo khung nhìn ngay trên node; **V** quay về công cụ chọn/kéo node.
- **Shift/Ctrl + bấm** để chọn nhiều node; **Shift + kéo nền** để khoanh vùng chọn. Kéo một node được chọn để di chuyển cả nhóm. Bật **Kéo cả nhánh** để các node con, kể cả đang thu gọn, đi theo.
- **Lăn chuột** zoom tại con trỏ; **Alt + lăn** di chuyển, **Shift + lăn** di chuyển ngang. Với trackpad, tắt **Lăn để zoom** để cuộn khung nhìn; **Ctrl + lăn** vẫn zoom.
- Bật **Bám lưới** để kéo theo bước 10 px; giữ **Alt** khi kéo để bỏ bám lưới tạm thời. **Mũi tên** dịch node 1 px, **Shift + mũi tên** dịch 10 px.
- **Ctrl+Z** hoàn tác vị trí, **Ctrl+Shift+Z / Ctrl+Y** làm lại. **Esc** hủy lượt kéo đang diễn ra. Hoàn tác áp dụng cho bố cục, được giữ trong phiên đang mở.
- **F** vừa khung, **C** tới node đang đọc, **0** trở về zoom 100%; **+/−** phóng/thu. Bản đồ thu nhỏ cho phép bấm/kéo để đi nhanh. Nút **Mở rộng đồ thị** dành toàn bộ chiều rộng cho cây; bấm node để mở lại khung đọc.
- Nút **Sắp xếp lại** trên thanh công cụ đồ thị dàn cây từ trái sang phải, giãn các nhánh để tránh chồng lấn, căn vừa khung và tự lưu bố cục vào SQLite. Có thể dùng **Ctrl+Z** để hoàn tác. Các node mới được đặt cạnh node cha, không đẩy các node bạn đã sắp xếp.

Tọa độ, khung nhìn/zoom, trạng thái thu gọn và các tùy chọn thao tác tự lưu vào bảng `research_graph_layouts` trong SQLite, riêng cho từng cây. Góc dưới báo **Đang lưu / Đã lưu DB / Chưa lưu — Thử lại**. Bố cục được tải lại từ database, không phụ thuộc localStorage của Chrome. Phím tắt chỉ có hiệu lực khi đồ thị được focus, không chiếm phím khi soạn câu hỏi hoặc đọc câu trả lời.

Mỗi node có trạng thái chờ/đang trả lời/lỗi và checkbox **Đã hiểu**. Có thể đổi tên node mà không đổi câu hỏi. Tìm kiếm tìm theo câu hỏi hoặc đoạn trích.

Cây và hàng đợi được lưu trong bảng `research_nodes` của `conversations.sqlite3`: câu hỏi, câu trả lời, đoạn trích, quan hệ cha–con, tùy chỉnh từng node, yêu cầu đã chuẩn bị, trạng thái, tên node và dấu “Đã hiểu”. Nội dung đang stream và tiến độ được ghi vào database theo mỗi cập nhật nhận được, để còn đọc được phần đã nhận khi server bị ngắt. Prompt đã điền được lưu tại thời điểm xếp hàng; sửa mẫu sau đó không thay đổi yêu cầu hay lần thử lại. File/ảnh, bản chụp transcript và lịch sử chat được lưu dạng BLOB trong `research_assets`, liên kết qua `research_node_assets`; các file cache chỉ là bản tạm để upload và tự dựng lại từ database. Node và file đính kèm được ghi trong cùng một transaction; các nhánh dùng cùng file không lưu lặp nội dung file.

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
npm --prefix web test
# Kiểm thử chuột/phím bằng Chrome headless, dùng database tạm, không gửi ChatGPT:
.venv\Scripts\python.exe -X utf8 tests/check_research_graph_browser.py
.venv\Scripts\python.exe -X utf8 tests/check_branch_composer_browser.py
.venv\Scripts\python.exe -X utf8 tests/check_markdown_browser.py
```

Backend: `web_api/server.py`, `web_api/service.py`. Frontend: `web/src/main.jsx`, `web/src/style.css`; React được bundle bằng esbuild, không cần Node server khi sử dụng.

Log backend: `caption-web.log`. Sửa backend cần restart tiến trình `web_main.py`; sửa frontend cần build lại và refresh tab. Để chạy foreground và dừng bằng Ctrl+C, dùng `python web_main.py` khi port 8765 chưa được dùng.

API mutation yêu cầu header `X-RTC-Client: web` và cùng origin. Upload tối đa 50 MB/file; API phục vụ attachment chỉ đọc file nằm trong conversation được chọn. Trang có thể đọc các cài đặt nhạy cảm trên localhost, vì vậy không mở port backend ra Internet.

## Cài đặt Windows Live Captions

Trong **Settings → Windows Live Captions**, mở ô chọn ngôn ngữ để app tự đọc danh sách thực tế từ Windows. Ô chọn hiển thị trạng thái đang tải, sau đó mở danh sách ngay; không cần nút tải lại riêng. Chọn **Nhận giọng nói từ micro**, ngôn ngữ phụ đề, rồi bấm **Lưu và áp dụng cho Windows**. Nút này lưu riêng phần Live Captions; ngôn ngữ ở phần Translation là tùy chọn khác.

Lựa chọn lưu trong bảng `livecaptions_preferences` của SQLite, dùng chung với native app. Backend web tự khôi phục lựa chọn đã lưu một lần khi khởi động với cửa sổ Live Captions đang mở, hoặc khi phát hiện Live Captions đóng rồi mở lại. Việc theo dõi chỉ đọc mã cửa sổ/tiến trình, không mở menu liên tục và không tự bật lại cửa sổ đã đóng. Tạo conversation, Start / Continue và Pause không kích hoạt thao tác cài đặt. Nếu Windows chưa sẵn sàng hoặc khôi phục thất bại, app báo trong Settings và không lặp lại tự động; hoàn tất thiết lập trong Windows rồi bấm Lưu và áp dụng để thử lại. Lỗi này không dừng bộ đọc phụ đề.

Backend điều khiển các nút qua UI Automation và xác nhận trạng thái thực tế. Giao diện Windows có thể hiện khi tải danh sách, áp dụng hoặc tự khôi phục cài đặt. Ngôn ngữ chưa sẵn sàng sẽ báo cần hoàn tất thiết lập/tải gói trong Live Captions. Danh sách ngôn ngữ được dùng lại theo cửa sổ, tiến trình và ngôn ngữ Windows hiện tại, không phụ thuộc khóa AppSessionId. Bộ đọc giữ Unicode và đọc từ vùng chứa phụ đề nếu phần tử chữ tạm thời trống. Lỗi đọc thật vẫn hiển thị; nút đóng thông báo không để cùng lỗi xuất hiện lại ở mỗi lần cập nhật giao diện.

Menu Live Captions cần cửa sổ đang được chọn. Nếu người dùng chuyển ứng dụng, app ngừng thao tác, chờ chuột/bàn phím nghỉ ít nhất 1,5 giây rồi thử lại; tối đa 3 lượt, thời gian chờ trong khoảng 20 giây. App đọc lại trạng thái trước khi bật/tắt micro, không đảo công tắc mù và không kéo người dùng về cửa sổ cũ sau khi họ đã chuyển đi. Khôi phục tự động cũng chờ lúc người dùng ngừng thao tác. Nếu vẫn bị gián đoạn, app báo chưa xác nhận thay vì báo thành công; người dùng có thể bấm áp dụng lại. Việc đọc phụ đề tiếp tục độc lập, chuột/bàn phím không bị khóa.

## Lưu cài đặt Chat và chọn tab ChatGPT

Trong **Chat → Tùy chọn Chat**, bấm **Lưu cài đặt Chat** để lưu tab ChatGPT đang chọn (mã tab và tên hiển thị), prompt, Power, số dòng transcript, đính kèm transcript/lịch sử, Auto screenshot và New ChatGPT chat vào `settings.json` dùng chung với native. Các tùy chọn đang chỉnh được dùng ngay khi gửi và giữ lại khi chuyển tab/conversation; chỉ bấm Lưu mới thay đổi mặc định cho lần mở app tiếp theo. Việc sửa/lưu nội dung mẫu prompt vẫn thực hiện trong trình chỉnh sửa prompt.

Mở lại Chat hoặc chuyển conversation không tải lại danh sách Chrome và không đổi tab đã chọn. Khi có tab đã lưu, F5 cũng khôi phục lựa chọn mà không quét Chrome. Bấm **Refresh** để tải lại danh sách: giữ tab đang chọn nếu còn tồn tại, nếu không thì chọn tab đầu tiên; nếu không có tab ChatGPT nào thì mở một tab mới. Bấm Lưu để ghi nhớ lựa chọn mới. Lần đầu chưa có tab đã lưu, app tự tìm/tạo tab một lần. Nếu tab đã lưu bị đóng, yêu cầu gửi có thể báo không tìm thấy tab; dùng Refresh để chọn lại.

Hộp tạo node con vẫn tự tải danh sách và chọn tab đầu tiên khi dùng tab có sẵn; các chế độ tạo cuộc hội thoại mới/tạo nhánh từ node cha giữ tab riêng vừa tạo. Các lượt tải đồng thời dùng chung yêu cầu để tránh mở trùng.

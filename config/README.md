# Giải thích cấu hình mặc định trước khi build

Chỉnh [defaults.json](defaults.json) để đặt giá trị ban đầu cho người dùng cuối. File này được đóng gói vào EXE web và native. Nó không phải file `settings.json` lưu lựa chọn cá nhân khi ứng dụng đang chạy.

Các giá trị “Mặc định” dưới đây tương ứng với file hiện tại. Trong bảng, tên như `settings.chat_max_lines` nghĩa là khóa `chat_max_lines` bên trong nhóm `settings`.

## Cách áp dụng

1. Sửa các giá trị cần thay đổi trong `config/defaults.json`.
2. Chạy từ thư mục gốc dự án:

   ```powershell
   .venv\Scripts\python.exe -m caption.defaults
   ```

3. Commit thay đổi rồi chạy `build_web_exe.bat`. Build kiểm tra cấu hình và đóng gói đúng file của commit được gắn tag.
4. Để xem trải nghiệm người dùng mới, chạy EXE với `--data-dir` trỏ tới một thư mục trống. Không cần xóa dữ liệu đang dùng.

Ví dụ kiểm tra một bản đã build, thay tên EXE bằng phiên bản thực tế:

```powershell
.\dist\RealTimeCaptionWeb-v1.2.3.exe --data-dir D:\rtc-test-defaults
```

Ứng dụng ưu tiên lựa chọn đã lưu trong `settings.json` hoặc database. Mặc định chỉ điền vào những trường chưa có; thay EXE không tự ghi đè lựa chọn của người dùng. Những nút chọn giao diện chưa có cơ chế lưu riêng, như tab mở đầu hoặc tự cuộn Chat realtime, lấy mặc định lúc mở giao diện.

Khi chạy từ source, khởi động lại backend sau khi sửa file. Chỉ refresh Chrome không làm backend đọc lại cấu hình. Với EXE, cần build lại: app đọc file bên trong bundle, không lấy `config/defaults.json` từ thư mục dữ liệu `RTC_DATA_DIR`.

## Quy tắc giá trị

- `true` / `false`: bật / tắt; không đặt trong dấu nháy.
- `null`: không đặt trong dấu nháy. Chỉ được dùng ở các trường cho phép kế thừa hoặc giữ nguyên trạng thái.
- `""`: chuỗi rỗng; không đồng nghĩa với `null`. Ví dụ tên prompt rỗng chọn mẫu mới nhất, còn `null` ở một số nhóm kế thừa lựa chọn Chat.
- Giữ nguyên tên nhóm và tên khóa. JSON không hỗ trợ comment, dấu phẩy cuối hay khóa trùng nhau.
- Các giới hạn số trong bảng là giới hạn được kiểm tra trong `caption/defaults.py`, trừ nơi ghi rõ là quy ước sử dụng. Kiểm tra cấu hình không xác nhận thiết bị, quyền micro, gói ngôn ngữ, API key hoặc khả năng của máy người dùng.
- API key trong file này sẽ đi theo bản phát hành. Để trống các khóa riêng và mã tab của máy phát triển.

| Config | Mặc định | Giá trị và ý nghĩa |
| --- | --- | --- |
| `schema_version` | `1` | Phiên bản cấu trúc file. Hiện chỉ chấp nhận số nguyên `1`; không phải version EXE hoặc Git tag. |

### Quy ước Power dùng chung

| Giá trị | Ý nghĩa |
| --- | --- |
| `-1` | Giữ nguyên mức hiện tại của tab ChatGPT; app không đổi Power. |
| `0` | Instant. |
| `1` | Medium. |
| `2` | High. |

Đây là giá trị app dùng để điều khiển giao diện ChatGPT. Tùy chọn thực tế vẫn phụ thuộc tab và model đang mở. `null`, ở nơi được cho phép, nghĩa là kế thừa một cài đặt khác; không đồng nghĩa với `-1`.

## Settings

### Nguồn transcript và native app

| Config | Mặc định | Giá trị và ý nghĩa |
| --- | --- | --- |
| `settings.chatgpt_plan` | `""` | Gói người dùng khai báo trong web Settings. Bỏ trống để hỏi khi mở app. `"free"` tắt file và ảnh trong các lượt gửi từ web, đồng thời giới hạn 10 lượt gửi qua app cho mỗi tab Chrome. |
| `settings.chatgpt_login_status` | `""` | `"signed_out"` tắt thao tác ChatGPT và chuyển nguồn thu sang Windows Live Captions. Lưu `"signed_in"` trong web Settings cần xác nhận nút tài khoản trên tab ChatGPT trong Chrome. Bỏ trống để hỏi khi mở app. |
| `settings.web_capture_source` | `"windows"` | Nguồn thu của web: `"windows"` đọc Windows Live Captions; `"chatgpt"` đọc chữ từ micro Dictate trong tab ChatGPT riêng. Chỉ chọn một nguồn. |
| `settings.device_label` | `""` | Chuỗi tên thiết bị/nguồn được chọn ở native app. Rỗng để app chọn theo danh sách thiết bị có sẵn. Không chọn micro Chrome hay thay `web_capture_source`. |
| `settings.model` | `"small.en"` | Model Whisper của native khi thu âm trực tiếp. Các lựa chọn có sẵn gồm `tiny.en`, `base.en`, `small.en`, `medium.en`, `distil-large-v3`, `large-v3-turbo`; còn có thể nhập tên model phù hợp. Không ảnh hưởng Windows Live Captions hoặc ChatGPT Dictate. Bộ kiểm tra chỉ kiểm tra đây là chuỗi. |
| `settings.compute` | `"auto"` | Thiết bị chạy Whisper ở native. Giao diện hỗ trợ `"auto"`, `"cuda"`, `"cpu"`. Khả năng dùng CUDA phụ thuộc máy và thư viện cài đặt. Không chọn thiết bị cho Chrome Translator. |
| `settings.always_on_top` | `false` | Giữ cửa sổ chính của native nổi trên các ứng dụng khác. Không điều khiển tab Chrome hay popup Chat realtime. |

### Popup transcript

Nhóm `overlay_*` dành cho cửa sổ phụ đề của native và popup transcript ChatGPT trên web. Đây là cửa sổ khác với popup Chat realtime. Các khóa này không thay đổi diện mạo cửa sổ Windows Live Captions của hệ điều hành.

| Config | Mặc định | Giá trị và ý nghĩa |
| --- | --- | --- |
| `settings.show_overlay` | `true` | Tự hiển thị overlay khi mở native app. Trên web, popup transcript được mở bằng nút riêng; khóa này không tự mở popup web. |
| `settings.overlay_font_size` | `22` | Số nguyên 8–96. Cỡ chữ Tk của popup transcript, dùng đơn vị point của Tk; không phải cỡ chữ CSS px của popup Chat realtime. |
| `settings.overlay_lines` | `2` | Số nguyên 1–10. Số dòng phụ đề giữ lại để hiển thị trong overlay. Không giới hạn số dòng lưu vào transcript. |
| `settings.overlay_alpha` | `0.82` | Số 0.1–1. Độ đục của cửa sổ khi có nền: `1` hoàn toàn đục, số nhỏ hơn trong suốt hơn. Chế độ chỉ chữ xử lý nền theo cách riêng. |
| `settings.overlay_transparent` | `false` | `true` để hiển thị chữ với nền trong suốt; `false` dùng thanh nền. |
| `settings.overlay_click_through` | `false` | `true` cho chuột đi xuyên qua overlay để tương tác cửa sổ phía sau; `false` để tương tác overlay. |
| `settings.overlay_width` | `0` | Chiều rộng theo px. `0` chọn theo kích thước màn hình; dùng số nguyên dương nếu muốn đặt cố định. Bộ kiểm tra hiện chỉ kiểm tra kiểu số nguyên. |
| `settings.overlay_x` | `-1` | Tọa độ X theo px; số âm dùng vị trí ngang tự động, số từ 0 trở lên dùng vị trí chỉ định. |
| `settings.overlay_y` | `-1` | Tọa độ Y theo px; số âm đặt gần đáy màn hình, số từ 0 trở lên dùng vị trí chỉ định. |

### Chat và Summary

Tên mẫu prompt là tên file **không có đuôi `.txt`**, ví dụ `"v1 - default"`. `""` chọn mẫu mới nhất. Nếu tên đã chọn không tồn tại, cơ chế tìm mẫu hiện tại cũng quay về mẫu mới nhất; nếu thư mục không có mẫu thì yêu cầu không gửi được.

| Config | Mặc định | Giá trị và ý nghĩa |
| --- | --- | --- |
| `settings.summary_new_chat` | `true` | Bắt đầu cuộc hội thoại ChatGPT mới khi gửi yêu cầu Summary. `false` tiếp tục cuộc hội thoại đang mở trong tab được chọn. |
| `settings.summary_attach_transcript` | `true` | Đính kèm transcript vào yêu cầu Summary. |
| `settings.summary_max_lines` | `300` | Số nguyên 0–1.000.000. Số dòng transcript gần nhất dùng cho Summary khi đính kèm; `0` lấy tất cả. |
| `settings.summary_prompt` | `""` | Tên mẫu trong `prompts/summarize/`. Đây là mẫu Summary thông thường, không phải prompt tóm tắt của Chat realtime. |
| `settings.summary_chatgpt_power` | `-1` | Power cho Summary, theo bảng Power dùng chung. |
| `settings.summary_chatgpt_selected_tab` | `""` | Mã nội bộ của tab ChatGPT dùng cho Summary. Để rỗng trong bản phát hành; mã tab ở máy phát triển không có ý nghĩa trên máy người dùng. |
| `settings.chat_max_lines` | `300` | Số nguyên 0–1.000.000. Số dòng transcript gần nhất đính kèm trong Chat; `0` lấy tất cả. Không đặt giới hạn transcript cho hai luồng tự động của Chat realtime. |
| `settings.chat_prompt` | `""` | Tên mẫu trong `prompts/chat/`; dùng cho Chat và là nguồn kế thừa của prompt hỏi đáp Chat realtime, tạo nhánh. |
| `settings.chat_chatgpt_power` | `-1` | Power mặc định cho Chat; các nhóm khác có thể kế thừa giá trị này. |
| `settings.chat_attach_history` | `false` | Đính kèm lịch sử chat được lưu trong app vào yêu cầu. Không điều khiển việc ChatGPT giữ lịch sử cuộc hội thoại đang mở. |
| `settings.chat_attach_transcript` | `true` | Đính kèm transcript vào yêu cầu Chat. Người dùng có thể đổi trước khi gửi. |
| `settings.chat_new_chat` | `false` | Tạo cuộc hội thoại mới khi gửi Chat nếu bật. Đây không phải nút mở tab thu âm riêng hay cơ chế tạo hai tab Chat realtime. |
| `settings.chatgpt_selected_tab` | `""` | Mã `window.name` nội bộ của tab ChatGPT đã chọn cho Chat; không phải URL. Để rỗng trong bản phát hành để app tìm/chọn tab trên máy người dùng. |
| `settings.chatgpt_selected_tab_title` | `""` | Tên hiển thị của tab đã lưu, giúp hiện lựa chọn mà không quét lại danh sách. Không dùng tên này để định danh tab. Để rỗng khi phát hành. |
| `settings.chat_screenshot` | `true` | Tự đính kèm ảnh chụp màn hình khi gửi Chat nếu chưa có ảnh đính kèm phù hợp. Là nguồn kế thừa cho cài đặt screenshot của Chat realtime và tạo nhánh. |

### Các trường nghiên cứu cũ trong Settings

Sáu trường sau vẫn còn trong cấu trúc dữ liệu để tương thích. Luồng tạo nhánh web hiện tại dùng nhóm `research` và cài đặt Chat, không đọc các trường này để cấu hình nhánh. Muốn đổi mặc định của chức năng **Nghiên cứu theo nhánh**, sửa nhóm `research` ở phần bên dưới.

| Config | Mặc định | Giá trị và ý nghĩa |
| --- | --- | --- |
| `settings.research_selected_tab` | `""` | Mã tab nghiên cứu của cấu hình cũ; để rỗng. |
| `settings.research_power` | `-1` | Power cũ; bộ kiểm tra vẫn nhận -1–2 nhưng không quyết định Power nhánh web hiện tại. |
| `settings.research_attach_transcript` | `false` | Cờ đính kèm transcript cũ, không phải `research.attach_transcript`. |
| `settings.research_max_lines` | `300` | Giới hạn dòng cũ, số nguyên 0–1.000.000; không phải `research.max_lines`. |
| `settings.research_screenshot` | `false` | Cờ screenshot cũ, không phải `research.screenshot`. |
| `settings.research_instructions` | `""` | Chuỗi chỉ dẫn nghiên cứu cũ. Luồng hiện tại dùng mẫu prompt Chat và câu hỏi của người dùng. |

### New words và Voca

| Config | Mặc định | Giá trị và ý nghĩa |
| --- | --- | --- |
| `settings.vocab_chatgpt_power` | `-1` | Power khi xử lý từ vựng qua ChatGPT. |
| `settings.voca_api_key` | `""` | Khóa kết nối tài khoản Voca. Để người dùng nhập khóa của họ. |
| `settings.voca_collection_id` | `""` | ID collection nhận từ vựng trong Voca. Rỗng dùng collection mặc định của ứng dụng kết nối. |

### Translation

Trên giao diện web hiện chỉ cho chọn `chrome` và `google_web`. Azure, Google Cloud, Gemini và VinAI vẫn được giữ trong backend/cài đặt dùng chung nhưng đã ẩn khỏi Settings web. **Giá trị trong file hiện vẫn là `translation_engine: "azure"`**; nếu bản phát hành web cần mặc định một phương thức đang hiển thị, hãy đổi thành `"chrome"` hoặc `"google_web"` trước khi build.

Nếu người dùng đã lưu phương thức bị ẩn, mở Settings web sẽ hiện lời nhắc chọn phương thức. Chỉ mở hoặc lưu cài đặt không liên quan không tự chuyển engine đó.

| Config | Mặc định | Giá trị và ý nghĩa |
| --- | --- | --- |
| `settings.translation_engine` | `"azure"` | Một trong `azure`, `google`, `chrome`, `google_web`, `gemini`, `vinai`. `chrome` là Translator API chạy trên máy qua Chrome; `google_web` thao tác tab Google Translate. `google` là Google Cloud API, khác với `google_web`. |
| `settings.translation_enabled` | `false` | Bật tự động dịch nội dung mới. Không tự bật thu giọng; nguồn transcript vẫn cần Start. |
| `settings.translation_partial` | `false` | Cho phép dịch khi câu chưa chốt, dành riêng cho Chrome Translator API. `google_web` có cơ chế đồng bộ chữ đang nói riêng, không phụ thuộc cờ này. |
| `settings.translation_auto_scroll` | `true` | Tự cuộn phần Translation xuống cuối khi có nội dung mới. |
| `settings.translation_view` | `"history"` | `history`: xem theo từng câu/lịch sử. `parallel`: đọc đoạn nguyên văn và bản dịch song song. Với engine `google_web`, giao diện luôn dùng `parallel`. |
| `settings.translation_batch_size` | `1` | Số nguyên 1–50. Số câu đã chốt gom thành một nhóm dịch. Không dùng cho cơ chế nối chữ của `google_web`. |
| `settings.translation_batch_timeout` | `5.0` | Số giây 0.1–300. Thời gian chờ tối đa để gửi nhóm chưa đủ số câu, tính từ khi bắt đầu nhóm. Không dùng cho `google_web`. |
| `settings.translation_source` | `"en"` | Mã ngôn ngữ nguồn, theo bảng bên dưới. Độc lập với ngôn ngữ nhận giọng của Windows Live Captions. |
| `settings.translation_target` | `"vi"` | Mã ngôn ngữ đích, phải khác nguồn. |
| `settings.translation_azure_key` | `""` | API key Azure Translator; cấu hình đã ẩn trên web. |
| `settings.translation_azure_region` | `""` | Region đi kèm tài nguyên Azure; cấu hình đã ẩn trên web. |
| `settings.translation_google_key` | `""` | API key Google Cloud Translation; không dùng cho tab Google Translate. Đã ẩn trên web. |
| `settings.translation_gemini_key` | `""` | API key Gemini; đã ẩn trên web. |
| `settings.translation_gemini_model` | `"gemini-3.5-flash-lite"` | Chuỗi tên model được gửi cho Gemini khi dùng engine đó. Giá trị mặc định trong code không đảm bảo model tồn tại hoặc tài khoản có quyền dùng; cần tên được nhà cung cấp/tài khoản hỗ trợ. Đã ẩn trên web. |
| `settings.translation_gemini_prompt` | Prompt dịch có `{source}`, `{target}`, `{text}` | Nội dung prompt Gemini. `{text}` là phần văn bản cần dịch; `{source}` và `{target}` là ngôn ngữ. Giữ `{text}` để engine đưa nội dung vào. File hiện yêu cầu chỉ trả bản dịch, giữ tên riêng, số và thuật ngữ chính xác. Đã ẩn trên web. |
| `settings.translation_device` | `"cpu"` | `cpu`, `cuda` hoặc `auto` cho VinAI offline. Không đổi thiết bị chạy Chrome Translator. Đã ẩn trên web. |

| Mã | Ngôn ngữ |
| --- | --- |
| `en` | Tiếng Anh |
| `vi` | Tiếng Việt |
| `ja` | Tiếng Nhật |
| `ko` | Tiếng Hàn |
| `zh` | Tiếng Trung |
| `fr` | Tiếng Pháp |
| `de` | Tiếng Đức |
| `es` | Tiếng Tây Ban Nha |

Các mã trên được app chấp nhận; từng engine còn có giới hạn riêng. VinAI hiện được tích hợp cho chiều `en` → `vi`. Bản web EXE mặc định không kèm thư viện VinAI, trừ khi build với tùy chọn bổ sung dịch offline.

## Chat realtime

Nhóm này áp dụng khi cuộc hội thoại chưa lưu cấu hình Chat realtime riêng. Hai tab ChatGPT được app quản lý; không cấu hình mã tab trong nhóm này. Khoảng cập nhật không bảo đảm có câu trả lời đúng mỗi N giây: app còn chờ lượt đang xử lý và chỉ gửi lượt tự động khi có transcript mới.

| Config | Mặc định | Giá trị và ý nghĩa |
| --- | --- | --- |
| `realtime_chat.interval` | `30` | Số nguyên 5–3600 giây. Khoảng giữa các lượt kiểm tra/gửi cập nhật tự động cho gợi ý và tóm tắt. |
| `realtime_chat.upload_timeout_seconds` | `600` | Số nguyên 30–1800 giây. Tổng thời gian chờ file/ảnh xuất hiện và ChatGPT sẵn sàng gửi trong Chat realtime, mặc định 10 phút. App cập nhật trạng thái chờ mỗi 5 giây và cho phép Stop. Áp dụng trực tiếp từ bản build cho cả hội thoại đã lưu; không đổi thời gian chờ câu trả lời hoặc timeout của Chat thường. |
| `realtime_chat.prompt` | `null` | Tên mẫu trong `prompts/chat/` dành cho câu hỏi/action người dùng gửi. `null` kế thừa `settings.chat_prompt`; `""` chọn mẫu mới nhất. Không phải prompt sinh danh sách gợi ý. |
| `realtime_chat.power` | `0` (Instant) | Power của lượt lấy gợi ý tự động. Chọn số nguyên -1–2, hoặc `null` để kế thừa `settings.chat_chatgpt_power`. |
| `realtime_chat.reply_power` | `1` (Medium) | Power khi người dùng bấm gợi ý hoặc gửi câu hỏi. Chọn số nguyên -1–2. Hội thoại cũ dùng mặc định này cho đến khi có cài đặt Hỏi đáp riêng. |
| `realtime_chat.summary_power` | `null` | `null` lấy Power Gợi ý khi chưa lưu mức riêng. Có thể chọn số nguyên -1–2 để đặt riêng mức cho tab tóm tắt. |
| `realtime_chat.screenshot` | `null` | `null` kế thừa `settings.chat_screenshot`; hoặc `true`/`false`. Dùng khi người dùng gửi câu hỏi/action, không chụp ảnh ở mỗi lượt tự động sinh gợi ý/tóm tắt. |
| `realtime_chat.actions_prompt` | Prompt gợi ý JSON trong file | Chuỗi có 1–200.000 ký tự sau khi bỏ khoảng trắng đầu/cuối. Nội dung chỉ dẫn tạo danh sách nút hành động; phải giữ cấu trúc JSON như ví dụ dưới đây. App tự nối transcript mới vào cuối. |
| `realtime_chat.summary_prompt` | Prompt tóm tắt tiếng Việt trong file | Chuỗi có 1–200.000 ký tự sau khi bỏ khoảng trắng đầu/cuối. Tóm tắt phần mới, nêu ý chính/quyết định/việc cần làm; app tự nối transcript mới. Không phải tên file trong `prompts/summarize/`. |

Cấu trúc phản hồi cần yêu cầu trong `actions_prompt`:

```json
{
  "actions": [
    {
      "label": "Hỏi lại thời hạn",
      "prompt": "Soạn câu hỏi để làm rõ thời hạn của công việc vừa được nhắc đến."
    }
  ]
}
```

Prompt mặc định yêu cầu 0–6 gợi ý, mỗi `label` dài 1–80 ký tự, mỗi `prompt` dài 1–4.000 ký tự, chỉ trả một khối mã JSON. App còn thêm lời nhắc định dạng sau transcript. Có thể đổi nội dung/hướng gợi ý nhưng không đổi tên `actions`, `label`, `prompt` hoặc yêu cầu thực hiện action ngay trong lượt sinh gợi ý.

## Nghiên cứu theo nhánh

Các trường `null` không ghi đè cài đặt mà giao diện tạo node lấy từ Chat. Cài đặt tạo nhánh đã được người dùng lưu trong database tiếp tục ưu tiên hơn file này.

| Config | Mặc định | Giá trị và ý nghĩa |
| --- | --- | --- |
| `research.prompt` | `null` | Kế thừa `settings.chat_prompt`; hoặc tên mẫu Chat không có đuôi `.txt`. Chuỗi rỗng chọn mẫu mới nhất. |
| `research.power` | `null` | Kế thừa `settings.chat_chatgpt_power`; hoặc số nguyên -1–2. |
| `research.attach_transcript` | `null` | Trong giao diện tạo nhánh, kế thừa lựa chọn đính kèm transcript hiện tại của tab Chat, kể cả lựa chọn chưa lưu. Có thể đặt `true`/`false`. API tạo nhánh gọi trực tiếp không truyền trường này vẫn có fallback không đính kèm, nếu chưa có mặc định đã lưu/đặt rõ. |
| `research.attach_history` | `null` | Kế thừa `settings.chat_attach_history`; hoặc `true`/`false`. Đính kèm lịch sử Chat của app; ngữ cảnh tổ tiên dùng trường riêng bên dưới. |
| `research.max_lines` | `null` | Kế thừa `settings.chat_max_lines`; hoặc số nguyên 0–1.000.000. Giới hạn dòng transcript khi đính kèm; `0` lấy tất cả. Không giới hạn số node tổ tiên. |
| `research.screenshot` | `null` | Kế thừa `settings.chat_screenshot`; hoặc `true`/`false` để đặt riêng screenshot cho nhánh. |
| `research.new_chat` | `false` | Trường tương thích với cơ chế cũ tạo cuộc hội thoại mới. Giao diện tạo node hiện đặt lại trường này thành `false` và điều khiển mở tab bằng `tab_mode`. Giữ `false`, dùng `tab_mode` cho hành vi mong muốn. |
| `research.ancestor_depth` | `0` | Số nguyên -1–10.000. `0`: không đính kèm tổ tiên. `1`: cặp hỏi–đáp của node cha. `2`: cha và ông. `-1`: toàn bộ chuỗi tổ tiên. Nếu cây ngắn hơn giá trị đã đặt, giao diện chỉ lấy số cấp thực có. |
| `research.tab_mode` | `"existing"` | `existing`: dùng tab ChatGPT được chọn. `new`: mở tab ChatGPT mới rồi chọn tab đó. `branch`: tạo nhánh ChatGPT từ response của node cha, cần URL cuộc hội thoại và thứ tự response hợp lệ. |

## Popup Chat realtime

Áp dụng cho popup có phần chat, gợi ý và tóm tắt. Không thay đổi popup transcript hoặc Windows Live Captions. Popup vẫn do Chrome Document Picture-in-Picture quản lý và nổi trên các ứng dụng khác; không có công tắc bật/tắt luôn nổi trong nhóm này.

| Config | Mặc định | Giá trị và ý nghĩa |
| --- | --- | --- |
| `realtime_popup.opacity` | `100` | Số nguyên 40–100, là phần trăm **độ đục**. `100`: không trong suốt. Giao diện hiển thị “Độ trong suốt” bằng `100 - opacity`; ví dụ `opacity: 70` tương ứng trong suốt 30%. |
| `realtime_popup.font_size` | `16` | Số nguyên 1–32 px. Cỡ chữ nội dung chat/tóm tắt, ô nhập và nút gợi ý; một số tiêu đề, bảng và mã được co giãn theo tỷ lệ. Không phóng to toàn bộ thanh điều khiển. |
| `realtime_popup.width` | `1100` | Số nguyên 200–4096 px. Chiều rộng yêu cầu khi mở cửa sổ mới; Chrome có thể điều chỉnh theo màn hình. Không ép đổi kích thước popup đang mở. |
| `realtime_popup.height` | `760` | Số nguyên 200–4096 px. Chiều cao yêu cầu khi mở cửa sổ mới, cùng quy tắc với `width`. |

Cỡ chữ và độ trong suốt mà người dùng đã lưu được ưu tiên. Việc kéo thay đổi kích thước cửa sổ không ghi lại `width`/`height` vào file mặc định.

## Giao diện web

| Config | Mặc định | Giá trị và ý nghĩa |
| --- | --- | --- |
| `web.theme` | `"light"` | `light` hoặc `dark`. Áp dụng khi database chưa lưu giao diện người dùng. Popup Chat realtime dùng chung theme này. |
| `web.initial_tab` | `"transcript"` | Tab chọn khi tải giao diện chính lần đầu: `transcript`, `chat`, `research`, `summary`, `translation`, `vocab`, `realtime`. Chọn tab không tự Start thu giọng hoặc Chat realtime. |
| `web.sidebar_hidden` | `false` | Ban đầu ẩn/hiện danh sách cuộc hội thoại bên trái. Không xóa danh sách hoặc cuộc hội thoại. |
| `web.reading_focus` | `false` | Bật chế độ tập trung đọc khi giao diện chính tải lần đầu. Người dùng có thể bật/tắt tiếp trên giao diện. |
| `web.transcript_auto_scroll` | `true` | Trạng thái tự cuộn ban đầu của transcript ở giao diện chính. Không thay `settings.translation_auto_scroll`. |
| `web.realtime_auto_scroll` | `true` | Trạng thái tự cuộn ban đầu của Chat realtime, cả giao diện chính và popup. Người dùng vẫn có thể tắt trong phiên đang mở. |

Ngoại trừ theme được lưu trong database, các mục trên là giá trị khởi tạo giao diện. App không áp lại chúng ở mỗi lần polling để tránh ghi đè thao tác của người dùng.

## Windows Live Captions

| Config | Mặc định | Giá trị và ý nghĩa |
| --- | --- | --- |
| `livecaptions.microphone` | `null` | `null`: không yêu cầu đổi trạng thái micro của Windows. `true`: nhận cả giọng nói từ micro. `false`: tắt lựa chọn nhận micro của Live Captions. Không điều khiển micro Dictate của ChatGPT. |
| `livecaptions.language` | `""` | Chuỗi tên ngôn ngữ đúng như menu Windows, ví dụ `"English (United States)"`. Rỗng giữ ngôn ngữ hiện tại. Không dùng mã `en`/`vi` của nhóm Translation và không tự cài gói ngôn ngữ còn thiếu. |

Cài đặt Live Captions đã lưu trong database được ưu tiên. Khi nguồn thu là Windows, app có thể khôi phục lựa chọn sau khi phát hiện cửa sổ Live Captions mới và Windows sẵn sàng thao tác; đây không phải lệnh áp lại liên tục mỗi khi tạo cuộc hội thoại.

## Đồ thị nghiên cứu

Áp dụng cho các thuộc tính bố cục chưa được lưu của từng cây. Các node, vị trí, mức zoom và lựa chọn đã lưu của cây không bị reset khi đổi mặc định.

| Config | Mặc định | Giá trị và ý nghĩa |
| --- | --- | --- |
| `research_graph.snap` | `true` | Bám lưới khi kéo node. |
| `research_graph.wheel_zoom` | `true` | Dùng lăn chuột để zoom. Khi tắt, thao tác lăn dùng để di chuyển khung nhìn theo cơ chế của đồ thị. |
| `research_graph.mode` | `"select"` | `select`: công cụ chọn/kéo node. `hand`: công cụ bàn tay để kéo khung nhìn. |
| `research_graph.move_subtree` | `false` | Khi bật, kéo node sẽ kéo theo các node con trong nhánh. |

## Ví dụ chỉnh một bộ mặc định cho web

Đây là các giá trị cần thay trong file đầy đủ, **không phải file thay thế**. Những nhóm/khóa khác phải được giữ lại:

```json
{
  "settings": {
    "web_capture_source": "chatgpt",
    "translation_engine": "chrome",
    "translation_source": "en",
    "translation_target": "vi",
    "translation_partial": true
  },
  "realtime_chat": {
    "interval": 15,
    "power": 0,
    "reply_power": 1,
    "summary_power": 1
  },
  "realtime_popup": {
    "font_size": 12,
    "opacity": 80
  },
  "web": {
    "theme": "dark",
    "initial_tab": "realtime"
  }
}
```

Bộ này mở tab Chat realtime với theme tối, đặt chu kỳ 15 giây, Power Gợi ý Instant và Power Hỏi đáp, Tóm tắt Medium. Popup dùng chữ 12 px và trong suốt 20%. Người dùng cần bấm Start và chuẩn bị quyền micro hoặc gói dịch theo giao diện.

## Khi chỉnh mà chưa thấy thay đổi

| Hiện tượng | Kiểm tra |
| --- | --- |
| Sửa JSON nhưng giao diện vẫn dùng giá trị cũ | Backend đã khởi động lại chưa? Có đang chạy EXE build trước đó không? |
| Bản EXE mới vẫn giữ lựa chọn cũ | `settings.json` hoặc database đã lưu lựa chọn đó. Dùng thư mục dữ liệu trống để kiểm tra mặc định. |
| Đổi `settings.research_power` nhưng nhánh không đổi | Đây là trường cũ. Sửa `research.power` và kiểm tra cài đặt nhánh đã lưu. |
| Đổi `settings.overlay_font_size` nhưng popup Chat realtime không đổi | Sửa `realtime_popup.font_size`. Hai popup dùng hai bộ cài đặt khác nhau. |
| Không thấy Azure/Gemini/VinAI trong Settings web | Các phương thức và trường cấu hình đó đã được ẩn khỏi giao diện web. |
| Đặt tên prompt mà app chọn mẫu khác | Kiểm tra file `.txt` có đúng tên và có được đóng gói trong thư mục prompt tương ứng không. |
| Lệnh kiểm tra báo thiếu/thừa khóa | Giữ đầy đủ cấu trúc theo `schema_version: 1`, không thay cả file bằng đoạn ví dụ rút gọn. |

Các điểm triển khai để đối chiếu khi bổ sung config mới: [bộ đọc và kiểm tra mặc định](../caption/defaults.py), [Settings](../caption/settings.py), [Chat realtime](../web_api/realtime_chat.py), [popup Chat realtime](../web_api/realtime_popup.py), [cài đặt đồ thị và nhánh](../caption/research_store.py).

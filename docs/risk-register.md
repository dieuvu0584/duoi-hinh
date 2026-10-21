# Sổ rủi ro

Mức độ: khả năng xảy ra (K) x tác động (T), thấp/vừa/cao. Đây là đánh giá ban đầu, cập nhật sau Package 0 và sau pilot.

| ID | Rủi ro | K | T | Biện pháp |
|---|---|---|---|---|
| R-1 | Meta đổi API/chính sách, token hết hạn hoặc bị thu hồi, Page bị hạn chế | V | C | Phiên bản API là cấu hình; job kiểm tra token hằng ngày; adapter tách biệt; kill switch; không đăng "cố gắng hết sức". |
| R-2 | Đăng trùng bài khi timeout | V | C | `video_id` làm mỏ neo, `UNKNOWN_RESULT`, đối soát trước khi thử lại, khóa duy nhất theo `intent_key`. |
| R-3 | Đăng bản đã sửa sau khi duyệt, hoặc review an toàn cũ | V | C | `content_hash` ràng buộc duyệt, review, video; kiểm tra lại ngay trước FINISH. Đã có trong `publishing_policy.py` và test. |
| R-4 | Thông tin sai/tin tức nhạy cảm, bôi nhọ, thông tin cá nhân, bản quyền ảnh nguồn | V | C | Cổng an toàn 3 lớp, người duyệt bắt buộc với chủ đề nhạy cảm, không dùng ảnh nguồn nếu chưa rõ giấy phép, ảnh AI luôn có nhãn minh họa. |
| R-5 | Pháp luật Việt Nam về thông tin sai sự thật, quyền hình ảnh, bản quyền | V | C | Chưa được kiểm chứng trong kit. Chủ dự án/luật sư rà soát trước khi đăng mảng news (D-16: không có luật sư, mảng news mặc định tắt). |
| R-6 | Nội dung bị coi là spam/lặp lại, giảm phân phối | V | V | Dedup chủ đề và đáp án, đa dạng hóa mẫu, theo dõi chỉ số; xem chính sách phân phối nội dung của Meta ở Package 0. |
| R-7 | Rebus tiếng Việt sai chơi chữ/dấu, đáp án mơ hồ | C | C | Đáp án do người xác minh, `rebus_spec` tách âm tiết, kiểm tra mơ hồ bằng QA, chấm nhiều người thử. |
| R-8 | Ảnh AI không đủ chất lượng/không nhất quán với manh mối | C | V | Rebus ghép bằng thư viện biểu tượng xác định, giới hạn số lần sinh lại, spike chất lượng ở Package 0. |
| R-9 | Chi phí vượt dự kiến (sinh ảnh/LLM) | V | V | `cost_ledger`, ngân sách ngày/tháng cưỡng chế phía server, ghi chi phí trước khi cho phép retry. |
| R-10 | Nhạc dính bản quyền | V | V | Chỉ dùng nhạc có giấy phép ghi lại; chờ kiểm tra bản quyền của Meta xong; chặn khi có match. |
| R-11 | Lộ bí mật (token, API key) | T | C | Secret store, không log, không đưa vào client/prompt; xoay khóa; quy trình thu hồi. |
| R-12 | Mất dữ liệu / VPS hỏng | T | C | Sao lưu có diễn tập khôi phục (Package F), IaC/compose, object storage. |
| R-13 | Hiểu sai "an toàn" vì LLM tự chấm LLM | V | C | LLM chỉ là bằng chứng; chính sách cuối ở code backend; người duyệt; điểm số không ghi đè lỗi cứng. |
| R-14 | Phụ thuộc một chủ sở hữu duy nhất (bận/ốm) | V | T | Bài quá hạn thì `SKIPPED_LATE`, không tự đăng; kill switch mặc định bật. |
| R-15 | Giao diện che khuất manh mối (UI Reels, zoom/crop) | V | V | Safe zone cấu hình, kiểm tra khung hình khóa, giới hạn zoom, test trên máy thật. |
| R-16 | Prompt injection từ nguồn tin/chủ đề do người dùng nhập | V | C | Dữ liệu ngoài là không tin cậy, LLM không có công cụ/tác dụng phụ, kiểm tra schema và bằng chứng, quyết định ở code (T-1). |
| R-17 | SSRF khi lấy nguồn; XSS/CSRF nếu sau này có giao diện web | V | C | Chặn IP nội bộ, escape đầu ra, CSP, CSRF token (T-2, T-3). |
| R-18 | Đã đăng nhầm mà không gỡ được kịp | V | C | Quy trình gỡ bài và đính chính, cảnh báo, kill switch không phải thu hồi (operations.md mục 1). |
| R-19 | Điều khoản sử dụng của nhà cung cấp ảnh/LLM (quyền thương mại, sở hữu đầu ra, chính sách nội dung), giấy phép font/biểu tượng/nhạc | V | C | Đọc và ghi lại điều khoản mỗi nhà cung cấp ở Package 0; lưu license mỗi asset. |
| R-20 | Tin cũ được đăng khi đã hết thời sự | V | V | `topic_fresh_until`, TTL review theo mảng, chính sách `topic_stale`. |
| R-21 | Race condition giữa kiểm tra và đăng; hai worker cùng đăng | T | C | Khóa dòng trong một transaction, khóa duy nhất, lease (T-9). |
| R-22 | Không có định nghĩa thương hiệu/biên tập nên LLM tự bịa giọng điệu và ranh giới | C | V | `docs/content-guidelines.md`, danh sách từ cấm kiểm tra bằng code. |
| R-23 | Chuỗi cung ứng phần mềm (PyPI, image, ffmpeg) | T | C | Khóa phụ thuộc theo hash, quét lỗ hổng trong CI (T-8). |

K/T: T = thấp, V = vừa, C = cao.
| R-24 | PC cá nhân tắt, ngủ, mất mạng hoặc tự khởi động lại đúng giờ đăng | C | V | Bài quá hạn thành `SKIPPED_LATE` (không đăng muộn bất ngờ), thông báo FCM khi hệ thống trở lại, cấu hình không ngủ, tự khởi động lại Docker (operations.md mục 8). |
| R-25 | Ổ đĩa PC hỏng hoặc bị mã độc, mất dữ liệu và bí mật (sao lưu cũng nằm trên PC, D-21) | T | C | **Rủi ro được chủ dự án chấp nhận có chủ đích.** Giảm nhẹ không tốn tiền: ổ/phân vùng khác, giữ nhiều bản, diễn tập khôi phục một lần, BitLocker, tách quyền `.env`. Xem lại khi đầu tư thêm. |
| R-26 | Lộ dịch vụ ra mạng (nhầm bind cổng) hoặc mất điện thoại có Tailscale | T | C | API chỉ bind localhost/Tailscale, kiểm tra cổng trong checklist, thu hồi node và thiết bị Flutter khi mất máy. |
| R-27 | Chủ dự án không có luật sư nhưng mảng news có rủi ro pháp lý | V | C | `news_lane_enabled=false` mặc định, duyệt tay mọi bài, checklist trước khi bật; hệ thống không tuyên bố đã tuân thủ pháp luật (D-16). |

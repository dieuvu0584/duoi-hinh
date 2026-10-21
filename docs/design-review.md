# Kết quả rà soát thiết kế (v0.1 -> v0.2)

Phạm vi: toàn bộ kit ChatGPT v0.1 (42 file). Phương pháp: đọc hết tài liệu/code, đối chiếu với tài liệu chính thức của Meta (Reels Publishing API, Page Video Reels, Graph API v26.0), chạy thử mã chính sách và công thức render video trên máy dev.

## Phát hiện và cách xử lý
| # | Mức | Phát hiện ở v0.1 | Xử lý ở v0.2 |
|---|---|---|---|
| 1 | Cao | Mục tiêu là Reels nhưng pipeline chỉ ra ảnh tĩnh; Meta Reels API nhận **video** | Thêm pipeline ảnh sang mp4, cổng ffprobe, thông số theo tài liệu Meta: `docs/video-pipeline.md`. Chạy thử công thức ffmpeg ở `spikes/`. |
| 2 | Cao | Review an toàn chỉ hết hạn theo thời gian, không gắn với nội dung (sửa caption/ảnh sau review vẫn đăng được) | Revision bất biến + `content_hash`; duyệt, review, video đều phải khớp hash (`workflow.md`, `data-model.md`, `publishing_policy.py`, 26 test). |
| 3 | Cao | Kill switch ở hai nơi (env và `system_settings`) | DB là nguồn duy nhất, env chỉ khởi tạo (`workflow.md`, `.env.example`). |
| 4 | Cao | Lên lịch bằng Meta (`SCHEDULED`) sẽ làm kill switch/hủy lịch mất tác dụng; không có khái niệm chuẩn bị video trước | Lịch do backend quản lý, luồng hai pha prepare/finish (`publishing-meta.md` D1, `contracts.py`). |
| 5 | Cao | `UNKNOWN_RESULT` chỉ nói "đối soát" chung chung | Dùng `video_id` làm mỏ neo; bảng quyết định đối soát theo `publishing_phase` (`publishing-meta.md` D2). |
| 6 | Cao | Prompt đòi "JSON theo schema trong data-model.md" nhưng không có schema | 4 JSON Schema trong `docs/schemas/`, prompt tham chiếu schema. |
| 7 | Vừa | Rebus tiếng Việt: không có cách kiểm tra đáp án theo âm tiết, dấu, thanh điệu | `rebus_spec` tách âm tiết, bắt buộc người xác minh ở pilot. |
| 8 | Vừa | Hàm chính sách thiếu: ngân sách, giới hạn tần suất, lịch đến hạn/quá hạn, token, media sẵn sàng, bản quyền; datetime không timezone gây lỗi khó đoán | Mở rộng thành 21 điều kiện chặn có mã lý do; datetime ngây thơ bị từ chối rõ ràng. |
| 9 | Vừa | State machine không có bảng chuyển; REJECTED/BLOCKED không định nghĩa | Bảng chuyển trạng thái đầy đủ cho content, job, publishing (`workflow.md`). |
| 10 | Vừa | Data model thiếu: phiên/thiết bị đăng nhập, sổ chi phí, phiên bản prompt, revision, video/audio/symbol, lần gọi LLM, chuỗi nguồn trùng | Bổ sung (`data-model.md`). |
| 11 | Vừa | Không có cơ chế công bố đáp án rebus | Mục `answer_reveals` và quyết định O-4. |
| 12 | Vừa | Kế hoạch 7 tuần lạc quan; rủi ro Meta/ảnh/rebus để cuối | Package 0 (spike) đứng đầu, cổng phát hành G0-G5 (`implementation-roadmap.md`). |
| 13 | Vừa | Đánh giá tỉ lệ 40/30/30 chỉ với ~12 bài là không có ý nghĩa thống kê | Ghi rõ trong roadmap và acceptance; chỉ kết luận định tính cho đến khi đủ bài. |
| 14 | Vừa | Flutter + Celery + VPN cho một chủ sở hữu là nặng | Đề xuất web admin trước (O-1), chờ chủ dự án chốt. |
| 15 | Thấp | Compose không đợi Postgres healthy; mật khẩu DB trong `.env.example` lệch; container chạy root; `pyproject` ghi sẵn dependency thiếu | Đã sửa compose, Dockerfile (user không phải root), `.env.example`; ghi chú dependency theo từng package. |
| 16 | Thấp | Thiếu yêu cầu đo được, rủi ro, câu hỏi mở | `requirements.md`, `risk-register.md`, `decisions.md`. |

## Những gì chưa được kiểm chứng (cố ý không giả định)
- Meta có chấp nhận file mp4 do ffmpeg tạo (GOP đóng, thông số) và thời gian xử lý thực tế.
- Standard access ở chế độ development có đủ cho Page của chính chủ (tránh App Review) hay không.
- FINISH có idempotent không, `ready` còn hiệu lực bao lâu, DRAFT có đăng lại được không, quyền comment/ghim.
- Chính sách nhãn AI hiện hành của Meta và chính sách phân phối nội dung lặp lại.
- Chất lượng thực tế của FLUX/Gemini cho manh mối rebus; độ chính xác của LLM với chơi chữ tiếng Việt.
- Rủi ro pháp lý ở Việt Nam cho mảng tin tức (cần người có chuyên môn rà soát).
Tất cả nằm trong Package 0.

## Xác minh đã thực hiện
- `pytest`: 26 test cho `publishing_policy` đều qua (venv trong thư mục tạm, Python 3.14).
- `ruff` (E,F,I,UP, dòng tối đa 100): không có lỗi. Chưa chạy type-check (mypy/pyright chưa được cấu hình).
- 4 JSON Schema đọc được; `PACKAGE_MANIFEST.json` hợp lệ.
- ffmpeg: render 10 giây 1080x1920 qua cổng ffprobe cục bộ, ~13 giây.
- Chưa chạy: Docker/compose, mọi API bên ngoài (Meta, Anthropic, FLUX, Gemini), kiểm thử trên máy thật.

# Rà soát lần 2 - lỗ hổng bổ sung
| # | Mức | Lỗ hổng tìm thấy | Xử lý |
|---|---|---|---|
| 17 | Cao | Không có mô hình đe dọa: prompt injection từ bài báo/chủ đề người dùng, SSRF khi tải nguồn, XSS/CSRF trong web admin, khởi tạo tài khoản chủ, đua tranh giữa kiểm tra và đăng | `docs/threat-model.md` (T-1..T-13) và mục Security trong acceptance tests. |
| 18 | Cao | Không có quy trình gỡ bài/đính chính; kill switch bị hiểu như thu hồi | `docs/operations.md` mục 1, trạng thái `RETRACTED`, bảng `retractions`; ghi rõ kill switch chỉ ngăn lần đăng sau. |
| 19 | Vừa | `human_review_required` có thể lấy từ đầu ra mô hình | Quy tắc: backend suy ra bằng luật xác định từ mức rủi ro và nhóm nhạy cảm. |
| 20 | Vừa | Tin cũ vẫn đăng được nếu review chưa hết hạn; không có khái niệm thời sự | `topic_fresh_until`, TTL review theo mảng, điều kiện chặn `topic_stale` (có test). |
| 21 | Vừa | Cổng đăng chỉ tin vào cờ `token_valid` không biết kết quả kiểm tra có cũ không; không kiểm tra bản ghi đang ở `READY_TO_PUBLISH`; không kiểm tra revision có còn là bản hiện hành | Thêm `token_checked_at`, `record_status`, `revision_is_current` vào chính sách (34 test qua). |
| 22 | Vừa | Hàm băm `content_hash` chưa định nghĩa chuẩn hóa nên có thể không ổn định | Quy định serialization chuẩn (NFC, khóa sắp xếp, thứ tự trường, `hash_version`) và test golden. |
| 23 | Vừa | Thiếu định nghĩa thương hiệu/biên tập, danh sách từ cấm | `docs/content-guidelines.md`; kiểm tra bằng code. |
| 24 | Vừa | Điều khoản sử dụng của nhà cung cấp ảnh/LLM, giấy phép font/biểu tượng/nhạc chưa được đề cập | R-19, mục 5 của Package 0, lưu license cho mỗi asset. |
| 25 | Vừa | Thiếu chế độ chạy khô (dry run) an toàn cho môi trường không phải production | `docs/operations.md` mục 7, FR-45. |
| 26 | Thấp | Tài liệu cũ còn nhắc Flutter/FCM là mặc định, lệch với quyết định mới | Đã đồng bộ `architecture.md`, `security.md`, README, CLAUDE.md. |
| 27 | Thấp | Chưa có chính sách giữ lại dữ liệu, RPO/RTO, giám sát và cảnh báo | `docs/operations.md` mục 4-5 (RPO/RTO là mục tiêu đề xuất, chủ dự án cần chốt). |

## Còn mở sau lần 2 (không thể giải quyết chỉ bằng tài liệu)
- Mọi mục VERIFY của Meta, chất lượng ảnh/rebus, tư vấn pháp lý, điều khoản nhà cung cấp: cần Package 0.
- Các số mặc định (TTL review, `prepare_lead`, giới hạn 3 bài/ngày, 100 MB, RPO/RTO, safe zone) là giả thuyết khởi điểm, chưa được đo.
- Chưa có cách đánh giá chất lượng LLM trên chơi chữ tiếng Việt: cần bộ mẫu đánh giá (evaluation set) do chủ dự án cung cấp.
- Chưa chạy: Docker, API ngoài, kiểm thử trên điện thoại thật.

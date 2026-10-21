# Yêu cầu sản phẩm (PRD) - Nhìn Hình Đoán Chuyện

## 1. Mục tiêu và phạm vi
Hệ thống một chủ sở hữu giúp tạo, kiểm duyệt, duyệt tay và đăng **Facebook Reels** cho thương hiệu "Nhìn Hình Đoán Chuyện", ba mảng: `daily_life`, `rebus`, `news`. Backend chạy trên VPS; điện thoại/web chỉ là giao diện quản trị.

**Ngoài phạm vi (MVP):** đa người dùng, đăng nền tảng khác (Instagram/TikTok), video do AI sinh, giọng đọc TTS, phụ đề tự động, tự động đăng không qua duyệt, quảng cáo trả phí.

**Giả định cần kiểm chứng:** xem `docs/decisions.md` mục B, C.

## 2. Chỉ số thành công của pilot (giả thuyết)
- Tỷ lệ gói nội dung qua QA > 80%; lỗi đăng < 2%; **0 bài đăng trùng**; 100% bài đăng tự động qua đủ cổng kiểm soát (chưa có tự động hoá ở pilot, nên là 100% bài đều được duyệt tay).
- Tỉ lệ người thử suy ra đúng đáp án (rebus) > 90% trên mẫu nhỏ trước khi đăng.
- Chi phí mỗi bài nằm trong ngân sách chủ dự án đặt; thời gian chủ dự án duyệt mỗi bài được đo, mục tiêu do chủ dự án đặt.

## 3. Yêu cầu chức năng
### Nội dung
- **FR-1** Nhập chủ đề thủ công; thu thập chủ đề tự động chỉ từ nguồn đã đăng ký và đã xem điều khoản (FR-30).
- **FR-2** Phân tích chủ đề bằng LLM, trả JSON đúng schema (`docs/schemas/analyze_topic.schema.json`); lưu đáp án/thông điệp và `clue_mapping` trước khi sinh ảnh.
- **FR-3** Rebus: đáp án tách âm tiết (`rebus_spec`), mỗi âm tiết có ít nhất một manh mối, **người xác minh** trước khi sinh ảnh.
- **FR-4** Tin tức: tách nguồn, ngày đăng, ngày sự kiện, sự kiện đã xác minh, tuyên bố chưa xác minh, quan điểm; không coi bài đăng lại là nguồn độc lập.
- **FR-5** Sinh ảnh qua FLUX/Gemini hoặc ảnh tải lên thủ công; mọi ảnh qua cùng quy trình kiểm tra, nguồn gốc, bản quyền, an toàn.
- **FR-6** Rebus ghép bằng thư viện biểu tượng theo bố cục xác định; chữ tiếng Việt chèn bằng Pillow.
- **FR-7** Chuẩn hóa 1080x1920 không méo ảnh; kiểm tra manh mối nằm trong safe zone.
- **FR-8** Render Reel mp4 theo `video_spec` và qua cổng ffprobe (`docs/video-pipeline.md`).
- **FR-9** QA tự động (khung hình chính) với quy tắc lỗi cứng ghi đè điểm.
- **FR-10** Mỗi revision có "explanation ticket" riêng cho admin.
- **FR-11** Dedup chủ đề và đáp án trong cửa sổ cấu hình.
### An toàn và duyệt
- **FR-20** Ba cổng an toàn: tiếp nhận, nội dung cuối, trước khi đăng. Thiếu/sai/quá hạn/không khớp `content_hash` thì chặn.
- **FR-21** Chủ đề nhạy cảm bắt buộc người duyệt (danh sách ở `.claude/rules/safety-and-publishing.md`).
- **FR-22** Chủ sở hữu duyệt từng revision; sửa nội dung tạo revision mới và mất hiệu lực duyệt cũ.
- **FR-23** Kill switch dừng mọi đường đăng (đã lên lịch và đăng ngay), kiểm tra lại ngay trước khi gọi Meta.
### Đăng bài và vận hành
- **FR-30** Đăng ký nguồn (loại, điều khoản, độ tin cậy, lịch quét).
- **FR-31** Lịch đăng do backend quản lý, múi giờ hiển thị `Asia/Ho_Chi_Minh`, lưu UTC.
- **FR-32** Đăng Reels theo luồng hai pha (chuẩn bị, rồi FINISH tại giờ đăng) với idempotency và đối soát `UNKNOWN_RESULT`.
- **FR-33** Khai báo `is_ai_generated` theo quy tắc; chờ kiểm tra bản quyền của Meta.
- **FR-34** Quy trình công bố đáp án rebus như một mục lịch riêng.
- **FR-35** Thu thập chỉ số theo mốc +1h/+24h/+72h/+7d, chỉ những chỉ số API trả về.
- **FR-36** Thông báo cho chủ sở hữu qua FCM khi cần duyệt, khi lỗi, khi gần hết hạn token/ngân sách.
- **FR-37** Ngân sách và giới hạn số lần sinh lại cưỡng chế phía server.
### Quản trị
- **FR-40** Đăng nhập chủ sở hữu, phiên theo thiết bị, thu hồi thiết bị.
- **FR-41** Bảng điều khiển: hàng đợi duyệt, việc nền, lịch, kill switch, chi phí, chỉ số.
- **FR-43** Gỡ bài/đính chính có lý do, ghi nhận, kiểm toán (`docs/operations.md`).
- **FR-44** Danh sách từ cấm và quy tắc biên tập kiểm tra bằng code (`docs/content-guidelines.md`).
- **FR-45** Chế độ `dry_run` không gọi Meta.
- **FR-42** Nhật ký kiểm toán chỉ-thêm cho mọi quyết định và thay đổi điều khiển.

## 4. Yêu cầu phi chức năng
- **NFR-1 Tin cậy:** việc nền lưu ở PostgreSQL, sống sót khi worker khởi động lại; xử lý "ít nhất một lần" an toàn nhờ idempotency.
- **NFR-2 Bảo mật:** mọi route admin cần xác thực; bí mật chỉ trong secret store; log đã che; kiểm tra file tải lên theo nội dung; truy cập admin qua mạng riêng.
- **NFR-3 Quan sát:** log có cấu trúc, request ID, chỉ số cho việc nền và đăng bài, cảnh báo cho lỗi đăng/ngân sách/token.
- **NFR-4 Hiệu năng:** render một Reel 10 giây trong thời gian chấp nhận được trên VPS (đo ở Package 0, ghi vào tài liệu).
- **NFR-5 Khả năng khôi phục:** sao lưu và diễn tập khôi phục trước khi lên sản xuất.
- **NFR-6 Kiểm thử:** không gọi API thật trong unit test; contract test dựa trên fixture ghi lại; kiểm thử lỗi (fault injection) cho đường đăng.
- **NFR-7 Chi phí:** mọi lệnh gọi nhà cung cấp trả phí đều ghi `cost_ledger` và tôn trọng ngân sách.
- **NFR-8 Địa phương hóa:** văn bản tiếng Việt chuẩn NFC, font hỗ trợ dấu, kiểm thử golden image.

## 5. Phụ thuộc bên ngoài
Anthropic API (phân tích, review), FLUX, Gemini, Meta Graph API (Page, quyền `pages_manage_posts`, `pages_read_engagement`, `pages_show_list`), ffmpeg, object storage, VPS. Model ID, schema, hạn mức và quyền phải kiểm tra lại với tài liệu chính thức khi triển khai.

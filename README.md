# Nhìn Hình Đoán Chuyện — Claude Code handoff kit

Bộ khung kỹ thuật để Claude Code triển khai hệ thống quản trị và tự động hóa nội dung Facebook Reels cho thương hiệu **Nhìn Hình Đoán Chuyện**.

## Mục tiêu
- Ba luồng nội dung: `daily_life`, `rebus`, `news`.
- Tạo gói nội dung có cấu trúc, sinh ảnh qua FLUX/Gemini hoặc ảnh upload thủ công, hậu kỳ ảnh 9:16, kiểm duyệt, duyệt người dùng, lên lịch/đăng Facebook qua Meta Graph API và thu thập số liệu được API hỗ trợ.
- Một chủ sở hữu duy nhất; vẫn bắt buộc xác thực. Backend chạy trên VPS; ứng dụng Android không phải tiến trình điều phối.
- Claude Code phải triển khai theo từng gói, viết test và cập nhật docs; không được tuyên bố một tính năng đã hoạt động nếu chưa có code/test/credential cần thiết.

## Phiên bản 0.2 (đã rà soát thiết kế)
Bắt đầu đọc `docs/design-review.md` (kết quả rà soát), `docs/requirements.md`, `docs/decisions.md`, rồi `docs/implementation-roadmap.md`. Sản phẩm đầu ra là **Reel mp4** (xem `docs/video-pipeline.md`, `docs/publishing-meta.md`). **Package 0** (các spike giảm rủi ro) phải xong trước khi viết code sản phẩm.

## Trạng thái của gói
Đây là **handoff kit + scaffold khởi tạo**, không phải sản phẩm hoàn chỉnh. Provider adapters, database models/migrations, authentication, upload flow, Meta publishing, Flutter app và analytics cần được triển khai/kiểm chứng. Không chứa API key hay token thật.

## Cách bắt đầu với Claude Code
1. Giải nén thư mục vào repo Git mới.
2. Mở thư mục bằng Claude Code.
3. Chạy `/project-status` để kiểm kê hiện trạng.
4. Chạy `/implement-next` để thực hiện **Package A — Backend Foundation** trước.
5. Yêu cầu Claude Code đọc `CLAUDE.md`, toàn bộ `.claude/rules/`, roadmap và acceptance tests trước khi sửa code.

## Stack dự kiến
- Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2.x, Alembic, PostgreSQL.
- Redis + Celery/Celery Beat cho job nền.
- Pillow/OpenCV cho kiểm tra kích thước, bố cục và overlay chữ tiếng Việt.
- Adapter riêng cho Claude, FLUX, Gemini, Meta Graph API.
- Giao diện quản trị: web admin responsive (mặc định, xem `docs/decisions.md` O-1); Flutter chỉ nếu chủ dự án quyết.
- ffmpeg/ffprobe để render và kiểm tra Reel mp4.
- Docker Compose trên Linux/VPS; object storage tương thích S3 khi triển khai.

Các phiên bản thư viện, API model IDs, quyền Meta và schema nhà cung cấp phải được kiểm tra từ tài liệu chính thức tại thời điểm triển khai. Không đoán endpoint hoặc model ID.

## Các lệnh Claude Code trong repo
- `/project-status`: kiểm tra trạng thái repo, TODO và test.
- `/implement-next`: chọn gói tiếp theo theo roadmap, ưu tiên Package A.
- `/review-safety`: rà soát nội dung, publish flow, quyền truy cập và kill switch.

## Thiết lập môi trường
- Sao chép `.env.example` thành `.env`, điền secret qua secret manager hoặc file local không commit.
- Không đưa token/API key vào mobile app, prompt, logs, screenshots hoặc Git.
- Chạy test theo hướng dẫn trong `docs/implementation-roadmap.md` khi các package tương ứng được triển khai.

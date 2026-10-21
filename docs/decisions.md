# Quyết định thiết kế và câu hỏi mở

## A. Đã chốt
| ID | Quyết định | Lý do |
|---|---|---|
| D-1 | **Đăng Facebook Reels (video mp4)**. Ảnh 1080x1920 chỉ là sản phẩm trung gian; có bước render ảnh sang mp4 bằng ffmpeg. | Meta Reels API nhận video, không nhận ảnh (xem `publishing-meta.md`). Chủ dự án chọn hướng này. |
| D-2 | Modular monolith, PostgreSQL là nguồn sự thật, Redis/Celery chỉ để chạy việc nền. | Một chủ sở hữu, quy mô nhỏ. |
| D-3 | **Lên lịch ở phía backend**, không dùng `video_state=SCHEDULED` của Meta. | Để kill switch, kiểm tra trước khi đăng và hủy lịch còn tác dụng đến phút cuối. |
| D-4 | `video_id` do Meta cấp là mỏ neo chống đăng trùng và đối soát. | Chống nhân đôi bài khi timeout. |
| D-5 | Mỗi lần sửa nội dung tạo **revision** mới; duyệt, review an toàn, video và lịch đăng đều gắn với `content_hash`. | Tránh đăng bản đã sửa sau khi duyệt. |
| D-6 | `system_settings` trong DB là nguồn duy nhất của kill switch; biến môi trường chỉ khởi tạo lần đầu. | Tránh hai nguồn lệch nhau. |
| D-7 | Đáp án rebus phải **do người xác minh** trước khi sinh ảnh (giai đoạn pilot). | Chơi chữ tiếng Việt, dấu và thanh điệu: LLM dễ sai. |
| D-8 | Pilot: duyệt tay 100% bài đăng. | Chưa có số liệu để tin cổng tự động. |

## B. Cần chủ dự án quyết (kèm đề xuất mặc định)
| ID | Câu hỏi | Đề xuất | Hạn chốt |
|---|---|---|---|
| O-1 | Giao diện quản trị: web admin hay app Flutter Android? | **Web admin (responsive)** trước, Flutter sau nếu thật sự cần. Nhẹ hơn nhiều, dùng được trên điện thoại. | Trước Package A |
| O-2 | Nhận thông báo ở đâu (cần duyệt bài, lỗi đăng)? | Telegram bot hoặc email. FCM chỉ cần khi có app Flutter. | Trước Package D |
| O-3 | Hosting: VPS nào, truy cập admin qua đâu? | VPS + Tailscale, admin không mở ra internet công cộng. | Trước Package A |
| O-4 | Cách công bố đáp án rebus? | MVP: chủ dự án tự comment sau N giờ. Sau đó thử comment qua API hoặc Reel đáp án nếu Meta cho phép. | Trước Package D |
| O-5 | Âm thanh của Reel? | Bắt đầu im lặng + 1 nguồn nhạc có bản quyền rõ ràng làm thử nghiệm A/B. Không dùng nhạc trending. | Package C |
| O-6 | Tỉ lệ 3 mảng nội dung (đề xuất cũ 40/30/30)? | Giữ làm giả thuyết; với ~12 bài sẽ không kết luận được, xem mục metrics trong roadmap. | Sau pilot |
| O-7 | Ngân sách tối đa mỗi ngày/tháng và số bài tối đa/ngày? | Đặt số nhỏ (ví dụ 1-3 bài/ngày) trong `system_settings`; chủ dự án điền số tiền. | Trước G3 |
| O-8 | Page đăng thử nghiệm riêng để test Meta? | Có: tạo Page test trước khi chạm Page thật. | Package 0 |
| O-9 | Ngôn ngữ/phạm vi pháp lý: ai rà soát rủi ro luật Việt Nam cho mảng tin tức? | Chủ dự án hoặc luật sư xác nhận; kit không thay thế tư vấn pháp lý. | Trước đăng mảng news |
| O-10 | Nguồn tin nào được phép dùng (điều khoản RSS/API)? | Bắt đầu bằng nhập chủ đề thủ công, thêm nguồn tự động từng cái sau khi đọc điều khoản. | Package B |

## C. Điều Package 0 phải xác nhận (VERIFY)
Xem danh sách trong `docs/publishing-meta.md` và `docs/video-pipeline.md`: Standard access có đủ không (khỏi App Review), vòng đời token, thời gian `ready` còn hiệu lực trước FINISH, FINISH có idempotent không, DRAFT có đăng lại được không, quyền comment/ghim bình luận, safe zone thực tế, chính sách nhãn AI hiện hành của Meta.

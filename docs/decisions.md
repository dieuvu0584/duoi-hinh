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
| D-9 (O-1) | **Giao diện quản trị là app Flutter (Android-first).** Backend chỉ là API, không có web admin. | Chủ dự án chọn (2026-10-21). |
| D-10 (O-2) | **Thông báo qua FCM** (push của app Flutter). | Chủ dự án chọn (2026-10-21). |
| D-11 (O-3) | **Backend chạy trên PC cá nhân của chủ dự án (Docker), truy cập từ điện thoại qua Tailscale.** Không mở cổng ra internet. | Chủ dự án chọn (2026-10-21). Hệ quả: xem `operations.md` mục 8 và rủi ro R-24..R-26. |
| D-12 (O-4) | Công bố đáp án rebus (tùy chọn, xem D-19): nếu dùng, MVP chủ dự án tự comment; thử comment qua API hoặc Reel đáp án nếu Meta cho phép (Package 0). | Chấp nhận đề xuất mặc định. |
| D-13 (O-5) | Âm thanh: bắt đầu im lặng + 1 nguồn nhạc có bản quyền rõ ràng làm thử nghiệm A/B. Không dùng nhạc trending. | Chấp nhận đề xuất mặc định. |
| D-14 (O-6) | Tỉ lệ 40/30/30 chỉ là giả thuyết; tháng đầu đánh giá định tính. | Chấp nhận đề xuất mặc định. |
| D-15 (O-8) | **Tạo Page riêng để test Meta** trước khi chạm Page thật. | Chủ dự án chọn có. |
| D-16 (O-9) | Chủ dự án không có luật sư, tự chịu trách nhiệm tuân thủ pháp luật về tin tức. Hệ thống **không** tuyên bố đã tuân thủ luật. Mảng `news` mặc định **tắt** (`news_lane_enabled=false` trong `system_settings`) cho đến khi chủ dự án tự bật sau khi hoàn thành checklist ở `content-guidelines.md`; khi bật, mọi bài news vẫn duyệt tay. | Giảm rủi ro khi chưa có tư vấn pháp lý; chủ dự án có thể bật bất kỳ lúc nào. |
| D-17 (O-10) | Bắt đầu bằng nhập chủ đề thủ công; thêm nguồn tự động từng cái sau khi đọc điều khoản. | Chấp nhận đề xuất mặc định. |
| D-18 (O-7/P-1) | Giới hạn nhỏ 1-3 bài/ngày. **Chủ dự án tự đặt ngân sách ngày/tháng trong màn hình admin của app Flutter.** Nguyên tắc: tiết kiệm tối đa, chứng minh hệ thống chạy ổn rồi mới đầu tư thêm. Mặc định ngân sách = 0, nghĩa là mọi lệnh gọi trả phí bị chặn cho đến khi chủ dự án đặt số. | Chủ dự án chọn (2026-10-21). |
| D-19 (P-3) | **Công bố đáp án rebus là tùy chọn**, có thể không công bố (`answer_reveals` không bắt buộc, mặc định tắt). | Chủ dự án chọn (2026-10-21). Cập nhật D-12. |
| D-21 (P-4) | **Sao lưu giai đoạn đầu chỉ lưu trên ổ cứng local của PC** (RPO 24 giờ, RTO 4 giờ làm mục tiêu). Chủ dự án chấp nhận rủi ro: nếu ổ đĩa hoặc PC hỏng thì mất cả dữ liệu lẫn bản sao lưu. Nên đặt bản sao lưu ở ổ vật lý hoặc phân vùng khác với dữ liệu chạy nếu có, và xem lại quyết định này trước khi đầu tư thêm. | Chủ dự án chọn (2026-10-21) để tiết kiệm; rủi ro được chấp nhận có chủ đích (R-25). |
| D-22 (P-2) | Hướng dẫn nội dung và thương hiệu (`content-guidelines.md`) được **giữ nguyên các giá trị mặc định**: giọng vui/ấm áp, format Reel, caption <= 150 ký tự (tối đa 300), 3-5 hashtag, danh sách từ cấm theo các nhóm đã nêu. | Chủ dự án chấp nhận (2026-10-21). |
| D-20 (P-5) | Dùng **Page test** để thử Meta; việc tạo app Meta và Page test làm trong Package 0. | Chủ dự án xác nhận (2026-10-21). |

## B. Còn cần chủ dự án cung cấp
| ID | Việc | Hạn |
|---|---|---|
| P-5 | Tạo tài khoản/app Meta, Page test, và (nếu cần) tài khoản nhà cung cấp ảnh để chạy Package 0. Chủ dự án đã đồng ý dùng Page test; cần làm khi bắt đầu Package 0. | Package 0 |

## C. Điều Package 0 phải xác nhận (VERIFY)
Xem danh sách trong `docs/publishing-meta.md` và `docs/video-pipeline.md`: Standard access có đủ không (khỏi App Review), vòng đời token, thời gian `ready` còn hiệu lực trước FINISH, FINISH có idempotent không, DRAFT có đăng lại được không, quyền comment/ghim bình luận, safe zone thực tế, chính sách nhãn AI hiện hành của Meta, API có xóa được Reel không, thời gian render trên PC của chủ dự án.

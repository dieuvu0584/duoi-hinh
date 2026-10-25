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
| D-24 | **Video có giọng đọc thuyết minh tiếng Việt bằng ElevenLabs** để mô tả hình ảnh và đặt câu hỏi. Kịch bản giọng đọc thuộc revision (nằm trong `content_hash`), rebus không bao giờ đọc đáp án, giọng tổng hợp luôn gắn `is_ai_generated=true`. Chủ dự án tự cung cấp `ELEVENLABS_API_KEY` và `ELEVENLABS_VOICE_ID`. | Chủ dự án yêu cầu (2026-10-22). Chi tiết: `video-pipeline.md` mục Narration. |
| D-26 | **Một ảnh gốc vuông 1:1 (1920x1920) sinh bằng fal.ai, cắt giữa (không viền đen) thành hai video từ cùng nội dung và giọng đọc: 9:16 1080x1920 đăng Facebook Reels và 16:9 1920x1080 đăng kênh YouTube.** Chủ thể và mọi manh mối phải nằm trong hình vuông an toàn ở giữa (56,25% mỗi chiều); kiểm tra bằng tọa độ khung manh mối, nếu không vừa thì dùng chế độ `fit` (nền mờ) hoặc sinh lại. Thêm nền tảng đăng YouTube (xem `publishing-youtube.md`), mặc định tắt (`youtube_enabled=false`). | Chủ dự án yêu cầu (2026-10-22). |
| D-27 | **Page Facebook, kênh YouTube và voice ID ElevenLabs do admin cài trong ứng dụng** (`system_settings.facebook_page_id`, `youtube_channel_id`, `elevenlabs_voice_id`); giá trị trong `.env` chỉ là mặc định khi ô trống. Đổi Page hoặc kênh cần nhập lại mật khẩu (quyết định bài đăng đi đâu) và được ghi nhật ký; đổi voice không cần. **Token và khóa API vẫn chỉ nằm trong `.env`** (không bao giờ qua API/ứng dụng). Worker dựng lại provider khi Page đổi. Voice của bài đã tạo nằm trong `content_hash`: đổi voice chỉ áp dụng cho bài mới. | Chủ dự án yêu cầu (2026-10-22). |
| D-28 | **Ý tưởng đi từ tin tức hằng ngày, không còn nhập tay từ đầu.** Quy trình: quét nguồn (RSS các báo Việt Nam + Google News) theo **các khung giờ do chủ chọn** (giờ Việt Nam, ví dụ 10:00, 16:00, 20:00, 00:00; mặc định tắt) -> Claude lọc chỉ giữ ý tưởng tiềm năng (tối đa 10 mỗi lượt, hết hạn sau 48 giờ) -> chủ chọn một ý và chọn **Đời sống** hoặc **Đố hình** -> Claude gợi ý 3 phương án; với đố hình, Claude **lấy thành ngữ trong kho của chủ rồi biến tấu cho hợp tin trong ngày** -> chủ xem, sửa, kiểm tra đáp án -> bấm Tạo bài -> pipeline hiện có. Tin chỉ là **cảm hứng** (không sao chép chữ hay ảnh của báo, không nhắc người thật); tiêu đề là dữ liệu không đáng tin (chỉ đưa vào Claude như dữ liệu); backend loại mọi ý có chủ đề nhạy cảm, rủi ro cao, từ cấm, hoặc câu đố tự lộ đáp án. Mảng news (D-16) vẫn tắt: đây không phải đăng tin. Mỗi lượt quét tốn khoảng USD 0,09-0,10 (đo thật, 80 tiêu đề) và tính vào hạn mức Claude. | Chủ dự án yêu cầu (2026-10-23). |
| D-29 | **Đăng YouTube cùng lúc với Facebook, mỗi nền tảng một bản ghi, một cổng kiểm tra và một lần kiểm tra token riêng.** Facebook nhận video 9:16, YouTube nhận video 16:9 của cùng phiên bản đã duyệt. YouTube tải lên ở chế độ riêng tư rồi mới chuyển công khai khi qua cổng; mặc định tắt (`youtube_enabled`, đổi cần mật khẩu). Token YouTube phải có quyền quản lý (`youtube` hoặc `youtube.force-ssl`), không chỉ `youtube.upload`: thiếu quyền thì coi là không hợp lệ và không tải gì lên. Ứng dụng có **ghi nhớ đăng nhập** (tùy chọn; mật khẩu chỉ lưu trong kho bảo mật của điện thoại, đăng xuất sẽ xóa). | Chủ dự án yêu cầu (2026-10-23). |
| D-30 | **Duyệt xong tự đăng ngay, không hẹn giờ** (cài đặt `publish_on_approve`, mặc định bật, đổi cần mật khẩu). Bấm Duyệt thì bài được xếp hàng đăng tức thì lên Facebook và (nếu bật) YouTube; worker vẫn chạy đủ cổng kiểm tra ngay trước khi tải lên và trước khi công khai. Chỉ xếp hàng khi công tắc dừng đăng đang tắt và tự động đăng đang bật; nếu không thì bài chỉ được duyệt và app nói rõ vì sao chưa đăng. Công tắc dừng vẫn chặn được bài đã xếp hàng. | Chủ dự án yêu cầu (2026-10-24). |
| D-31 | **Vòng phản hồi chất lượng.** Chủ dự án chấm 1-5 sao, gắn nhãn và ghi chú cho từng video (`video_feedback`, mỗi bài một dòng). Claude chỉ ĐỀ XUẤT bài học từ phản hồi gần đây (`POST /api/lessons/distill`, tốn vài cent, cần ít nhất 3 đánh giá); bài học chỉ vào prompt sau khi chủ dự án duyệt (`lessons.status = approved`, tối đa 15 bài cho mỗi lần gọi, đưa vào dữ liệu `channel_lessons` của bước phân tích và bước viết prompt ảnh). Bài học là hướng dẫn phong cách, không bao giờ thay đáp án rebus, luật an toàn hay schema (backend vẫn quyết định). Khi tự động đăng tắt, điểm QA chỉ để tham khảo: không tự tạo lại ảnh. | Chủ dự án yêu cầu (2026-10-24). |
| D-32 | **Đổi tên thương hiệu thành "Memes On Air" và bỏ mảng đố hình (rebus).** Trang Facebook đã đổi tên (Page ID giữ nguyên). Chỉ làm ảnh/video meme hài hước (`daily_life`); API và app không còn nhận ý tưởng mới `rebus`, prompt dặn Claude không chọn mảng này, nguồn ý tưởng chỉ gợi ý `daily_life`. Mã rebus và các bài cũ giữ nguyên để đọc/gỡ được, kho thành ngữ không còn trong app. Prompt thêm hướng dẫn hài hước và điểm hài chiếm 40% với mảng đời sống. | Chủ dự án quyết định (2026-10-25). |
| D-20 (P-5) | Dùng **Page test** để thử Meta; việc tạo app Meta và Page test làm trong Package 0. | Chủ dự án xác nhận (2026-10-21). |

## B. Còn cần chủ dự án cung cấp
| ID | Việc | Hạn |
|---|---|---|
| P-6 | Cung cấp `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, đặt ngân sách riêng cho ElevenLabs trong app (mặc định 0 = chặn mọi lần gọi tốn tiền), và xác nhận giọng được dùng thương mại theo gói tài khoản. | Trước khi chạy thử giọng đọc |
| P-7 | Cho YouTube: kênh (nên có kênh thử nghiệm), dự án Google Cloud, OAuth client và refresh token (`YOUTUBE_*` trong `.env`), để chạy thử upload ở Package 0. | Trước khi bật YouTube |
| P-5 | Tạo tài khoản/app Meta, Page test, và (nếu cần) tài khoản nhà cung cấp ảnh để chạy Package 0. Chủ dự án đã đồng ý dùng Page test; cần làm khi bắt đầu Package 0. | Package 0 |

## B2. Kết quả đo thật (Package 0)
- **Meta chấp nhận mp4 do ffmpeg tạo**; xử lý sau FINISH mất khoảng 17 giây cho video 10 giây.
- **Meta chỉ bắt đầu xử lý sau FINISH**, không phải sau upload. Thiết kế "chuẩn bị tới `ready` rồi mới FINISH" đã sửa thành "chuẩn bị = upload xong + kiểm tra bản quyền sạch" (`publishing-meta.md` D1).
- **Xóa Reel qua API được**, cả bản nháp lẫn bản đã công khai (`DELETE /{video-id}`).
- **Chuyển nháp sang công khai bằng API được** (`POST /{video-id}` với `published=true`, công khai sau vài giây). FINISH lần hai trên cùng video bị từ chối (không tạo bài trùng).
- **D-23: luồng đăng chính = chuẩn bị bản nháp (FINISH ở chế độ DRAFT, chờ `ready` và bản quyền sạch), đến giờ thì qua cổng kiểm tra rồi chuyển nháp sang công khai.** Cách này giữ được kill switch và quyền hủy tới phút cuối, và không có độ trễ xử lý khi lên sóng.
- Token Page có thời hạn khoảng 60 ngày: cần cảnh báo trước khi hết hạn.

- **Giọng đọc ElevenLabs chạy được**: 18 lần thử (3 model x 6 câu) thành công; tốc độ khoảng 3,1-3,3 từ/giây; độ trễ Flash/Turbo khoảng 0,3-0,5 giây, v3 khoảng 2-3,8 giây. Chi phí đo bằng header `character-cost`: khoảng 0,2 đơn vị mỗi ký tự cho Flash/Turbo và 0,4 cho v3 trên gói Starter của bạn (81.227 ký tự/kỳ, không vượt hạn mức). **Chất lượng phát âm chưa đánh giá được vì cần bạn nghe thử** (file ở `spikes/output/tts/`).

- **Tạo ảnh qua fal.ai (model FLUX.2)**: 4 ảnh 9:16 thử thành công, mỗi ảnh khoảng 0,025$ và 10-12 giây; chất lượng đủ dùng cho meme và biểu tượng rebus. Đã dùng khoảng 0,10$ trong ngân sách 4$. Ảnh sinh ra có **chữ giả méo mó dù đã dặn 'no text'** (biển hiệu trong ảnh xe máy), nên QA phải coi chữ méo là lỗi cứng và chữ thật vẫn chỉ thêm bằng Pillow.

- **Kết quả nghe thử giọng đọc (chủ dự án, 2026-10-22):** `eleven_flash_v2_5` và `eleven_turbo_v2_5` đọc sai ngày tháng năm (cả bản số lẫn bản đánh vần) và đọc sai cụm "thật no"; bản `eleven_v3` không có lỗi nào được báo. **D-25: dùng `eleven_v3` cho giọng đọc** (đắt gấp đôi trên mỗi ký tự, vẫn không đáng kể, chậm hơn nhưng render ngoại tuyến nên không ảnh hưởng).

- **Hai bản video từ ảnh vuông (đã làm thử):** cả hai tạo được, tràn khung, không viền đen, qua ffprobe, âm lượng -15,5 LUFS. **Mô hình không giữ được chủ thể trong hình vuông giữa** dù đã dặn (đuôi mèo, mép đồng hồ bị cắt) nên bắt buộc có bước kiểm tra bố cục và chế độ `fit` dự phòng. Ảnh 1920x1920 tốn khoảng 0,044$.

- **Kiểm tra bố cục bằng Claude chạy được:** Claude trả tọa độ manh mối chính xác (đã xem khung vẽ lên ảnh), khoảng 0,012$ mỗi ảnh; code (`composition.py`, 13 test) quyết định cắt hay dùng nền mờ cho từng bản video, và thiếu tọa độ thì mặc định không cắt.

## C. Điều Package 0 phải xác nhận (VERIFY)
- YouTube: tải thử lên kênh thử nghiệm (riêng tư rồi công khai, `publishAt`, `containsSyntheticMedia`, xóa); tài liệu nói dự án API chưa xác minh không bị khóa riêng tư, cần thử để chắc.

- Giọng đọc: chất lượng tiếng Việt thực tế (dấu, thanh điệu, tên riêng) của `eleven_flash_v2_5` so với `eleven_v3`/`eleven_v4`; giá theo ký tự của gói đang dùng; tốc độ nói thực tế (số âm tiết/giây); giới hạn độ dài mỗi lần gọi và lỗi 401/429/hết hạn mức (tài liệu chính thức không nêu); quyền dùng thương mại của giọng đã chọn.

Xem danh sách trong `docs/publishing-meta.md` và `docs/video-pipeline.md`: Standard access có đủ không (khỏi App Review), vòng đời token, thời gian `ready` còn hiệu lực trước FINISH, FINISH có idempotent không, DRAFT có đăng lại được không, quyền comment/ghim bình luận, safe zone thực tế, chính sách nhãn AI hiện hành của Meta, API có xóa được Reel không, thời gian render trên PC của chủ dự án.

# Ứng dụng của chủ kênh (Flutter)

Màn hình: đăng nhập, danh sách bài (cần duyệt / đang tạo / đã duyệt / đã lên lịch / đã đăng / bị loại), xem và duyệt
bài (xem video 9:16 và 16:9, lời đọc, phiếu giải thích, kiểm tra an toàn), tạo ý tưởng, trạng thái hệ thống
(công tắc DỪNG ĐĂNG, tự động đăng, tạm dừng tạo, chi phí) và cài đặt (Page, kênh YouTube, voice ID, giới hạn, ngân sách).

- Token lưu trong kho an toàn của hệ điều hành (Android Keystore), không lưu trong shared preferences hay log.
- Mọi văn bản từ máy chủ hiển thị dạng chữ thường, không dựng HTML.
- API ở `http://<địa chỉ Tailscale>:8000` (HTTP thường, đã được Tailscale mã hóa); không mở API ra Internet.

```
flutter pub get
flutter analyze && flutter test
flutter run -d windows        # hoặc -d chrome / thiết bị Android
flutter build apk --release   # cần Android SDK cmdline-tools + giấy phép (flutter doctor)
```
Chưa có thông báo đẩy (FCM): cần dự án Firebase của bạn (`google-services.json`), làm ở bước sau.

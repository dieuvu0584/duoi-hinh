import 'package:flutter/material.dart';

import 'api/api_client.dart';

/// Vietnamese text for an error, without leaking request details.
String friendlyError(Object e) {
  if (e is ApiException) {
    if (e.isNetwork) return 'Không kết nối được máy chủ. Kiểm tra Tailscale/mạng.';
    switch (e.code) {
      case 'reauth_failed':
        return 'Sai mật khẩu.';
      case 'rate_limited':
        return 'Thử quá nhiều lần, hãy chờ một lát.';
      case 'invalid_credentials':
        return 'Sai tên đăng nhập hoặc mật khẩu.';
      case 'answer_required':
        return 'Câu đố cần có đáp án do bạn cung cấp.';
      case 'news_lane_disabled':
        return 'Mảng tin tức đang tắt (quyết định D-16).';
      case 'no_video':
        return 'Bản được duyệt chưa có video.';
      case 'review_blocked':
        return 'Bài đã bị chặn, không thể gỡ bằng tay.';
      case 'download_failed':
        return 'Không tải được video, hãy thử lại.';
      case 'not_overridable':
        return 'Bài này bị giữ vì một phát hiện về nội dung nên không thể duyệt tay. Hãy sửa và tạo lại.';
      case 'text_check_failed':
        return 'Chữ trong bài vi phạm kiểm tra bắt buộc (lộ đáp án hoặc từ cấm), không thể duyệt.';
      case 'busy':
        return 'Hệ thống đang tải lên hoặc đăng bài này, hãy thử lại sau một phút.';
      case 'nothing_to_cancel':
        return 'Bài này không có lịch đăng nào để hủy.';
      case 'nothing_to_retract':
        return 'Bài này chưa được đăng nên không có gì để gỡ.';
      case 'provider_unavailable':
        return 'Chưa cấu hình nền tảng đó nên không xóa được bản nháp.';
      case 'scan_in_progress':
        return 'Đang có một lượt quét chờ hoặc đang chạy.';
      case 'no_idioms':
        return 'Kho thành ngữ đang trống. Hãy thêm thành ngữ trước.';
      case 'no_valid_options':
        return 'Chưa có phương án dùng được, hãy thử lại.';
      case 'llm_invalid_output':
        return 'Claude trả kết quả không dùng được, hãy thử lại.';
      case 'llm_not_configured':
        return 'Máy chủ chưa có khóa Claude.';
      case 'idea_closed':
        return 'Ý tưởng này đã được xử lý hoặc đã hết hạn.';
      case 'unsafe_url':
        return 'Chỉ nhận địa chỉ https công khai.';
      case 'source_exists':
        return 'Nguồn này đã có trong danh sách.';
      case 'idiom_exists':
        return 'Thành ngữ này đã có trong kho.';
    }
    if (e.code.startsWith('budget_')) {
      return 'Hết ngân sách cho việc này. Tăng hạn mức trong Cài đặt rồi thử lại.';
    }
    return e.message;
  }
  return 'Có lỗi không mong muốn.';
}

void showMessage(BuildContext context, String text) {
  ScaffoldMessenger.of(context)
    ..hideCurrentSnackBar()
    ..showSnackBar(SnackBar(content: Text(text)));
}

Future<String?> askPassword(BuildContext context, {String reason = 'Xác nhận bằng mật khẩu'}) {
  final controller = TextEditingController();
  return showDialog<String>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: Text(reason),
      content: TextField(
        controller: controller,
        obscureText: true,
        autofocus: true,
        decoration: const InputDecoration(labelText: 'Mật khẩu chủ'),
        onSubmitted: (v) => Navigator.pop(ctx, v),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Hủy')),
        FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text), child: const Text('Xác nhận')),
      ],
    ),
  );
}

Future<String?> askNote(BuildContext context, String title) {
  final controller = TextEditingController();
  return showDialog<String>(
    context: context,
    builder: (ctx) => AlertDialog(
      title: Text(title),
      content: TextField(
        controller: controller,
        maxLength: 500,
        maxLines: 3,
        decoration: const InputDecoration(labelText: 'Ghi chú (không bắt buộc)'),
      ),
      actions: [
        TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Hủy')),
        FilledButton(
            onPressed: () => Navigator.pop(ctx, controller.text), child: const Text('Đồng ý')),
      ],
    ),
  );
}

/// Runs [call] without a password; if the server asks for re-authentication, asks the owner for
/// the password once and runs it again with it. Returns false when the owner cancelled.
Future<bool> withReauth(
  BuildContext context,
  Future<void> Function(String? password) call, {
  String reason = 'Thay đổi này cần mật khẩu',
}) async {
  try {
    await call(null);
    return true;
  } on ApiException catch (e) {
    if (!e.needsReauth) rethrow;
  }
  if (!context.mounted) return false;
  final password = await askPassword(context, reason: reason);
  if (password == null || password.isEmpty) return false;
  await call(password);
  return true;
}

/// Statuses while a video is still being made: the owner may cancel it at any time.
const creatingStatuses = {
  'DRAFT', 'ANALYZING', 'READY_FOR_GENERATION', 'NEEDS_REWORK', 'GENERATING', 'RENDERING',
  'QA_PENDING', 'FINAL_SAFETY_PENDING',
};

String laneLabel(String lane) => switch (lane) {
      'daily_life' => 'Đời sống',
      'rebus' => 'Đố hình',
      'news' => 'Tin tức',
      _ => lane,
    };

String statusLabel(String status) => switch (status) {
      'DRAFT' => 'Mới tạo',
      'ANALYZING' => 'Đang phân tích',
      'READY_FOR_GENERATION' => 'Sẵn sàng tạo',
      'GENERATING' => 'Đang tạo ảnh/giọng',
      'RENDERING' => 'Đang dựng video',
      'QA_PENDING' => 'Đang kiểm tra chất lượng',
      'FINAL_SAFETY_PENDING' => 'Đang kiểm tra an toàn',
      'NEEDS_REWORK' => 'Cần làm lại',
      'HELD_FOR_HUMAN' => 'Cần bạn xem xét',
      'PENDING_APPROVAL' => 'Chờ bạn duyệt',
      'APPROVED' => 'Đã duyệt',
      'SCHEDULED' => 'Đã lên lịch',
      'PUBLISHED' => 'Đã đăng',
      'REJECTED' => 'Đã từ chối',
      'BLOCKED' => 'Bị chặn',
      _ => status,
    };

Color statusColor(BuildContext context, String status) {
  final scheme = Theme.of(context).colorScheme;
  return switch (status) {
    'PENDING_APPROVAL' || 'HELD_FOR_HUMAN' => scheme.tertiary,
    'APPROVED' || 'SCHEDULED' || 'PUBLISHED' => scheme.primary,
    'REJECTED' || 'BLOCKED' => scheme.error,
    _ => scheme.outline,
  };
}

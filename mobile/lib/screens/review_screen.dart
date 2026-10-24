import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:video_player/video_player.dart';

import '../api/api_client.dart';
import '../media_saver.dart';
import '../ui_helpers.dart';
import 'create_screen.dart';

class ReviewScreen extends StatefulWidget {
  const ReviewScreen({super.key, required this.id});

  final String id;

  @override
  State<ReviewScreen> createState() => _ReviewScreenState();
}

const _inPipeline = {
  'DRAFT', 'ANALYZING', 'READY_FOR_GENERATION', 'NEEDS_REWORK', 'GENERATING',
  'RENDERING', 'QA_PENDING', 'FINAL_SAFETY_PENDING'
};

class _ReviewScreenState extends State<ReviewScreen> {
  Map<String, dynamic>? _ticket;
  String? _error;
  bool _busy = false;

  ApiClient get _api => context.read<ApiClient>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final t = await _api.ticket(widget.id);
      if (mounted) {
        setState(() {
          _ticket = t;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = friendlyError(e));
    }
  }

  Future<void> _act(Future<void> Function() action, {String? done, bool pop = false}) async {
    setState(() => _busy = true);
    try {
      await action();
      if (!mounted) return;
      if (done != null) showMessage(context, done);
      if (pop) {
        Navigator.of(context).pop();
        return;
      }
      await _load();
    } catch (e) {
      if (mounted) showMessage(context, friendlyError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  /// What approving will do, in the owner's words (it may publish at once).
  String _approveConsequence(Map<String, dynamic> s) {
    if (s['publish_on_approve'] != true) {
      return 'Bài sẽ chuyển sang "Đã duyệt"; bạn chọn giờ đăng sau.';
    }
    final where = s['youtube_enabled'] == true ? 'Facebook và YouTube' : 'Facebook';
    if (s['publishing_kill_switch'] == true) {
      return 'Công tắc đăng đang TẮT nên bài chỉ được duyệt, chưa đăng. Bật lại ở tab Hệ thống để đăng.';
    }
    if (s['automation_enabled'] != true) {
      return 'Tự động đăng đang TẮT nên bài chỉ được duyệt, chưa đăng. Bật ở tab Hệ thống.';
    }
    return 'Bài sẽ được ĐĂNG NGAY lên $where (sau khi hệ thống tải lên và kiểm tra, khoảng vài phút), không cần hẹn giờ.';
  }

  Future<void> _approve({bool held = false}) async {
    var consequence = 'Bài sẽ chuyển sang "Đã duyệt".';
    try {
      consequence = _approveConsequence(await _api.settings());
    } catch (_) {
      // Settings could not be read: the dialog still works, the server decides.
    }
    if (!mounted) return;
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(held ? 'Tự duyệt bài này?' : 'Duyệt bài này?'),
        content: Text((held
                ? 'Kiểm tra an toàn tự động của Claude không chạy xong. Bạn tự chịu trách nhiệm: '
                    'hãy xem hết video, đọc chú thích và lời đọc trước khi duyệt. '
                    'Việc kiểm tra bắt buộc về lộ đáp án và từ cấm vẫn được chạy.\n\n'
                : 'Bạn xác nhận đã xem video, lời đọc và chú thích. Mọi chỉnh sửa sau đó sẽ làm lệnh duyệt mất hiệu lực.\n\n') +
            consequence),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Hủy')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Duyệt')),
        ],
      ),
    );
    if (ok != true) return;
    String? message;
    await _act(() async {
      final result =
          held ? await _api.approveHeld(widget.id) : await _api.approve(widget.id);
      final publish = Map<String, dynamic>.from(result['publish'] as Map? ?? {});
      message = publish['queued'] == true
          ? 'Đã duyệt và xếp hàng đăng ngay lên ${(publish['platforms'] as List).map((p) => p == 'youtube' ? 'YouTube' : 'Facebook').join(' + ')}.'
          : switch ('${publish['reason']}') {
              'kill_switch' => 'Đã duyệt. Chưa đăng vì công tắc đăng đang tắt.',
              'automation_disabled' => 'Đã duyệt. Chưa đăng vì tự động đăng đang tắt.',
              'disabled' => 'Đã duyệt. Bạn chọn giờ đăng ở nút "Lên lịch đăng".',
              _ => 'Đã duyệt nhưng chưa xếp hàng đăng (${publish['reason']}). Hãy lên lịch bằng tay.',
            };
    }, done: null);
    if (mounted && message != null) showMessage(context, message!);
  }

  /// Approved but not queued: take the approval back (the item becomes "rejected").
  Future<void> _withdrawApproval() async {
    final note = await askNote(context, 'Hủy duyệt bài này');
    if (note == null) return;
    await _act(() => _api.reject(widget.id, note: note.isEmpty ? 'Hủy duyệt' : note),
        done: 'Đã hủy duyệt', pop: true);
  }

  Future<void> _cancelSchedule() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Hủy lịch đăng?'),
        content: const Text(
            'Bài chưa lên công khai sẽ không được đăng nữa, bản nháp đã tải lên Facebook/YouTube '
            '(nếu có) sẽ bị xóa. Bài quay về trạng thái "Đã duyệt": bạn có thể lên lịch lại hoặc hủy duyệt.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Giữ lịch')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Hủy lịch')),
        ],
      ),
    );
    if (ok == true) await _act(() => _api.cancelSchedule(widget.id), done: 'Đã hủy lịch đăng');
  }

  Future<void> _retract() async {
    final reason = TextEditingController();
    final password = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Gỡ bài đã đăng'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          const Text('Bài sẽ bị xóa khỏi nền tảng. Việc này không hoàn tác được.'),
          TextField(controller: reason, decoration: const InputDecoration(labelText: 'Lý do gỡ (bắt buộc)')),
          TextField(
              controller: password,
              obscureText: true,
              decoration: const InputDecoration(labelText: 'Mật khẩu')),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Hủy')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Gỡ bài')),
        ],
      ),
    );
    if (ok != true) return;
    if (reason.text.trim().length < 3 || password.text.isEmpty) {
      if (mounted) showMessage(context, 'Cần nhập lý do (ít nhất 3 ký tự) và mật khẩu.');
      return;
    }
    await _act(() => _api.retractPublished(widget.id, reason: reason.text.trim(), password: password.text),
        done: 'Đã gửi lệnh gỡ bài');
  }

  Future<void> _reject() async {
    final note = await askNote(context, 'Từ chối bài này');
    if (note == null) return;
    await _act(() => _api.reject(widget.id, note: note.isEmpty ? null : note),
        done: 'Đã từ chối', pop: true);
  }

  Future<void> _schedule() async {
    // Which platforms: Facebook always; YouTube only when it is switched on in Settings.
    var youtubeOn = false;
    try {
      youtubeOn = (await _api.settings())['youtube_enabled'] == true;
    } catch (_) {
      youtubeOn = false;
    }
    if (!mounted) return;
    final platforms = await showDialog<List<String>>(
      context: context,
      builder: (ctx) {
        var facebook = true;
        var youtube = youtubeOn;
        return StatefulBuilder(
          builder: (ctx, setLocal) => AlertDialog(
            title: const Text('Đăng lên đâu?'),
            content: Column(mainAxisSize: MainAxisSize.min, children: [
              CheckboxListTile(
                value: facebook,
                onChanged: (v) => setLocal(() => facebook = v ?? false),
                title: const Text('Facebook Reels (9:16)'),
              ),
              CheckboxListTile(
                value: youtube,
                onChanged: youtubeOn ? (v) => setLocal(() => youtube = v ?? false) : null,
                title: const Text('YouTube (16:9)'),
                subtitle: youtubeOn ? null : const Text('Đang tắt. Bật ở Cài đặt, mục Đăng bài.'),
              ),
            ]),
            actions: [
              TextButton(onPressed: () => Navigator.pop(ctx), child: const Text('Hủy')),
              FilledButton(
                onPressed: !facebook && !youtube
                    ? null
                    : () => Navigator.pop(ctx, [if (facebook) 'facebook', if (youtube) 'youtube']),
                child: const Text('Tiếp tục'),
              ),
            ],
          ),
        );
      },
    );
    if (platforms == null || !mounted) return;
    final now = DateTime.now();
    final date = await showDatePicker(
      context: context,
      initialDate: now,
      firstDate: now,
      lastDate: now.add(const Duration(days: 60)),
    );
    if (date == null || !mounted) return;
    final time = await showTimePicker(
        context: context, initialTime: TimeOfDay.fromDateTime(now.add(const Duration(hours: 1))));
    if (time == null) return;
    final when = DateTime(date.year, date.month, date.day, time.hour, time.minute);
    await _act(() => _api.schedule(widget.id, when, platforms: platforms),
        done: 'Đã lên lịch đăng (${platforms.map((p) => p == 'youtube' ? 'YouTube' : 'Facebook').join(' + ')})');
  }

  @override
  Widget build(BuildContext context) {
    final t = _ticket;
    return Scaffold(
      appBar: AppBar(title: const Text('Xem và duyệt')),
      body: _error != null && t == null
          ? Center(child: Padding(padding: const EdgeInsets.all(24), child: Text(_error!)))
          : t == null
              ? const Center(child: CircularProgressIndicator())
              : RefreshIndicator(onRefresh: _load, child: _body(t)),
      bottomNavigationBar: t == null ? null : _actions(t),
    );
  }

  Widget _body(Map<String, dynamic> t) {
    final rev = Map<String, dynamic>.from(t['revision'] as Map);
    final explanation = Map<String, dynamic>.from(t['explanation'] as Map? ?? {});
    final analysis = Map<String, dynamic>.from(explanation['analysis'] as Map? ?? {});
    final safety = Map<String, dynamic>.from(t['safety'] as Map? ?? {});
    final videos = List<Map<String, dynamic>>.from(t['videos'] as List);
    final status = '${t['status']}';
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Row(children: [
          Chip(
            label: Text(statusLabel(status)),
            side: BorderSide(color: statusColor(context, status)),
          ),
          const SizedBox(width: 8),
          Text(laneLabel('${t['lane']}')),
        ]),
        if (t['hold'] != null) _holdCard(Map<String, dynamic>.from(t['hold'] as Map)),
        if (t['waiting'] != null) _waitingCard(Map<String, dynamic>.from(t['waiting'] as Map)),
        if ((t['explanation'] as Map?)?['qa_flag'] != null)
          _qaFlagCard(Map<String, dynamic>.from((t['explanation'] as Map)['qa_flag'] as Map)),
        if (_inPipeline.contains(status))
          Card(
            color: Theme.of(context).colorScheme.secondaryContainer,
            margin: const EdgeInsets.only(top: 12),
            child: const Padding(
              padding: EdgeInsets.all(12),
              child: Text(
                  'Bài đang được tạo nên chưa thể duyệt. Dựng video có thể mất vài phút; '
                  'kéo xuống để cập nhật. Khi xong, trạng thái chuyển thành "Chờ bạn duyệt" '
                  'và nút Duyệt sẽ xuất hiện ở cuối màn hình.'),
            ),
          ),
        if (videos.isEmpty)
          const Padding(
              padding: EdgeInsets.symmetric(vertical: 24),
              child: Text('Chưa có video cho bản hiện tại.'))
        else
          _VideoPreview(videos: videos, contentId: widget.id),
        const SizedBox(height: 16),
        _section('Chữ trên video', '${rev['overlay_text'] ?? ''}'),
        _section('Chú thích đăng kèm',
            '${rev['caption'] ?? ''}\n${(rev['hashtags'] as List? ?? []).join(' ')}'),
        _section('Lời đọc (giọng AI)', '${rev['narration_text'] ?? ''}'),
        if (rev['answer_text'] != null)
          _section('Đáp án / ý nghĩa (chỉ bạn thấy, không đăng)', '${rev['answer_text']}'),
        _explanation(explanation, analysis, rev),
        _safety(safety),
        const SizedBox(height: 8),
        Text('Phiên bản ${rev['revision_no']} · mã ${'${rev['content_hash']}'.substring(0, 10)}',
            style: Theme.of(context).textTheme.bodySmall),
        const SizedBox(height: 80),
      ],
    );
  }

  /// Why the pipeline stopped, in plain words, and what the owner can do about it.
  Widget _holdCard(Map<String, dynamic> hold) {
    final stage = '${hold['stage']}';
    final kind = '${hold['kind']}';
    final reason = hold['reason'] == null ? '' : '${hold['reason']}';
    if (kind != 'held') return _rejectedCard(kind, stage, reason);
    final advice = hold['can_override'] == true
        ? 'Kiểm tra an toàn tự động không chạy xong (Claude trả kết quả lỗi), không phải do nội dung có vấn đề. '
            'Video đã dựng xong: bạn xem kỹ rồi bấm "Duyệt" nếu thấy ổn.'
        : switch (stage) {
      'render' =>
        'Dựng video bị lỗi (thường do máy quá chậm). Bấm "Thử lại" để dựng lại; ảnh và giọng đọc đã có nên không tốn thêm tiền.',
      'generate' =>
        'Claude thấy ý tưởng này khó vẽ rõ ràng. Bạn có thể bấm "Thử lại" (tốn khoảng \$0,05) hoặc "Sửa và tạo lại" để đơn giản hóa mô tả.',
      _ =>
        'Hệ thống giữ lại bài này để bạn xem xét. Bạn có thể sửa ý tưởng rồi tạo lại, hoặc từ chối.',
    };
    return Card(
      color: Theme.of(context).colorScheme.tertiaryContainer,
      margin: const EdgeInsets.only(top: 12),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Vì sao bài bị giữ lại', style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 4),
          Text(advice),
          if (reason.isNotEmpty) ...[
            const SizedBox(height: 6),
            SelectableText('Chi tiết: $reason', style: Theme.of(context).textTheme.bodySmall),
          ],
        ]),
      ),
    );
  }

  /// The pipeline is paused for a reason the owner can fix (most often the daily budget).
  Widget _waitingCard(Map<String, dynamic> waiting) {
    final reason = '${waiting['reason']}';
    final text = reason.contains('budget_')
        ? 'Bài đang chờ vì đã hết ngân sách (${reason.contains('daily') ? 'trong ngày' : 'trong tháng'}). '
            'Tăng hạn mức ở Cài đặt, mục Ngân sách (cần mật khẩu), rồi quay lại bấm "Chạy lại ngay".'
        : reason.contains('voice_not_configured')
            ? 'Bài đang chờ vì chưa chọn giọng đọc. Điền Voice ID ở Cài đặt rồi bấm "Chạy lại ngay".'
            : 'Bài đang tạm dừng: $reason. Hệ thống tự thử lại sau ít phút, hoặc bấm "Chạy lại ngay".';
    return Card(
      color: Theme.of(context).colorScheme.errorContainer,
      margin: const EdgeInsets.only(top: 12),
      child: Padding(padding: const EdgeInsets.all(12), child: Text(text)),
    );
  }

  /// The automatic quality check was not happy, but the owner decides (nothing is thrown away).
  Widget _qaFlagCard(Map<String, dynamic> flag) {
    final issues = List<Object?>.from(flag['issues'] as List? ?? []);
    return Card(
      color: Theme.of(context).colorScheme.tertiaryContainer,
      margin: const EdgeInsets.only(top: 12),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Cảnh báo chất lượng', style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 4),
          Text('Kiểm tra tự động chấm ${flag['score']} điểm (đạt từ 80). '
              'Hãy xem video kỹ rồi tự quyết định: Duyệt nếu bạn thấy dùng được, Từ chối hoặc '
              '"Sửa và tạo lại" nếu chưa ổn.'),
          for (final i in issues.take(5)) Text('• $i', style: Theme.of(context).textTheme.bodySmall),
        ]),
      ),
    );
  }

  /// The system itself turned the item down (quality check, safety, regeneration limit).
  Widget _rejectedCard(String kind, String stage, String reason) {
    final score = RegExp(r'score ([0-9.]+)').firstMatch(reason)?.group(1);
    final advice = reason.startsWith('qa_rejected')
        ? 'Kiểm tra chất lượng tự động chấm ${score ?? '?'} điểm (dưới 65) nên hệ thống tự loại bài này, '
            'không cần bạn bấm gì. Ảnh không thể hiện đúng ý tưởng; chi tiết ở mục "Lỗi kiểm tra chất lượng" bên dưới.'
        : reason == 'regeneration_limit'
            ? 'Hệ thống đã tạo lại ảnh đủ số lần cho phép nhưng vẫn chưa đạt, nên loại bài này.'
            : kind == 'blocked'
                ? 'Bài bị chặn vì không qua kiểm tra an toàn.'
                : reason.isEmpty
                    ? 'Bài đã bị từ chối.'
                    : 'Hệ thống loại bài này.';
    return Card(
      color: Theme.of(context).colorScheme.errorContainer,
      margin: const EdgeInsets.only(top: 12),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(kind == 'blocked' ? 'Vì sao bài bị chặn' : 'Vì sao bài bị loại',
              style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 4),
          Text(advice),
          if (reason.isNotEmpty) ...[
            const SizedBox(height: 6),
            SelectableText('Chi tiết: $reason', style: Theme.of(context).textTheme.bodySmall),
          ],
        ]),
      ),
    );
  }

  Future<void> _editAndRecreate(Map<String, dynamic> t) async {
    final source = Map<String, dynamic>.from(t['source'] as Map? ?? {});
    final created = await Navigator.of(context).push<void>(MaterialPageRoute<void>(
      builder: (_) => Scaffold(
        appBar: AppBar(title: const Text('Sửa và tạo lại')),
        body: CreateScreen(
          initialLane: '${t['lane']}',
          initialBrief: source['brief'] == null ? '' : '${source['brief']}',
          initialAnswer: source['answer'] == null ? '' : '${source['answer']}',
        ),
      ),
    ));
    if (mounted) await _load();
    return created;
  }

  Widget _section(String title, String text) => Padding(
        padding: const EdgeInsets.only(bottom: 12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(title, style: Theme.of(context).textTheme.labelLarge),
          const SizedBox(height: 2),
          SelectableText(text.trim().isEmpty ? '—' : text.trim()),
        ]),
      );

  Widget _explanation(Map<String, dynamic> e, Map<String, dynamic> analysis, Map<String, dynamic> rev) {
    final clues = (rev['clue_mapping'] as List? ?? []);
    final risks = (analysis['ambiguity_risks'] as List? ?? []);
    final qa = (e['last_qa_issues'] as List? ?? []);
    final notes = (e['notes'] as List? ?? []);
    return ExpansionTile(
      tilePadding: EdgeInsets.zero,
      title: const Text('Phiếu giải thích (chỉ bạn thấy)'),
      initiallyExpanded: true,
      children: [
        Align(
          alignment: Alignment.centerLeft,
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            _section('Hình ảnh thể hiện', '${e['visual_explanation'] ?? ''}'),
            _section('Người xem sẽ hiểu', '${analysis['intended_viewer_inference'] ?? ''}'),
            if (clues.isNotEmpty)
              _section(
                  'Manh mối',
                  clues
                      .map((c) => c is Map ? '• ${c['visual_clue'] ?? c['clue_id']} → ${c['maps_to'] ?? ''}' : '• $c')
                      .join('\n')),
            if (risks.isNotEmpty) _section('Rủi ro hiểu nhầm', risks.map((r) => '• $r').join('\n')),
            if (qa.isNotEmpty) _section('Lỗi kiểm tra chất lượng', qa.map((r) => '• $r').join('\n')),
            if (notes.isNotEmpty)
              _section('Ghi chú hệ thống',
                  notes.map((n) => n is Map ? '• ${n['stage']}: ${n['reason']}' : '• $n').join('\n')),
          ]),
        ),
      ],
    );
  }

  Widget _safety(Map<String, dynamic> safety) {
    final fin = safety['final'] as Map?;
    if (fin == null) return const SizedBox.shrink();
    final findings = (fin['findings'] as List? ?? []);
    final colorScheme = Theme.of(context).colorScheme;
    final blocked = fin['decision'] == 'block';
    return Card(
      color: blocked ? colorScheme.errorContainer : null,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Kiểm tra an toàn: ${fin['decision']} · rủi ro ${fin['risk_level']}',
              style: Theme.of(context).textTheme.titleSmall),
          if ((fin['categories'] as List).isNotEmpty) Text('Chủ đề nhạy cảm: ${(fin['categories'] as List).join(', ')}'),
          if (fin['human_review_required'] == true)
            Text(fin['human_review_complete'] == true
                ? 'Bạn đã xem xét thủ công.'
                : 'Bắt buộc bạn tự xem xét trước khi duyệt.'),
          for (final f in findings) Text('• ${f is Map ? '${f['category']}: ${f['description']}' : f}'),
        ]),
      ),
    );
  }

  Widget? _actions(Map<String, dynamic> t) {
    final status = '${t['status']}';
    final buttons = <Widget>[];
    if (t['needs_human_review_completion'] == true) {
      buttons.add(Expanded(
        child: FilledButton.tonal(
          onPressed: _busy
              ? null
              : () => _act(() => _api.completeHumanReview(widget.id), done: 'Đã ghi nhận bạn xem xét'),
          child: const Text('Tôi đã xem xét kỹ'),
        ),
      ));
    }
    final hold = t['hold'] == null ? null : Map<String, dynamic>.from(t['hold'] as Map);
    final source = Map<String, dynamic>.from(t['source'] as Map? ?? {});
    if (status == 'HELD_FOR_HUMAN' && hold?['can_override'] == true) {
      buttons.add(Expanded(
        child: FilledButton(
          onPressed: _busy ? null : () => _approve(held: true),
          child: const Text('Duyệt'),
        ),
      ));
    }
    if (status == 'HELD_FOR_HUMAN' && hold?['can_resume'] == true) {
      buttons.add(Expanded(
        child: FilledButton(
          onPressed: _busy
              ? null
              : () => _act(() => _api.resumeHeld(widget.id), done: 'Đã cho chạy lại'),
          child: const Text('Thử lại'),
        ),
      ));
    }
    if (status == 'HELD_FOR_HUMAN' && source['brief'] != null) {
      buttons.add(Expanded(
        child: FilledButton.tonal(
          onPressed: _busy ? null : () => _editAndRecreate(t),
          child: const Text('Sửa và tạo lại'),
        ),
      ));
    }
    if ((status == 'REJECTED' || status == 'BLOCKED') && source['brief'] != null) {
      buttons.add(Expanded(
        child: FilledButton.tonal(
          onPressed: _busy ? null : () => _editAndRecreate(t),
          child: const Text('Sửa và tạo lại'),
        ),
      ));
    }
    if (status == 'PENDING_APPROVAL' || status == 'HELD_FOR_HUMAN') {
      buttons.add(Expanded(
        child: OutlinedButton(onPressed: _busy ? null : _reject, child: const Text('Từ chối')),
      ));
    }
    if (status == 'PENDING_APPROVAL') {
      buttons.add(Expanded(
        child: FilledButton(
          onPressed: _busy || t['can_approve'] != true ? null : _approve,
          child: const Text('Duyệt'),
        ),
      ));
    }
    if (status == 'APPROVED') {
      buttons.add(Expanded(
        child: FilledButton(
            onPressed: _busy ? null : _schedule, child: const Text('Lên lịch đăng')),
      ));
      buttons.add(Expanded(
        child: OutlinedButton(
            onPressed: _busy ? null : _withdrawApproval, child: const Text('Hủy duyệt')),
      ));
    }
    if (status == 'SCHEDULED') {
      buttons.add(Expanded(
        child: FilledButton.tonal(
            onPressed: _busy ? null : _cancelSchedule, child: const Text('Hủy lịch đăng')),
      ));
    }
    if (status == 'PUBLISHED') {
      buttons.add(Expanded(
        child: OutlinedButton(
            onPressed: _busy ? null : _retract, child: const Text('Gỡ bài đã đăng')),
      ));
    }
    if (const {
      'DRAFT', 'ANALYZING', 'READY_FOR_GENERATION', 'NEEDS_REWORK', 'GENERATING',
      'RENDERING', 'QA_PENDING', 'FINAL_SAFETY_PENDING'
    }.contains(status)) {
      buttons.add(Expanded(
        child: OutlinedButton(
            onPressed: _busy ? null : () => _act(() => _api.retryNow(widget.id), done: 'Sẽ chạy lại ở lượt kế tiếp'),
            child: const Text('Chạy lại ngay')),
      ));
    }
    if (buttons.isEmpty) return null;
    // At most two buttons per row so labels stay readable on a phone.
    final rows = <Widget>[];
    for (var i = 0; i < buttons.length; i += 2) {
      final pair = buttons.skip(i).take(2).toList();
      rows.add(Padding(
        padding: EdgeInsets.only(top: i == 0 ? 0 : 8),
        child: Row(children: [
          pair[0],
          if (pair.length > 1) ...[const SizedBox(width: 8), pair[1]],
        ]),
      ));
    }
    return SafeArea(
      child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 12), child: Column(mainAxisSize: MainAxisSize.min, children: rows)),
    );
  }
}

/// Plays one of the rendered videos (9:16 Reel or 16:9 YouTube) with the owner's token.
class _VideoPreview extends StatefulWidget {
  const _VideoPreview({required this.videos, required this.contentId});

  final List<Map<String, dynamic>> videos;
  final String contentId;

  @override
  State<_VideoPreview> createState() => _VideoPreviewState();
}

class _VideoPreviewState extends State<_VideoPreview> {
  late String _target = '${widget.videos.first['target']}';
  VideoPlayerController? _controller;
  String? _error;
  double? _downloading; // 0..1 while a download runs

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _open());
  }

  Future<void> _open() async {
    final api = context.read<ApiClient>();
    final video = widget.videos.firstWhere((v) => v['target'] == _target);
    final old = _controller;
    setState(() {
      _controller = null;
      _error = null;
    });
    await old?.dispose();
    try {
      final controller = VideoPlayerController.networkUrl(
        Uri.parse(api.mediaUrl('${video['url']}')),
        httpHeaders: await api.authHeaders(),
      );
      await controller.initialize();
      controller.setLooping(true);
      if (!mounted) {
        await controller.dispose();
        return;
      }
      setState(() => _controller = controller);
    } catch (_) {
      if (mounted) setState(() => _error = 'Không mở được video.');
    }
  }

  Future<void> _download() async {
    final video = widget.videos.firstWhere((v) => v['target'] == _target);
    final api = context.read<ApiClient>();
    final saver = context.read<MediaSaver>();
    final name = 'nhd_${widget.contentId.substring(0, 8)}_$_target.mp4';
    setState(() => _downloading = 0);
    try {
      await saver.saveVideo(
        api: api,
        path: '${video['url']}',
        name: name,
        onProgress: (p) {
          if (mounted) setState(() => _downloading = p);
        },
      );
      if (mounted) showMessage(context, 'Đã lưu vào thư viện ảnh ($name).');
    } catch (e) {
      if (mounted) showMessage(context, friendlyError(e));
    } finally {
      if (mounted) setState(() => _downloading = null);
    }
  }

  @override
  void dispose() {
    _controller?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final c = _controller;
    final current = widget.videos.firstWhere((v) => v['target'] == _target);
    return Column(children: [
      const SizedBox(height: 12),
      SegmentedButton<String>(
        segments: [
          for (final v in widget.videos)
            ButtonSegment(
                value: '${v['target']}',
                label: Text(v['target'] == 'reel_9x16' ? 'Reels 9:16' : 'YouTube 16:9')),
        ],
        selected: {_target},
        onSelectionChanged: (s) {
          setState(() => _target = s.first);
          _open();
        },
      ),
      const SizedBox(height: 8),
      ConstrainedBox(
        constraints: const BoxConstraints(maxHeight: 460),
        child: AspectRatio(
          aspectRatio: (current['width'] as num) / (current['height'] as num),
          child: ColoredBox(
            color: Colors.black,
            child: _error != null
                ? Center(child: Text(_error!, style: const TextStyle(color: Colors.white)))
                : c == null
                    ? const Center(child: CircularProgressIndicator())
                    : GestureDetector(
                        onTap: () => setState(() => c.value.isPlaying ? c.pause() : c.play()),
                        child: Stack(alignment: Alignment.center, children: [
                          VideoPlayer(c),
                          if (!c.value.isPlaying)
                            const Icon(Icons.play_circle_outline, size: 64, color: Colors.white70),
                        ]),
                      ),
          ),
        ),
      ),
      Text('Chế độ: ${current['mode'] == 'fill' ? 'cắt giữa' : 'toàn ảnh + nền mờ'} · '
          '${current['duration_s'] ?? '?'} giây'),
      const SizedBox(height: 8),
      OutlinedButton.icon(
        onPressed: _downloading == null ? _download : null,
        icon: _downloading == null
            ? const Icon(Icons.download_outlined)
            : SizedBox(
                width: 18,
                height: 18,
                child: CircularProgressIndicator(strokeWidth: 2, value: _downloading == 0 ? null : _downloading)),
        label: Text(_downloading == null
            ? 'Tải video này về máy'
            : 'Đang tải… ${(100 * (_downloading ?? 0)).round()}%'),
      ),
    ]);
  }
}

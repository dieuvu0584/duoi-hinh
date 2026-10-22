import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import 'package:video_player/video_player.dart';

import '../api/api_client.dart';
import '../media_saver.dart';
import '../ui_helpers.dart';

class ReviewScreen extends StatefulWidget {
  const ReviewScreen({super.key, required this.id});

  final String id;

  @override
  State<ReviewScreen> createState() => _ReviewScreenState();
}

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

  Future<void> _approve() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Duyệt bài này?'),
        content: const Text(
            'Bạn xác nhận đã xem video, lời đọc và chú thích. Mọi chỉnh sửa sau đó sẽ làm lệnh duyệt mất hiệu lực.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Hủy')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Duyệt')),
        ],
      ),
    );
    if (ok == true) await _act(() => _api.approve(widget.id), done: 'Đã duyệt');
  }

  Future<void> _reject() async {
    final note = await askNote(context, 'Từ chối bài này');
    if (note == null) return;
    await _act(() => _api.reject(widget.id, note: note.isEmpty ? null : note),
        done: 'Đã từ chối', pop: true);
  }

  Future<void> _schedule() async {
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
    await _act(() => _api.schedule(widget.id, when), done: 'Đã lên lịch đăng');
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
            onPressed: _busy ? null : _schedule, child: const Text('Lên lịch đăng Facebook')),
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
    final spaced = <Widget>[];
    for (final b in buttons) {
      if (spaced.isNotEmpty) spaced.add(const SizedBox(width: 8));
      spaced.add(b);
    }
    return SafeArea(
      child: Padding(padding: const EdgeInsets.fromLTRB(16, 8, 16, 12), child: Row(children: spaced)),
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

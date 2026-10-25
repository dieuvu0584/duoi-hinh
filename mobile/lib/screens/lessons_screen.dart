import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../ui_helpers.dart';

const _laneNames = {
  'all': 'Mọi mảng',
  'daily_life': 'Đời sống',
  'rebus': 'Đố hình',
  'news': 'Tin tức',
};

/// Rules for the channel, distilled by Claude from the owner's feedback. Only the ones the owner
/// approves are given to Claude when it designs the next videos.
class LessonsScreen extends StatefulWidget {
  const LessonsScreen({super.key});

  @override
  State<LessonsScreen> createState() => _LessonsScreenState();
}

class _LessonsScreenState extends State<LessonsScreen> {
  List<Map<String, dynamic>>? _lessons;
  Map<String, dynamic>? _summary;
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
      final lessons = await _api.lessons();
      final summary = await _api.feedbackSummary();
      if (mounted) {
        setState(() {
          _lessons = lessons;
          _summary = summary;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = friendlyError(e));
    }
  }

  Future<void> _run(Future<void> Function() action) async {
    setState(() => _busy = true);
    try {
      await action();
      await _load();
    } catch (e) {
      if (mounted) showMessage(context, friendlyError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _distill() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Rút bài học từ đánh giá?'),
        content: const Text('Claude đọc các đánh giá gần đây và ĐỀ XUẤT bài học (tốn vài cent). '
            'Chưa có gì được áp dụng cho đến khi bạn duyệt từng bài.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Hủy')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Rút bài học')),
        ],
      ),
    );
    if (ok != true) return;
    await _run(() async {
      final made = await _api.distillLessons();
      if (mounted) {
        showMessage(context, made == 0 ? 'Chưa có bài học mới nào.' : 'Có $made bài học đề xuất.');
      }
    });
  }

  Future<void> _add() async {
    final text = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Thêm bài học của bạn'),
        content: TextField(
          controller: text,
          maxLength: 300,
          maxLines: 3,
          decoration: const InputDecoration(hintText: 'Ví dụ: Chữ trên video tối đa 4 từ'),
        ),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Hủy')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Thêm')),
        ],
      ),
    );
    if (ok == true && text.text.trim().length >= 3) {
      await _run(() => _api.addLesson(text.text.trim()));
    }
  }

  Widget _tile(Map<String, dynamic> l) {
    final status = '${l['status']}';
    final id = '${l['id']}';
    final reason = '${l['reason']}';
    return Card(
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('${l['text']}'),
          const SizedBox(height: 4),
          Text('${_laneNames['${l['lane']}'] ?? l['lane']}${reason.isEmpty ? '' : ' · $reason'}',
              style: Theme.of(context).textTheme.bodySmall),
          Row(mainAxisAlignment: MainAxisAlignment.end, children: [
            if (status == 'proposed') ...[
              TextButton(
                  onPressed: _busy ? null : () => _run(() => _api.setLessonStatus(id, 'rejected')),
                  child: const Text('Bỏ')),
              FilledButton.tonal(
                  onPressed: _busy ? null : () => _run(() => _api.setLessonStatus(id, 'approved')),
                  child: const Text('Duyệt')),
            ] else
              IconButton(
                tooltip: 'Xóa',
                onPressed: _busy ? null : () => _run(() => _api.deleteLesson(id)),
                icon: const Icon(Icons.delete_outline),
              ),
          ]),
        ]),
      ),
    );
  }

  String _summaryText(Map<String, dynamic> s) {
    final count = (s['count'] as num?)?.toInt() ?? 0;
    if (count == 0) {
      return 'Chưa có video nào được đánh giá. Mở một video và bấm sao ở mục "Đánh giá video này".';
    }
    final byLane = Map<String, dynamic>.from(s['by_lane'] as Map? ?? {});
    final lanes = byLane.entries
        .map((e) => '${_laneNames[e.key] ?? e.key}: ${e.value['average']} sao (${e.value['count']})')
        .join(' · ');
    final ideas = Map<String, dynamic>.from(s['ideas'] as Map? ?? {});
    final ideaCount = (ideas['count'] as num?)?.toInt() ?? 0;
    final ideaText = ideaCount == 0 ? '' : ' $ideaCount ý tưởng đã chấm sao (trung bình ${ideas['average_rating']}).';
    return '$count video đã đánh giá. $lanes$ideaText';
  }

  @override
  Widget build(BuildContext context) {
    final lessons = _lessons;
    final proposed = [for (final l in lessons ?? []) if (l['status'] == 'proposed') l];
    final approved = [for (final l in lessons ?? []) if (l['status'] == 'approved') l];
    return Scaffold(
      appBar: AppBar(title: const Text('Bài học rút kinh nghiệm')),
      body: lessons == null
          ? Center(child: _error != null ? Text(_error!) : const CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 96),
                children: [
                  const Text('Bạn đánh giá video, Claude rút ra quy tắc riêng cho kênh, bạn duyệt rồi '
                      'các quy tắc đó được đưa vào lúc thiết kế video mới. Quy tắc không bao giờ đổi '
                      'đáp án rebus hay luật an toàn.'),
                  const SizedBox(height: 8),
                  if (_summary != null)
                    Text(_summaryText(_summary!), style: Theme.of(context).textTheme.bodySmall),
                  const SizedBox(height: 12),
                  FilledButton.icon(
                    onPressed: _busy ? null : _distill,
                    icon: const Icon(Icons.auto_awesome),
                    label: const Text('Rút bài học từ đánh giá'),
                  ),
                  if (proposed.isNotEmpty) ...[
                    const SizedBox(height: 16),
                    Text('Đề xuất, chờ bạn duyệt (${proposed.length})',
                        style: Theme.of(context).textTheme.titleMedium),
                    for (final l in proposed) _tile(l),
                  ],
                  const SizedBox(height: 16),
                  Text('Đang áp dụng (${approved.length})',
                      style: Theme.of(context).textTheme.titleMedium),
                  if (approved.isEmpty)
                    const Padding(
                        padding: EdgeInsets.symmetric(vertical: 8),
                        child: Text('Chưa có bài học nào được duyệt.')),
                  for (final l in approved) _tile(l),
                ],
              ),
            ),
      floatingActionButton: FloatingActionButton.extended(
          onPressed: _add, icon: const Icon(Icons.add), label: const Text('Thêm')),
    );
  }
}

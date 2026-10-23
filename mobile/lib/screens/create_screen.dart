import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../ui_helpers.dart';

class CreateScreen extends StatefulWidget {
  const CreateScreen({super.key, this.initialLane, this.initialBrief, this.initialAnswer});

  /// Used to correct a held idea and create it again: the old text is already filled in.
  final String? initialLane;
  final String? initialBrief;
  final String? initialAnswer;

  @override
  State<CreateScreen> createState() => _CreateScreenState();
}

class _CreateScreenState extends State<CreateScreen> {
  String _lane = 'daily_life';
  final _brief = TextEditingController();
  final _answer = TextEditingController();
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    if (widget.initialLane == 'daily_life' || widget.initialLane == 'rebus') {
      _lane = widget.initialLane!;
    }
    _brief.text = widget.initialBrief ?? '';
    _answer.text = widget.initialAnswer ?? '';
  }

  Future<void> _submit() async {
    final brief = _brief.text.trim();
    if (brief.isEmpty) {
      showMessage(context, 'Hãy mô tả ý tưởng.');
      return;
    }
    setState(() => _busy = true);
    try {
      await context.read<ApiClient>().createIdea(
            lane: _lane,
            brief: brief,
            answer: _lane == 'rebus' ? _answer.text : null,
          );
      _brief.clear();
      _answer.clear();
      if (widget.initialBrief != null && mounted) {
        showMessage(context, 'Đã xếp hàng tạo lại. Xem ở mục Bài viết (Đang tạo).');
        Navigator.of(context).pop();
        return;
      }
      if (mounted) {
        showMessage(context, 'Đã xếp hàng. Hệ thống sẽ tạo và báo ở mục Bài viết khi chờ bạn duyệt.');
      }
    } catch (e) {
      if (mounted) showMessage(context, friendlyError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        SegmentedButton<String>(
          segments: const [
            ButtonSegment(value: 'daily_life', label: Text('Đời sống')),
            ButtonSegment(value: 'rebus', label: Text('Đố hình')),
          ],
          selected: {_lane},
          onSelectionChanged: (s) => setState(() => _lane = s.first),
        ),
        const SizedBox(height: 16),
        TextField(
          controller: _brief,
          maxLines: 5,
          maxLength: 2000,
          decoration: InputDecoration(
            labelText: _lane == 'rebus' ? 'Gợi ý hình ảnh của câu đố' : 'Ý tưởng / tình huống',
            helperText: 'Mô tả ngắn gọn; hệ thống tự viết lời đọc, chú thích và ảnh.',
            border: const OutlineInputBorder(),
          ),
        ),
        if (_lane == 'rebus') ...[
          const SizedBox(height: 12),
          TextField(
            controller: _answer,
            maxLength: 500,
            decoration: const InputDecoration(
              labelText: 'Đáp án (bạn đã kiểm tra, không đăng công khai)',
              border: OutlineInputBorder(),
            ),
          ),
        ],
        const SizedBox(height: 12),
        FilledButton.icon(
          onPressed: _busy ? null : _submit,
          icon: const Icon(Icons.auto_awesome),
          label: const Text('Tạo bài'),
        ),
        const SizedBox(height: 8),
        Text('Mảng tin tức đang tắt theo quyết định D-16.',
            style: Theme.of(context).textTheme.bodySmall),
      ],
    );
  }

  @override
  void dispose() {
    _brief.dispose();
    _answer.dispose();
    super.dispose();
  }
}

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../ui_helpers.dart';

/// Labels the owner can tap on a video; the keys are what the backend stores.
const feedbackLabels = <String, String>{
  'great': 'Rất hay',
  'funny': 'Hài, duyên',
  'confusing_image': 'Ảnh khó hiểu',
  'unclear_clues': 'Manh mối rối',
  'too_cluttered': 'Quá rối mắt',
  'bad_text': 'Chữ chưa ổn',
  'bad_voiceover': 'Lời đọc dở',
  'too_slow': 'Hơi chậm',
  'answer_too_easy': 'Đáp án quá dễ',
  'answer_too_hard': 'Đáp án quá khó',
};

/// Detailed levels the owner can give, separately for the picture and for the words.
const feedbackGroups = <String, String>{
  'image': 'Hình ảnh',
  'text': 'Câu chữ (chữ trên video, chú thích, lời đọc)',
};
const feedbackDimensions = <String, String>{
  'surprise': 'Bất ngờ',
  'meaning': 'Ý nghĩa',
  'impact': 'Ấn tượng',
  'humor': 'Hài hước',
};
const _levelWords = ['Nhạt', 'Hơi nhạt', 'Ổn', 'Khá mạnh', 'Rất mạnh'];

/// The owner's verdict on a video: stars, a few tags and an optional note. Claude later turns
/// many of these into rules for the channel, which the owner approves (Hệ thống > Bài học).
class FeedbackCard extends StatefulWidget {
  const FeedbackCard({super.key, required this.id, this.initial});

  final String id;
  final Map<String, dynamic>? initial;

  @override
  State<FeedbackCard> createState() => _FeedbackCardState();
}

class _FeedbackCardState extends State<FeedbackCard> {
  int _rating = 0;
  final Set<String> _labels = {};
  final Map<String, Map<String, int>> _scores = {};
  final _note = TextEditingController();
  bool _busy = false;
  bool _saved = false;

  @override
  void initState() {
    super.initState();
    final f = widget.initial;
    if (f != null) {
      _rating = (f['rating'] as num?)?.toInt() ?? 0;
      _labels.addAll(List<Object?>.from(f['labels'] as List? ?? []).map((e) => '$e'));
      _note.text = '${f['note'] ?? ''}';
      final scores = Map<String, dynamic>.from(f['scores'] as Map? ?? {});
      for (final g in scores.entries) {
        _scores[g.key] = {
          for (final d in Map<String, dynamic>.from(g.value as Map).entries)
            d.key: (d.value as num).toInt()
        };
      }
      _saved = _rating > 0;
    }
  }

  @override
  void dispose() {
    _note.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    setState(() => _busy = true);
    try {
      await context
          .read<ApiClient>()
          .saveFeedback(widget.id,
              rating: _rating, labels: _labels.toList(), note: _note.text, scores: _scores);
      if (mounted) {
        setState(() => _saved = true);
        showMessage(context, 'Đã lưu đánh giá.');
      }
    } catch (e) {
      if (mounted) showMessage(context, friendlyError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _levelRow(String group, String dim, String label) {
    final current = _scores[group]?[dim];
    return Row(children: [
      SizedBox(width: 92, child: Text(label)),
      for (var n = 1; n <= 5; n++)
        Padding(
          padding: const EdgeInsets.symmetric(horizontal: 3),
          child: ChoiceChip(
            key: ValueKey('level-$group-$dim-$n'),
            label: Text('$n'),
            tooltip: _levelWords[n - 1],
            visualDensity: VisualDensity.compact,
            showCheckmark: false,
            selected: current == n,
            onSelected: (_) => setState(() {
              final dims = _scores.putIfAbsent(group, () => {});
              if (current == n) {
                dims.remove(dim);
                if (dims.isEmpty) _scores.remove(group);
              } else {
                dims[dim] = n;
              }
              _saved = false;
            }),
          ),
        ),
    ]);
  }

  @override
  Widget build(BuildContext context) {
    return Card(
      margin: const EdgeInsets.only(top: 12),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Đánh giá video này', style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 4),
          Text('Giúp Claude rút kinh nghiệm cho các video sau.',
              style: Theme.of(context).textTheme.bodySmall),
          Row(children: [
            for (var i = 1; i <= 5; i++)
              IconButton(
                tooltip: '$i sao',
                onPressed: () => setState(() {
                  _rating = i;
                  _saved = false;
                }),
                icon: Icon(i <= _rating ? Icons.star : Icons.star_border,
                    color: i <= _rating ? Colors.amber : null),
              ),
          ]),
          Wrap(spacing: 6, runSpacing: 0, children: [
            for (final e in feedbackLabels.entries)
              FilterChip(
                label: Text(e.value),
                selected: _labels.contains(e.key),
                onSelected: (v) => setState(() {
                  v ? _labels.add(e.key) : _labels.remove(e.key);
                  _saved = false;
                }),
              ),
          ]),
          const SizedBox(height: 8),
          ExpansionTile(
            tilePadding: EdgeInsets.zero,
            title: const Text('Chấm chi tiết (không bắt buộc)'),
            subtitle: const Text('Mỗi mục 1 đến 5, chạm lại để bỏ chọn'),
            children: [
              for (final g in feedbackGroups.entries) ...[
                Align(
                    alignment: Alignment.centerLeft,
                    child: Text(g.value, style: Theme.of(context).textTheme.titleSmall)),
                for (final d in feedbackDimensions.entries) _levelRow(g.key, d.key, d.value),
                const SizedBox(height: 8),
              ],
            ],
          ),
          TextField(
            controller: _note,
            maxLength: 500,
            maxLines: 3,
            minLines: 1,
            onChanged: (_) => setState(() => _saved = false),
            decoration: const InputDecoration(
              labelText: 'Ghi chú (không bắt buộc)',
              border: OutlineInputBorder(),
            ),
          ),
          Align(
            alignment: Alignment.centerRight,
            child: FilledButton.tonal(
              onPressed: _busy || _rating == 0 || _saved ? null : _save,
              child: Text(_saved ? 'Đã lưu' : 'Lưu đánh giá'),
            ),
          ),
        ]),
      ),
    );
  }
}

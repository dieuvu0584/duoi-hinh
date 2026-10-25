import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../ui_helpers.dart';
import 'review_screen.dart';

class _Filter {
  const _Filter(this.label, this.status);
  final String label;
  final String? status; // null = the server default: everything that needs the owner
}

const _filters = [
  _Filter('Cần xử lý', null),
  _Filter('Đang tạo',
      'DRAFT,ANALYZING,READY_FOR_GENERATION,NEEDS_REWORK,GENERATING,RENDERING,QA_PENDING,FINAL_SAFETY_PENDING'),
  _Filter('Đã duyệt', 'APPROVED'),
  _Filter('Đã lên lịch', 'SCHEDULED'),
  _Filter('Đã đăng', 'PUBLISHED'),
  _Filter('Bị loại', 'REJECTED,BLOCKED'),
];

class QueueScreen extends StatefulWidget {
  const QueueScreen({super.key});

  @override
  State<QueueScreen> createState() => _QueueScreenState();
}

/// The best name for a row: the on-screen text, else the caption, else the idea it was made from.
String _title(Map<String, dynamic> it) {
  for (final key in ['overlay_text', 'caption', 'brief']) {
    final v = it[key];
    if (v is String && v.trim().isNotEmpty) return v.trim();
  }
  return '(chưa có nội dung)';
}

class _QueueScreenState extends State<QueueScreen> {
  int _filter = 0;
  late Future<List<Map<String, dynamic>>> _items;

  @override
  void initState() {
    super.initState();
    _items = _load();
  }

  Future<List<Map<String, dynamic>>> _load() =>
      context.read<ApiClient>().queue(status: _filters[_filter].status);

  Future<void> _reload() async {
    final next = _load();
    setState(() {
      _items = next;
    });
    await next.catchError((_) => <Map<String, dynamic>>[]);
  }

  Future<void> _cancel(Map<String, dynamic> it) async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Hủy tạo bài này?'),
        content: Text('"${_title(it)}" sẽ dừng ngay, không tốn thêm tiền cho các bước sau. '
            'Bước đang chạy dở (nếu có) vẫn đã tính phí.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Giữ lại')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Hủy tạo bài')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    try {
      await context.read<ApiClient>().cancelCreation('${it['id']}');
      if (mounted) showMessage(context, 'Đã hủy tạo bài.');
    } catch (e) {
      if (mounted) showMessage(context, friendlyError(e));
    }
    if (mounted) _reload();
  }

  Future<void> _open(String id) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => ReviewScreen(id: id)));
    if (mounted) _reload();
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        SizedBox(
          height: 56,
          child: ListView.separated(
            scrollDirection: Axis.horizontal,
            padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
            itemCount: _filters.length,
            separatorBuilder: (_, _) => const SizedBox(width: 8),
            itemBuilder: (context, i) => ChoiceChip(
              label: Text(_filters[i].label),
              selected: _filter == i,
              onSelected: (_) {
                setState(() => _filter = i);
                _reload();
              },
            ),
          ),
        ),
        Expanded(
          child: RefreshIndicator(
            onRefresh: _reload,
            child: FutureBuilder<List<Map<String, dynamic>>>(
              future: _items,
              builder: (context, snap) {
                if (snap.connectionState != ConnectionState.done) {
                  return const Center(child: CircularProgressIndicator());
                }
                if (snap.hasError) {
                  return ListView(children: [
                    Padding(
                      padding: const EdgeInsets.all(24),
                      child: Text(friendlyError(snap.error!)),
                    ),
                  ]);
                }
                final items = snap.data!;
                if (items.isEmpty) {
                  return ListView(children: const [
                    Padding(padding: EdgeInsets.all(32), child: Center(child: Text('Không có bài nào.')))
                  ]);
                }
                return ListView.separated(
                  itemCount: items.length,
                  separatorBuilder: (_, _) => const Divider(height: 1),
                  itemBuilder: (context, i) {
                    final it = items[i];
                    final status = '${it['status']}';
                    return ListTile(
                      // Text is always rendered as plain text, never as markup.
                      title: Text(
                        _title(it),
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                      ),
                      subtitle: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                        Text('${laneLabel('${it['lane']}')} · ${statusLabel(status)}'),
                        if (it['rating'] != null)
                          Row(children: [
                            for (var s = 1; s <= 5; s++)
                              Icon(s <= (it['rating'] as num) ? Icons.star : Icons.star_border,
                                  size: 16, color: Colors.amber),
                          ])
                        else if (status == 'PUBLISHED')
                          Text('Chưa đánh giá', style: Theme.of(context).textTheme.bodySmall),
                      ]),
                      leading: Icon(Icons.circle, size: 14, color: statusColor(context, status)),
                      trailing: creatingStatuses.contains(status)
                          ? IconButton(
                              tooltip: 'Hủy tạo bài',
                              icon: const Icon(Icons.cancel_outlined),
                              onPressed: () => _cancel(it),
                            )
                          : const Icon(Icons.chevron_right),
                      onTap: () => _open('${it['id']}'),
                    );
                  },
                );
              },
            ),
          ),
        ),
      ],
    );
  }
}

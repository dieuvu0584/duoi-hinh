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
    setState(() => _items = next);
    await next.catchError((_) => <Map<String, dynamic>>[]);
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
                        '${it['overlay_text'] ?? it['caption'] ?? '(chưa có nội dung)'}',
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                      ),
                      subtitle: Text('${laneLabel('${it['lane']}')} · ${statusLabel(status)}'),
                      leading: Icon(Icons.circle, size: 14, color: statusColor(context, status)),
                      trailing: const Icon(Icons.chevron_right),
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

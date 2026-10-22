import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../ui_helpers.dart';
import 'candidate_screen.dart';
import 'create_screen.dart';
import 'idioms_screen.dart';
import 'scan_schedule_screen.dart';
import 'sources_screen.dart';

String laneHint(String lane) => switch (lane) {
      'daily_life' => 'Hợp: Đời sống',
      'rebus' => 'Hợp: Đố hình',
      _ => 'Hợp: cả hai',
    };

class IdeasScreen extends StatefulWidget {
  const IdeasScreen({super.key});

  @override
  State<IdeasScreen> createState() => _IdeasScreenState();
}

class _IdeasScreenState extends State<IdeasScreen> {
  Map<String, dynamic>? _data;
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
      final d = await _api.ideaCandidates();
      if (mounted) {
        setState(() {
          _data = d;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = friendlyError(e));
    }
  }

  Future<void> _scan() async {
    setState(() => _busy = true);
    try {
      await _api.scanNow();
      if (mounted) {
        showMessage(context, 'Đã xếp lịch quét. Kéo xuống để cập nhật sau khoảng 1–2 phút.');
      }
      await _load();
    } catch (e) {
      if (mounted) showMessage(context, friendlyError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _open(Widget screen) async {
    await Navigator.of(context).push(MaterialPageRoute<void>(builder: (_) => screen));
    if (mounted) _load();
  }

  String _scanSummary(Map<String, dynamic>? last) {
    if (last == null) return 'Chưa quét lần nào.';
    final status = switch ('${last['status']}') {
      'queued' => 'đang chờ chạy',
      'running' => 'đang chạy',
      'done' => 'xong, ${last['items_new']} tin mới, ${last['candidates']} ý tưởng',
      _ => 'lỗi (${last['note'] ?? '?'})',
    };
    final when = DateTime.tryParse('${last['requested_at']}')?.toLocal();
    final time = when == null
        ? ''
        : '${when.day.toString().padLeft(2, '0')}/${when.month.toString().padLeft(2, '0')} '
            '${when.hour.toString().padLeft(2, '0')}:${when.minute.toString().padLeft(2, '0')}';
    return 'Lần quét gần nhất ($time): $status';
  }

  @override
  Widget build(BuildContext context) {
    final d = _data;
    final items = d == null ? <Map<String, dynamic>>[] : List<Map<String, dynamic>>.from(d['items'] as List);
    return Scaffold(
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(16, 8, 16, 96),
          children: [
            Row(children: [
              Expanded(
                child: FilledButton.icon(
                  onPressed: _busy ? null : _scan,
                  icon: const Icon(Icons.radar),
                  label: const Text('Quét tin ngay'),
                ),
              ),
              PopupMenuButton<String>(
                tooltip: 'Thiết lập quét',
                onSelected: (v) => _open(switch (v) {
                  'schedule' => const ScanScheduleScreen(),
                  'sources' => const SourcesScreen(),
                  _ => const IdiomsScreen(),
                }),
                itemBuilder: (_) => const [
                  PopupMenuItem(value: 'schedule', child: Text('Khung giờ quét')),
                  PopupMenuItem(value: 'sources', child: Text('Nguồn tin (RSS)')),
                  PopupMenuItem(value: 'idioms', child: Text('Kho thành ngữ')),
                ],
              ),
            ]),
            const SizedBox(height: 8),
            Text(_scanSummary(d?['last_scan'] as Map<String, dynamic>?),
                style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 12),
            if (_error != null) Text(_error!),
            if (d == null && _error == null) const Center(child: CircularProgressIndicator()),
            if (d != null && items.isEmpty)
              const Padding(
                padding: EdgeInsets.all(24),
                child: Text(
                    'Chưa có ý tưởng nào. Bấm "Quét tin ngay", hoặc bật quét tự động ở mục Khung giờ quét. '
                    'Bạn cũng có thể tự nhập ý tưởng bằng nút bên dưới.'),
              ),
            for (final it in items) _tile(it),
          ],
        ),
      ),
      floatingActionButton: FloatingActionButton.extended(
        onPressed: () => _open(Scaffold(
          appBar: AppBar(title: const Text('Tự nhập ý tưởng')),
          body: const CreateScreen(),
        )),
        icon: const Icon(Icons.edit_outlined),
        label: const Text('Tự nhập'),
      ),
    );
  }

  Widget _tile(Map<String, dynamic> it) {
    final sources = List<Map<String, dynamic>>.from(it['sources'] as List);
    return Card(
      margin: const EdgeInsets.only(bottom: 10),
      child: InkWell(
        onTap: () => _open(CandidateScreen(id: '${it['id']}')),
        child: Padding(
          padding: const EdgeInsets.all(14),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            // Text is always shown as plain text (it comes from outside).
            Text('${it['situation']}', style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: 6),
            Text('${it['why']}', style: Theme.of(context).textTheme.bodySmall),
            const SizedBox(height: 8),
            Wrap(spacing: 8, runSpacing: 4, children: [
              Chip(label: Text('${it['score']} điểm'), visualDensity: VisualDensity.compact),
              Chip(label: Text(laneHint('${it['suggested_lane']}')), visualDensity: VisualDensity.compact),
              if (it['status'] == 'proposed')
                const Chip(label: Text('Đã có gợi ý'), visualDensity: VisualDensity.compact),
            ]),
            if (sources.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text('Từ: ${sources.first['source']} — ${sources.first['title']}',
                  maxLines: 2, overflow: TextOverflow.ellipsis, style: Theme.of(context).textTheme.bodySmall),
            ],
          ]),
        ),
      ),
    );
  }
}

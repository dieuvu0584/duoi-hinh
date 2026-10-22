import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../ui_helpers.dart';

class SourcesScreen extends StatefulWidget {
  const SourcesScreen({super.key});

  @override
  State<SourcesScreen> createState() => _SourcesScreenState();
}

class _SourcesScreenState extends State<SourcesScreen> {
  List<Map<String, dynamic>>? _items;
  String? _error;

  ApiClient get _api => context.read<ApiClient>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final items = await _api.feedSources();
      if (mounted) {
        setState(() {
          _items = items;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = friendlyError(e));
    }
  }

  Future<void> _run(Future<void> Function() action) async {
    try {
      await action();
      await _load();
    } catch (e) {
      if (mounted) showMessage(context, friendlyError(e));
    }
  }

  Future<void> _add() async {
    final name = TextEditingController();
    final url = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Thêm nguồn tin RSS'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(controller: name, decoration: const InputDecoration(labelText: 'Tên (ví dụ: Báo Lao Động)')),
          TextField(
              controller: url,
              keyboardType: TextInputType.url,
              decoration: const InputDecoration(labelText: 'Địa chỉ RSS (https://…)')),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Hủy')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Thêm')),
        ],
      ),
    );
    if (ok == true && name.text.trim().isNotEmpty) {
      await _run(() => _api.addFeedSource(name.text.trim(), url.text.trim()));
    }
  }

  @override
  Widget build(BuildContext context) {
    final items = _items;
    return Scaffold(
      appBar: AppBar(title: const Text('Nguồn tin (RSS)')),
      body: items == null
          ? Center(child: _error != null ? Text(_error!) : const CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.only(bottom: 96),
                children: [
                  const Padding(
                    padding: EdgeInsets.all(16),
                    child: Text(
                        'Hệ thống đọc các nguồn đang bật mỗi lần quét. Chỉ nhận địa chỉ https công khai. '
                        'Tắt nguồn nào ít hữu ích để tiết kiệm.'),
                  ),
                  for (final s in items)
                    SwitchListTile(
                      title: Text('${s['name']}'),
                      subtitle: Text(s['last_error'] != null
                          ? 'Lỗi lần gần nhất: ${s['last_error']}'
                          : '${s['kind']}'),
                      value: s['enabled'] == true,
                      secondary: IconButton(
                        tooltip: 'Xóa',
                        icon: const Icon(Icons.delete_outline),
                        onPressed: () => _run(() => _api.deleteFeedSource('${s['id']}')),
                      ),
                      onChanged: (v) => _run(() => _api.setFeedSourceEnabled('${s['id']}', v)),
                    ),
                ],
              ),
            ),
      floatingActionButton:
          FloatingActionButton.extended(onPressed: _add, icon: const Icon(Icons.add), label: const Text('Thêm nguồn')),
    );
  }
}

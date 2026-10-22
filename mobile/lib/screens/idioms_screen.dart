import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../ui_helpers.dart';

class IdiomsScreen extends StatefulWidget {
  const IdiomsScreen({super.key});

  @override
  State<IdiomsScreen> createState() => _IdiomsScreenState();
}

class _IdiomsScreenState extends State<IdiomsScreen> {
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
      final items = await _api.idioms();
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
    final text = TextEditingController();
    final meaning = TextEditingController();
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Thêm thành ngữ / tục ngữ'),
        content: Column(mainAxisSize: MainAxisSize.min, children: [
          TextField(controller: text, decoration: const InputDecoration(labelText: 'Thành ngữ (đúng dấu)')),
          TextField(controller: meaning, decoration: const InputDecoration(labelText: 'Nghĩa ngắn (không bắt buộc)')),
        ]),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Hủy')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Thêm')),
        ],
      ),
    );
    if (ok == true && text.text.trim().length >= 2) {
      await _run(() => _api.addIdiom(text.text.trim(), meaning.text.trim()));
    }
  }

  @override
  Widget build(BuildContext context) {
    final items = _items;
    return Scaffold(
      appBar: AppBar(title: Text(items == null ? 'Kho thành ngữ' : 'Kho thành ngữ (${items.length})')),
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
                        'Câu đố được biến tấu từ các thành ngữ này cho hợp tin trong ngày. '
                        'Thêm những câu dễ vẽ thành hình để có nhiều lựa chọn hơn.'),
                  ),
                  for (final i in items)
                    ListTile(
                      title: Text('${i['text']}'),
                      subtitle: '${i['meaning']}'.isEmpty ? null : Text('${i['meaning']}'),
                      trailing: IconButton(
                        tooltip: 'Xóa',
                        icon: const Icon(Icons.delete_outline),
                        onPressed: () => _run(() => _api.deleteIdiom('${i['id']}')),
                      ),
                    ),
                ],
              ),
            ),
      floatingActionButton:
          FloatingActionButton.extended(onPressed: _add, icon: const Icon(Icons.add), label: const Text('Thêm')),
    );
  }
}

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../ui_helpers.dart';

class ScanScheduleScreen extends StatefulWidget {
  const ScanScheduleScreen({super.key});

  @override
  State<ScanScheduleScreen> createState() => _ScanScheduleScreenState();
}

class _ScanScheduleScreenState extends State<ScanScheduleScreen> {
  bool? _enabled;
  List<String> _times = [];
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
      final s = await _api.settings();
      if (mounted) {
        setState(() {
          _enabled = s['scan_enabled'] == true;
          _times = List<String>.from(s['scan_times'] as List);
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = friendlyError(e));
    }
  }

  Future<void> _save(Map<String, dynamic> changes) async {
    setState(() => _busy = true);
    try {
      final s = await _api.putSettings(changes);
      if (mounted) {
        setState(() {
          _enabled = s['scan_enabled'] == true;
          _times = List<String>.from(s['scan_times'] as List);
        });
      }
    } catch (e) {
      if (mounted) showMessage(context, friendlyError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _addTime() async {
    final picked =
        await showTimePicker(context: context, initialTime: const TimeOfDay(hour: 10, minute: 0));
    if (picked == null) return;
    final hhmm =
        '${picked.hour.toString().padLeft(2, '0')}:${picked.minute.toString().padLeft(2, '0')}';
    await _save({
      'scan_times': {..._times, hhmm}.toList()
    });
  }

  @override
  Widget build(BuildContext context) {
    final enabled = _enabled;
    return Scaffold(
      appBar: AppBar(title: const Text('Khung giờ quét')),
      body: enabled == null
          ? Center(child: _error != null ? Text(_error!) : const CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Tự động quét theo các khung giờ'),
                  subtitle: const Text('Mỗi lần quét tốn khoảng \$0,05–0,10 (Claude lọc tin). Tắt thì chỉ quét khi bạn bấm.'),
                  value: enabled,
                  onChanged: _busy ? null : (v) => _save({'scan_enabled': v}),
                ),
                const Divider(),
                Text('Giờ quét mỗi ngày (giờ Việt Nam)', style: Theme.of(context).textTheme.titleSmall),
                const SizedBox(height: 8),
                Wrap(spacing: 8, runSpacing: 8, children: [
                  for (final t in _times)
                    InputChip(
                      label: Text(t),
                      onDeleted: _busy ? null : () => _save({'scan_times': _times.where((x) => x != t).toList()}),
                    ),
                  ActionChip(
                    avatar: const Icon(Icons.add, size: 18),
                    label: const Text('Thêm giờ'),
                    onPressed: _busy || _times.length >= 8 ? null : _addTime,
                  ),
                ]),
                const SizedBox(height: 12),
                Text(
                    'Tối đa 8 giờ mỗi ngày. Nếu máy tắt đúng giờ quét, hệ thống vẫn chạy bù trong 3 tiếng sau đó; '
                    'quá 3 tiếng thì bỏ lượt đó. Việc quét cũng bị chặn khi hết ngân sách Claude hoặc khi bạn tạm dừng tạo nội dung.',
                    style: Theme.of(context).textTheme.bodySmall),
              ],
            ),
    );
  }
}

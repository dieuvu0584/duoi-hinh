import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../ui_helpers.dart';

class StatusScreen extends StatefulWidget {
  const StatusScreen({super.key});

  @override
  State<StatusScreen> createState() => _StatusScreenState();
}

class _StatusScreenState extends State<StatusScreen> {
  Map<String, dynamic>? _settings;
  Map<String, dynamic>? _costs;
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
      final c = await _api.costs();
      if (mounted) {
        setState(() {
          _settings = s;
          _costs = c;
          _error = null;
        });
      }
    } catch (e) {
      if (mounted) setState(() => _error = friendlyError(e));
    }
  }

  Future<void> _run(Future<void> Function() action, {String? done}) async {
    setState(() => _busy = true);
    try {
      await action();
      if (mounted && done != null) showMessage(context, done);
      await _load();
    } catch (e) {
      if (mounted) showMessage(context, friendlyError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _stop() => _run(() async => _api.stopPublishing(), done: 'Đã dừng đăng bài');

  Future<void> _resume() async {
    final password = await askPassword(context, reason: 'Bật lại đăng bài');
    if (password == null || password.isEmpty) return;
    await _run(() async => _api.resumePublishing(password), done: 'Đã bật lại đăng bài');
  }

  Future<void> _toggle(String field, bool value, {required String reason}) => _run(() async {
        await withReauth(context, (pw) async => _api.putSettings({field: value}, password: pw),
            reason: reason);
      });

  @override
  Widget build(BuildContext context) {
    final s = _settings;
    if (s == null) {
      return Center(child: _error != null ? Text(_error!) : const CircularProgressIndicator());
    }
    final killed = s['publishing_kill_switch'] == true;
    final scheme = Theme.of(context).colorScheme;
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Card(
            color: killed ? scheme.errorContainer : scheme.primaryContainer,
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text(killed ? 'ĐĂNG BÀI: ĐANG DỪNG' : 'Đăng bài: đang cho phép',
                    style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 4),
                Text(killed
                    ? 'Không bài nào được đăng cho tới khi bạn bật lại (cần mật khẩu).'
                    : 'Công tắc dừng khẩn cấp chặn mọi lệnh đăng, kể cả đã lên lịch. Không thu hồi bài đã đăng.'),
                const SizedBox(height: 12),
                killed
                    ? FilledButton(onPressed: _busy ? null : _resume, child: const Text('Bật lại đăng bài'))
                    : FilledButton.icon(
                        style: FilledButton.styleFrom(backgroundColor: scheme.error),
                        onPressed: _busy ? null : _stop,
                        icon: const Icon(Icons.stop_circle_outlined),
                        label: const Text('DỪNG ĐĂNG NGAY'),
                      ),
              ]),
            ),
          ),
          SwitchListTile(
            title: const Text('Tự động đăng theo lịch'),
            subtitle: const Text('Tắt = hệ thống không tự chuẩn bị hay đăng gì.'),
            value: s['automation_enabled'] == true,
            onChanged: _busy
                ? null
                : (v) => _toggle('automation_enabled', v, reason: 'Bật/tắt tự động đăng'),
          ),
          SwitchListTile(
            title: const Text('Tạm dừng tạo nội dung'),
            subtitle: const Text('Dừng mọi lệnh gọi trả phí để tạo bài mới.'),
            value: s['generation_paused'] == true,
            onChanged: _busy ? null : (v) => _toggle('generation_paused', v, reason: 'Xác nhận'),
          ),
          const Divider(),
          Text('Chi phí (USD)', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          if (_costs != null) ..._costRows(_costs!),
          if (_error != null)
            Padding(padding: const EdgeInsets.only(top: 12), child: Text(_error!)),
        ],
      ),
    );
  }

  List<Widget> _costRows(Map<String, dynamic> c) {
    final g = Map<String, dynamic>.from(c['global'] as Map);
    final providers = Map<String, dynamic>.from(c['providers'] as Map);
    return [
      _costRow('Tổng hôm nay', g['spent_today'], g['daily_limit']),
      _costRow('Tổng tháng', g['spent_month'], g['monthly_limit']),
      for (final e in providers.entries)
        _costRow('${e.key} (tháng)', (e.value as Map)['spent_month_usd'], (e.value as Map)['monthly_limit_usd']),
      const SizedBox(height: 4),
      Text('Hạn mức 0 nghĩa là chưa cho phép tiêu tiền. Đặt hạn mức ở tab Cài đặt.',
          style: Theme.of(context).textTheme.bodySmall),
    ];
  }

  Widget _costRow(String label, Object? spent, Object? limit) {
    final s = double.tryParse('$spent') ?? 0;
    final l = double.tryParse('$limit') ?? 0;
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
          Text(label),
          Text('\$${s.toStringAsFixed(2)} / \$${l.toStringAsFixed(2)}'),
        ]),
        LinearProgressIndicator(value: l <= 0 ? 0 : (s / l).clamp(0, 1).toDouble()),
      ]),
    );
  }
}

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../ui_helpers.dart';

const _budgetProviders = {
  'anthropic': 'Claude (viết nội dung, kiểm tra)',
  'fal': 'fal.ai (tạo ảnh)',
  'elevenlabs': 'ElevenLabs (giọng đọc)',
};

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  final _page = TextEditingController();
  final _youtube = TextEditingController();
  final _voice = TextEditingController();
  final _numbers = <String, TextEditingController>{
    'max_publishes_per_day': TextEditingController(),
    'auto_create_per_day': TextEditingController(),
    'auto_create_per_scan': TextEditingController(),
    'max_image_regenerations': TextEditingController(),
    'max_lateness_minutes': TextEditingController(),
    'prepare_lead_minutes': TextEditingController(),
  };
  final _globalDaily = TextEditingController();
  final _globalMonthly = TextEditingController();
  final _monthly = {for (final p in _budgetProviders.keys) p: TextEditingController()};
  final _daily = {for (final p in _budgetProviders.keys) p: TextEditingController()};

  Map<String, dynamic>? _settings;
  Map<String, dynamic> _costs = {};
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
      if (!mounted) return;
      setState(() {
        _settings = s;
        _error = null;
        // Show what is really used: the owner's own value, else the default from the server's .env.
        _page.text = _effective(s, 'facebook_page_id');
        _youtube.text = _effective(s, 'youtube_channel_id');
        _voice.text = _effective(s, 'elevenlabs_voice_id');
        for (final e in _numbers.entries) {
          e.value.text = '${s[e.key]}';
        }
        _globalDaily.text = _trim('${s['daily_budget_limit']}');
        _globalMonthly.text = _trim('${s['monthly_budget_limit']}');
        _costs = c;
        final providers = Map<String, dynamic>.from(c['providers'] as Map);
        for (final p in _budgetProviders.keys) {
          final row = providers[p] as Map?;
          _monthly[p]!.text = row == null ? '0' : _trim('${row['monthly_limit_usd']}');
          _daily[p]!.text = row?['daily_limit_usd'] == null ? '' : _trim('${row!['daily_limit_usd']}');
        }
      });
    } catch (e) {
      if (mounted) setState(() => _error = friendlyError(e));
    }
  }

  static String _effective(Map<String, dynamic> s, String key) =>
      '${(s['effective'] as Map?)?[key] ?? s[key] ?? ''}';

  /// 'Mặc định từ .env' while the owner has not set their own value.
  String _source(String key) {
    final s = _settings!;
    if (s[key] != null) return 'Đã đặt riêng. Xóa trống để dùng lại mặc định.';
    final hasDefault = (s['env_defaults'] as Map?)?[key] != null;
    return hasDefault ? 'Đang dùng giá trị mặc định từ .env.' : 'Chưa có giá trị.';
  }

  static String _trim(String v) {
    if (!v.contains('.')) return v;
    var out = v.replaceFirst(RegExp(r'0+$'), '');
    if (out.endsWith('.')) out = out.substring(0, out.length - 1);
    return out.isEmpty ? '0' : out;
  }

  Future<void> _save(Future<void> Function() action, String done) async {
    setState(() => _busy = true);
    try {
      await action();
      if (mounted) showMessage(context, done);
      await _load();
    } catch (e) {
      if (mounted) showMessage(context, friendlyError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Map<String, dynamic> _changedTargets() {
    final s = _settings!;
    final changes = <String, dynamic>{};
    void text(String key, TextEditingController c) {
      // Only a real edit is sent: showing the .env default must not copy it into the database.
      if (c.text.trim() != _effective(s, key)) changes[key] = c.text.trim();
    }

    text('facebook_page_id', _page);
    text('youtube_channel_id', _youtube);
    text('elevenlabs_voice_id', _voice);
    for (final e in _numbers.entries) {
      final v = int.tryParse(e.value.text.trim());
      if (v != null && v != s[e.key]) changes[e.key] = v;
    }
    return changes;
  }

  Future<void> _togglePublishOnApprove(bool value) async {
    await _save(() async {
      await withReauth(
          context, (pw) async => _api.putSettings({'publish_on_approve': value}, password: pw),
          reason: 'Đổi cách đăng sau khi duyệt cần mật khẩu');
    }, value ? 'Duyệt xong sẽ đăng ngay' : 'Duyệt xong sẽ chờ bạn chọn giờ');
  }

  Future<void> _toggleYoutube(bool value) async {
    await _save(() async {
      await withReauth(
          context, (pw) async => _api.putSettings({'youtube_enabled': value}, password: pw),
          reason: 'Bật/tắt đăng YouTube cần mật khẩu');
    }, value ? 'Đã bật đăng YouTube' : 'Đã tắt đăng YouTube');
  }

  Future<void> _saveGeneral() async {
    final changes = _changedTargets();
    if (changes.isEmpty) {
      showMessage(context, 'Không có thay đổi.');
      return;
    }
    await _save(() async {
      await withReauth(context, (pw) async => _api.putSettings(changes, password: pw),
          reason: 'Đổi Page/kênh cần mật khẩu');
    }, 'Đã lưu');
  }

  Future<void> _saveBudgets() async {
    final s = _settings!;
    await _save(() async {
      final global = <String, dynamic>{};
      if (_globalDaily.text.trim() != _trim('${s['daily_budget_limit']}')) {
        global['daily_budget_limit'] = _globalDaily.text.trim();
      }
      if (_globalMonthly.text.trim() != _trim('${s['monthly_budget_limit']}')) {
        global['monthly_budget_limit'] = _globalMonthly.text.trim();
      }
      if (global.isNotEmpty) {
        await withReauth(context, (pw) async => _api.putSettings(global, password: pw),
            reason: 'Đổi ngân sách cần mật khẩu');
      }
      for (final p in _budgetProviders.keys) {
        if (!mounted) return;
        final daily = _daily[p]!.text.trim();
        await withReauth(
            context,
            (pw) => _api.putProviderBudget(
                provider: p,
                monthly: _monthly[p]!.text.trim().isEmpty ? '0' : _monthly[p]!.text.trim(),
                daily: daily.isEmpty ? null : daily,
                password: pw),
            reason: 'Tăng ngân sách cần mật khẩu');
      }
    }, 'Đã lưu ngân sách');
  }

  @override
  Widget build(BuildContext context) {
    if (_settings == null) {
      return Center(child: _error != null ? Text(_error!) : const CircularProgressIndicator());
    }
    return RefreshIndicator(
      onRefresh: _load,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          Text('Nơi đăng và giọng đọc', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Duyệt xong tự đăng ngay'),
            subtitle: const Text(
                'Bấm Duyệt là đăng luôn, không phải chọn giờ. Vẫn qua mọi bước kiểm tra, và công tắc dừng đăng vẫn chặn được. Cần mật khẩu.'),
            value: _settings!['publish_on_approve'] == true,
            onChanged: _busy ? null : _togglePublishOnApprove,
          ),
          SwitchListTile(
            contentPadding: EdgeInsets.zero,
            title: const Text('Đăng lên YouTube'),
            subtitle: const Text(
                'Khi bật, bài đã duyệt có thể lên lịch đăng cả kênh YouTube (video 16:9). Cần mật khẩu.'),
            value: _settings!['youtube_enabled'] == true,
            onChanged: _busy ? null : _toggleYoutube,
          ),
          _field(_page, 'Facebook Page ID', helper: '${_source('facebook_page_id')} Đổi cần mật khẩu.'),
          _field(_youtube, 'YouTube Channel ID', helper: '${_source('youtube_channel_id')} Dạng UC… (đăng YouTube chưa bật).'),
          _field(_voice, 'ElevenLabs Voice ID', helper: '${_source('elevenlabs_voice_id')} Chỉ áp dụng cho bài tạo mới.'),
          const SizedBox(height: 8),
          Text('Giới hạn vận hành', style: Theme.of(context).textTheme.titleMedium),
          _field(_numbers['max_publishes_per_day']!, 'Số bài đăng tối đa mỗi ngày', number: true),
          _field(_numbers['auto_create_per_day']!, 'Số bài tự tạo mỗi ngày (0 = tắt, bạn vẫn duyệt)', number: true),
          _field(_numbers['auto_create_per_scan']!, 'Tối đa ý tưởng được làm video sau mỗi lần quét (1-10)', number: true),
          _field(_numbers['max_image_regenerations']!, 'Số lần làm lại ảnh tối đa mỗi bài', number: true),
          _field(_numbers['max_lateness_minutes']!, 'Trễ tối đa (phút) trước khi bỏ lượt', number: true),
          _field(_numbers['prepare_lead_minutes']!, 'Chuẩn bị video trước giờ đăng (phút)', number: true),
          FilledButton(onPressed: _busy ? null : _saveGeneral, child: const Text('Lưu')),
          const Divider(height: 40),
          Text('Ngân sách (USD)', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 8),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Text(
                'Mỗi lần hệ thống gọi một dịch vụ trả phí, nó phải qua đủ các hạn mức bên dưới. '
                'Chỉ cần một hạn mức hết là lệnh bị chặn và bài chờ (không mất gì).\n\n'
                '• Tổng: giới hạn chung cho tất cả dịch vụ cộng lại.\n'
                '• Từng dịch vụ: giới hạn riêng. Các dịch vụ này tính tiền theo tháng (gói/credit), nên hạn mức chính là "mỗi tháng".\n'
                '• "Mỗi ngày" là phanh phụ để một ngày lỗi không tiêu hết cả tháng. Có thể bỏ trống ở từng dịch vụ.\n\n'
                '0 = không cho phép tiêu tiền. Tăng hạn mức cần mật khẩu.',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ),
          ),
          const SizedBox(height: 16),
          Text('Tổng cho tất cả dịch vụ', style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 8),
          _field(_globalDaily, 'Tối đa mỗi ngày (USD)',
              number: true, decimal: true, helper: _used('global', 'spent_today', 'hôm nay')),
          _field(_globalMonthly, 'Tối đa mỗi tháng (USD)',
              number: true, decimal: true, helper: _used('global', 'spent_month', 'tháng này')),
          for (final e in _budgetProviders.entries) ...[
            const SizedBox(height: 12),
            Text(e.value, style: Theme.of(context).textTheme.titleSmall),
            const SizedBox(height: 8),
            _field(_monthly[e.key]!, 'Tối đa mỗi tháng (USD)',
                number: true, decimal: true, helper: _used(e.key, 'spent_month_usd', 'tháng này')),
            _field(_daily[e.key]!, 'Tối đa mỗi ngày (USD), bỏ trống = không giới hạn riêng',
                number: true, decimal: true, helper: _used(e.key, 'spent_today_usd', 'hôm nay')),
          ],
          FilledButton(onPressed: _busy ? null : _saveBudgets, child: const Text('Lưu ngân sách')),
        ],
      ),
    );
  }

  /// 'Đã dùng hôm nay: $0.31' from the last /costs reading.
  String _used(String scope, String key, String when) {
    final Map<dynamic, dynamic>? row = scope == 'global'
        ? _costs['global'] as Map?
        : (_costs['providers'] as Map?)?[scope] as Map?;
    final v = double.tryParse('${row?[key]}') ?? 0;
    return 'Đã dùng $when: \$${v.toStringAsFixed(2)}';
  }

  Widget _field(TextEditingController c, String label,
      {String? helper, bool number = false, bool decimal = false}) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 12),
      child: TextField(
        controller: c,
        keyboardType: number
            ? TextInputType.numberWithOptions(decimal: decimal)
            : TextInputType.text,
        decoration: InputDecoration(labelText: label, helperText: helper, border: const OutlineInputBorder()),
      ),
    );
  }

  @override
  void dispose() {
    for (final c in [_page, _youtube, _voice, _globalDaily, _globalMonthly, ..._numbers.values, ..._monthly.values, ..._daily.values]) {
      c.dispose();
    }
    super.dispose();
  }
}

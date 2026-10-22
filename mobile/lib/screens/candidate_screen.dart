import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../ui_helpers.dart';

class CandidateScreen extends StatefulWidget {
  const CandidateScreen({super.key, required this.id});

  final String id;

  @override
  State<CandidateScreen> createState() => _CandidateScreenState();
}

class _CandidateScreenState extends State<CandidateScreen> {
  Map<String, dynamic>? _idea;
  String? _error;
  bool _busy = false;
  String? _lane; // chosen lane once options are shown
  List<Map<String, dynamic>> _options = [];
  int _selected = -1;
  final _brief = TextEditingController();
  final _answer = TextEditingController();

  ApiClient get _api => context.read<ApiClient>();

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final idea = await _api.ideaCandidate(widget.id);
      if (idea['status'] == 'created' || idea['status'] == 'dismissed' || idea['status'] == 'expired') {
        throw ApiException(409, 'idea_closed', 'closed');
      }
      if (!mounted) return;
      setState(() {
        _idea = idea;
        // Options already asked for earlier are shown again instead of paying twice.
        final proposals = List<Map<String, dynamic>>.from(idea['proposals'] as List? ?? []);
        if (proposals.isNotEmpty && idea['lane'] != null) {
          _lane = '${idea['lane']}';
          _options = proposals;
          _pick(0);
        }
      });
    } catch (e) {
      if (mounted) setState(() => _error = friendlyError(e));
    }
  }

  Future<void> _propose(String lane) async {
    setState(() {
      _busy = true;
      _lane = lane;
    });
    try {
      final res = await _api.proposeIdea(widget.id, lane);
      final options = List<Map<String, dynamic>>.from(res['proposals'] as List);
      if (!mounted) return;
      setState(() {
        _options = options;
        _pick(0);
      });
    } catch (e) {
      if (mounted) {
        setState(() => _lane = null);
        showMessage(context, friendlyError(e));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  void _pick(int i) {
    _selected = i;
    _brief.text = '${_options[i]['brief']}';
    _answer.text = '${_options[i]['answer'] ?? ''}';
  }

  Future<void> _create() async {
    final lane = _lane!;
    if (_brief.text.trim().isEmpty) {
      showMessage(context, 'Phần mô tả đang trống.');
      return;
    }
    if (lane == 'rebus' && _answer.text.trim().isEmpty) {
      showMessage(context, 'Câu đố cần có đáp án bạn đã kiểm tra.');
      return;
    }
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Tạo bài này?'),
        content: Text(lane == 'rebus'
            ? 'Hệ thống sẽ tạo ảnh, giọng đọc và video (tốn khoảng \$0,2–0,3). '
                'Đáp án "${_answer.text.trim()}" sẽ được coi là đã kiểm tra: dấu và thanh điệu phải đúng.'
            : 'Hệ thống sẽ tạo ảnh, giọng đọc và video (tốn khoảng \$0,2–0,3).'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Hủy')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Tạo bài')),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    setState(() => _busy = true);
    try {
      await _api.createFromIdea(
          id: widget.id,
          lane: lane,
          brief: _brief.text.trim(),
          answer: lane == 'rebus' ? _answer.text.trim() : null);
      if (!mounted) return;
      showMessage(context, 'Đã xếp hàng tạo bài. Xem ở mục Bài viết (Đang tạo).');
      Navigator.of(context).pop();
    } catch (e) {
      if (mounted) showMessage(context, friendlyError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _dismiss() async {
    setState(() => _busy = true);
    try {
      await _api.dismissIdea(widget.id);
      if (mounted) Navigator.of(context).pop();
    } catch (e) {
      if (mounted) showMessage(context, friendlyError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final idea = _idea;
    return Scaffold(
      appBar: AppBar(title: const Text('Chọn hướng làm bài')),
      body: idea == null
          ? Center(child: _error != null ? Text(_error!) : const CircularProgressIndicator())
          : ListView(
              padding: const EdgeInsets.all(16),
              children: [
                Text('${idea['situation']}', style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 6),
                Text('${idea['why']}'),
                const SizedBox(height: 12),
                ExpansionTile(
                  tilePadding: EdgeInsets.zero,
                  title: const Text('Tin gốc (chỉ để tham khảo)'),
                  children: [
                    for (final s in List<Map<String, dynamic>>.from(idea['sources'] as List))
                      ListTile(
                        dense: true,
                        contentPadding: EdgeInsets.zero,
                        title: Text('${s['title']}'),
                        subtitle: SelectableText('${s['source']} · ${s['url']}'),
                      ),
                  ],
                ),
                const Divider(),
                if (_lane == null) ...[
                  Text('Làm thành loại nào?', style: Theme.of(context).textTheme.titleSmall),
                  const SizedBox(height: 8),
                  Row(children: [
                    Expanded(
                      child: FilledButton(
                          onPressed: _busy ? null : () => _propose('daily_life'),
                          child: const Text('Đời sống')),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: FilledButton.tonal(
                          onPressed: _busy ? null : () => _propose('rebus'),
                          child: const Text('Đố hình')),
                    ),
                  ]),
                  const SizedBox(height: 8),
                  Text(
                      'Đố hình lấy thành ngữ từ kho của bạn rồi biến tấu cho hợp tin. '
                      'Claude sẽ gợi ý 3 phương án (tốn vài cent).',
                      style: Theme.of(context).textTheme.bodySmall),
                  TextButton(onPressed: _busy ? null : _dismiss, child: const Text('Bỏ ý tưởng này')),
                ] else if (_busy && _options.isEmpty) ...[
                  const Padding(
                      padding: EdgeInsets.all(24),
                      child: Center(child: CircularProgressIndicator())),
                  const Center(child: Text('Claude đang nghĩ các phương án…')),
                ] else ...[
                  Text('Chọn một phương án rồi sửa theo ý bạn',
                      style: Theme.of(context).textTheme.titleSmall),
                  const SizedBox(height: 8),
                  for (var i = 0; i < _options.length; i++) _optionCard(i),
                  const SizedBox(height: 12),
                  TextField(
                    controller: _brief,
                    maxLines: 8,
                    minLines: 4,
                    maxLength: 2000,
                    decoration: InputDecoration(
                      labelText: _lane == 'rebus' ? 'Mô tả các hình manh mối' : 'Mô tả tình huống / điểm hài',
                      border: const OutlineInputBorder(),
                    ),
                  ),
                  if (_lane == 'rebus') ...[
                    const SizedBox(height: 12),
                    TextField(
                      controller: _answer,
                      maxLength: 500,
                      decoration: const InputDecoration(
                        labelText: 'Đáp án (bạn kiểm tra dấu và thanh điệu)',
                        helperText: 'Không đăng công khai. Chỉ bạn thấy khi duyệt.',
                        border: OutlineInputBorder(),
                      ),
                    ),
                  ],
                  const SizedBox(height: 12),
                  FilledButton.icon(
                    onPressed: _busy ? null : _create,
                    icon: const Icon(Icons.auto_awesome),
                    label: const Text('Tạo bài'),
                  ),
                  Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
                    TextButton(
                        onPressed: _busy
                            ? null
                            : () => setState(() {
                                  _lane = null;
                                  _options = [];
                                }),
                        child: const Text('Đổi loại')),
                    TextButton(
                        onPressed: _busy ? null : () => _propose(_lane!),
                        child: const Text('Gợi ý lại')),
                  ]),
                ],
              ],
            ),
    );
  }

  Widget _optionCard(int i) {
    final o = _options[i];
    final selected = i == _selected;
    final scheme = Theme.of(context).colorScheme;
    return Card(
      color: selected ? scheme.primaryContainer : null,
      margin: const EdgeInsets.only(bottom: 8),
      child: InkWell(
        onTap: () => setState(() => _pick(i)),
        child: Padding(
          padding: const EdgeInsets.all(12),
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('${o['title']}', style: Theme.of(context).textTheme.titleSmall),
            if ('${o['answer']}'.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text('Đáp án: ${o['answer']}'),
              if ('${o['idiom']}'.isNotEmpty)
                Text('Từ thành ngữ: ${o['idiom']}', style: Theme.of(context).textTheme.bodySmall),
              if ('${o['twist']}'.isNotEmpty)
                Text('Biến tấu: ${o['twist']}', style: Theme.of(context).textTheme.bodySmall),
            ],
            const SizedBox(height: 4),
            Text('${o['explanation']}', style: Theme.of(context).textTheme.bodySmall),
          ]),
        ),
      ),
    );
  }

  @override
  void dispose() {
    _brief.dispose();
    _answer.dispose();
    super.dispose();
  }
}

import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../image_chooser.dart';
import '../ui_helpers.dart';
import 'prompts_sheet.dart';

class CreateScreen extends StatefulWidget {
  const CreateScreen({
    super.key,
    this.initialLane,
    this.initialBrief,
    this.initialAnswer,
    this.initialInstructions,
    this.previousIssues = const [],
    this.replaceId,
    this.replaceStatus,
  });

  /// Used to correct a held idea and create it again: the old text is already filled in.
  final String? initialLane;
  final String? initialBrief;
  final String? initialAnswer;
  final String? initialInstructions;

  /// What the automatic check disliked last time (shown so the owner can answer it).
  final List<String> previousIssues;

  /// The item being replaced: refused automatically once the new one is queued (if still open).
  final String? replaceId;
  final String? replaceStatus;

  @override
  State<CreateScreen> createState() => _CreateScreenState();
}

class _CreateScreenState extends State<CreateScreen> {
  final String _lane = 'daily_life';
  final _brief = TextEditingController();
  final _answer = TextEditingController();
  final _instructions = TextEditingController();
  bool _busy = false;
  Uint8List? _image;
  List<String> _imageNotes = const [];

  @override
  void initState() {
    super.initState();
    // The puzzle lane is closed (decision D-32): everything is made as a meme picture.
    _brief.text = widget.initialBrief ?? '';
    _answer.text = widget.initialAnswer ?? '';
    _instructions.text = widget.initialInstructions ?? '';
  }

  Future<void> _submit() async {
    final brief = _brief.text.trim();
    if (brief.isEmpty) {
      showMessage(context, 'Hãy mô tả ý tưởng.');
      return;
    }
    setState(() => _busy = true);
    try {
      final api = context.read<ApiClient>();
      await api.createIdea(
            lane: _lane,
            brief: brief,
            answer: _lane == 'rebus' ? _answer.text : null,
            instructions: _instructions.text,
          );
      // Recreating replaces the old version: an item still waiting for the owner is refused so
      // that two versions of the same idea do not sit in the queue.
      if (widget.replaceId != null &&
          const {'PENDING_APPROVAL', 'HELD_FOR_HUMAN'}.contains(widget.replaceStatus)) {
        try {
          await api.reject(widget.replaceId!, note: 'Đã tạo lại phiên bản mới');
        } catch (_) {
          // The new version is queued either way; the old one can be refused by hand.
        }
      }
      _brief.clear();
      _answer.clear();
      _instructions.clear();
      if (widget.initialBrief != null && mounted) {
        showMessage(context, 'Đã xếp hàng tạo lại. Xem ở mục Bài viết (Đang tạo).');
        Navigator.of(context).pop();
        return;
      }
      if (mounted) {
        showMessage(context, 'Đã xếp hàng. Hệ thống sẽ tạo và báo ở mục Bài viết khi chờ bạn duyệt.');
      }
    } catch (e) {
      if (mounted) showMessage(context, friendlyError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return ListView(
      padding: const EdgeInsets.all(16),
      children: [
        Row(children: [
          OutlinedButton.icon(
            onPressed: _busy ? null : _pickImage,
            icon: const Icon(Icons.image_outlined),
            label: Text(_image == null ? 'Đính kèm ảnh' : 'Đổi ảnh'),
          ),
          if (_image != null) ...[
            const SizedBox(width: 12),
            ClipRRect(
                borderRadius: BorderRadius.circular(8),
                child: Image.memory(_image!, width: 56, height: 56, fit: BoxFit.cover)),
            IconButton(
              tooltip: 'Bỏ ảnh',
              onPressed: _busy ? null : () => setState(() => _image = null),
              icon: const Icon(Icons.close),
            ),
          ],
        ]),
        if (_image != null) ...[
          const SizedBox(height: 8),
          Text('Viết vào ô bên dưới điều bạn thấy hay ở ảnh, rồi bấm "Rút ý tưởng từ ảnh".',
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 8),
          FilledButton.tonalIcon(
            onPressed: _busy ? null : _draftFromImage,
            icon: const Icon(Icons.auto_awesome),
            label: const Text('Rút ý tưởng từ ảnh (tốn vài cent)'),
          ),
        ],
        for (final w in _imageNotes)
          Padding(
            padding: const EdgeInsets.only(top: 6),
            child: Text('⚠ $w', style: TextStyle(color: Theme.of(context).colorScheme.error)),
          ),
        const SizedBox(height: 12),
        TextField(
          controller: _brief,
          maxLines: 5,
          maxLength: 2000,
          decoration: InputDecoration(
            labelText: _lane == 'rebus' ? 'Gợi ý hình ảnh của câu đố' : 'Ý tưởng / tình huống',
            helperText: 'Mô tả ngắn gọn; hệ thống tự viết lời đọc, chú thích và ảnh.',
            border: const OutlineInputBorder(),
          ),
        ),
        const SizedBox(height: 12),
        if (widget.previousIssues.isNotEmpty)
          Card(
            color: Theme.of(context).colorScheme.tertiaryContainer,
            child: Padding(
              padding: const EdgeInsets.all(12),
              child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                Text('Lần trước kiểm tra tự động chê:',
                    style: Theme.of(context).textTheme.titleSmall),
                for (final i in widget.previousIssues.take(5))
                  Text('• $i', style: Theme.of(context).textTheme.bodySmall),
              ]),
            ),
          ),
        if (widget.previousIssues.isNotEmpty) const SizedBox(height: 12),
        TextField(
          controller: _instructions,
          maxLines: 4,
          minLines: 2,
          maxLength: 1500,
          decoration: const InputDecoration(
            labelText: 'Yêu cầu chỉnh sửa cụ thể cho AI (không bắt buộc)',
            helperText:
                'Ví dụ: vẽ con mèo to hơn, bỏ đám đông, nền xanh, đừng có người thứ hai. AI làm theo khi vẫn vẽ được trong một khung hình.',
            helperMaxLines: 3,
            border: OutlineInputBorder(),
          ),
        ),
        if (_lane == 'rebus') ...[
          const SizedBox(height: 12),
          TextField(
            controller: _answer,
            maxLength: 500,
            decoration: const InputDecoration(
              labelText: 'Đáp án (bạn đã kiểm tra, không đăng công khai)',
              border: OutlineInputBorder(),
            ),
          ),
        ],
        const SizedBox(height: 12),
        FilledButton.icon(
          onPressed: _busy ? null : _submit,
          icon: const Icon(Icons.auto_awesome),
          label: const Text('Tạo bài'),
        ),
        OutlinedButton.icon(
          onPressed: _busy ? null : _showPrompts,
          icon: const Icon(Icons.copy_all_outlined),
          label: const Text('Lấy prompt cho Gemini, Flow, HeyGen'),
        ),
        const SizedBox(height: 8),
        Text('Mảng tin tức đang tắt theo quyết định D-16.',
            style: Theme.of(context).textTheme.bodySmall),
      ],
    );
  }

  Future<void> _pickImage() async {
    try {
      final bytes = await context.read<ImageChooser>().pick();
      if (bytes != null && mounted) {
        setState(() {
          _image = bytes;
          _imageNotes = const [];
        });
      }
    } catch (e) {
      if (mounted) showMessage(context, friendlyError(e));
    }
  }

  /// Claude reads the picture together with what the owner wrote and drafts the idea; the draft
  /// replaces the text box so the owner can still edit it before creating anything.
  Future<void> _draftFromImage() async {
    final image = _image;
    if (image == null) return;
    setState(() => _busy = true);
    try {
      final out = await context.read<ApiClient>().ideaFromImage(image, note: _brief.text);
      if (!mounted) return;
      setState(() {
        _brief.text = '${out['brief']}';
        _imageNotes = List<Object?>.from(out['warnings'] as List? ?? []).map((e) => '$e').toList();
      });
      showMessage(context, 'Đã viết nháp ý tưởng từ ảnh, bạn xem và sửa trước khi tạo bài.');
    } catch (e) {
      if (mounted) showMessage(context, friendlyError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _showPrompts() async {
    final brief = _brief.text.trim();
    if (brief.isEmpty) {
      showMessage(context, 'Hãy mô tả ý tưởng trước.');
      return;
    }
    try {
      final prompts = await context.read<ApiClient>().promptsForOtherTools(
          lane: _lane, brief: brief, instructions: _instructions.text);
      if (mounted) await showPromptsSheet(context, prompts);
    } catch (e) {
      if (mounted) showMessage(context, friendlyError(e));
    }
  }

  @override
  void dispose() {
    _brief.dispose();
    _answer.dispose();
    _instructions.dispose();
    super.dispose();
  }
}

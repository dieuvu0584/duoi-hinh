import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../ui_helpers.dart';

/// Shows ready-made prompts for the owner's other AI accounts (Gemini, Flow/Veo, HeyGen) with a
/// copy button each.
Future<void> showPromptsSheet(BuildContext context, List<Map<String, dynamic>> prompts) {
  return showModalBottomSheet<void>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    builder: (ctx) => DraggableScrollableSheet(
      expand: false,
      initialChildSize: 0.85,
      maxChildSize: 0.95,
      builder: (_, controller) => ListView(
        controller: controller,
        padding: const EdgeInsets.fromLTRB(16, 0, 16, 24),
        children: [
          Text('Prompt cho công cụ khác', style: Theme.of(ctx).textTheme.titleLarge),
          const SizedBox(height: 4),
          Text('Sao chép rồi dán vào Gemini, Google Flow hoặc HeyGen của bạn.',
              style: Theme.of(ctx).textTheme.bodySmall),
          for (final p in prompts)
            Card(
              child: Padding(
                padding: const EdgeInsets.all(12),
                child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
                  Text('${p['title']}', style: Theme.of(ctx).textTheme.titleSmall),
                  const SizedBox(height: 6),
                  SelectableText('${p['text']}'),
                  Align(
                    alignment: Alignment.centerRight,
                    child: TextButton.icon(
                      onPressed: () async {
                        await Clipboard.setData(ClipboardData(text: '${p['text']}'));
                        if (ctx.mounted) showMessage(ctx, 'Đã sao chép prompt.');
                      },
                      icon: const Icon(Icons.copy, size: 18),
                      label: const Text('Sao chép'),
                    ),
                  ),
                ]),
              ),
            ),
        ],
      ),
    ),
  );
}

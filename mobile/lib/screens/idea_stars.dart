import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../ui_helpers.dart';

/// The owner's stars for an idea (1-5). Tap a star to rate; Claude later learns from the stars.
class IdeaStars extends StatefulWidget {
  const IdeaStars(
      {super.key, required this.id, this.rating, this.dense = false, this.onDismissed});

  final String id;
  final int? rating;
  final bool dense;

  /// Called when the rating was under 3 stars: the server then hides the idea from the default list.
  final VoidCallback? onDismissed;

  @override
  State<IdeaStars> createState() => _IdeaStarsState();
}

class _IdeaStarsState extends State<IdeaStars> {
  int? _rating;
  bool _busy = false;

  @override
  void initState() {
    super.initState();
    _rating = widget.rating;
  }

  @override
  void didUpdateWidget(IdeaStars old) {
    super.didUpdateWidget(old);
    // The list is reloaded after the owner rates elsewhere (e.g. on the idea screen): show the
    // saved value instead of the stale one this widget kept from its first build.
    if (!_busy && widget.rating != old.rating) _rating = widget.rating;
  }

  Future<void> _rate(int stars) async {
    final before = _rating;
    setState(() {
      _rating = stars;
      _busy = true;
    });
    try {
      await context.read<ApiClient>().rateIdea(widget.id, stars);
      if (stars < 3 && mounted) {
        showMessage(context,
            'Ý tưởng dưới 3 sao được ẩn khỏi danh sách (xem lại ở bộ lọc "Đã ẩn") và không bị quét lại.');
        widget.onDismissed?.call();
      }
    } catch (e) {
      if (mounted) {
        setState(() => _rating = before);
        showMessage(context, friendlyError(e));
      }
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Row(mainAxisSize: MainAxisSize.min, children: [
      for (var i = 1; i <= 5; i++)
        IconButton(
          tooltip: '$i sao cho ý tưởng này',
          visualDensity: VisualDensity.compact,
          padding: EdgeInsets.zero,
          constraints: BoxConstraints(minWidth: widget.dense ? 32 : 40, minHeight: widget.dense ? 32 : 40),
          onPressed: _busy ? null : () => _rate(i),
          icon: Icon(i <= (_rating ?? 0) ? Icons.star : Icons.star_border,
              size: widget.dense ? 22 : 26, color: i <= (_rating ?? 0) ? Colors.amber : null),
        ),
    ]);
  }
}

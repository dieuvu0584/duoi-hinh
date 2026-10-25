import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nhd_admin/api/api_client.dart';
import 'package:nhd_admin/api/token_store.dart';
import 'package:nhd_admin/screens/candidate_screen.dart';
import 'package:nhd_admin/screens/ideas_screen.dart';
import 'package:nhd_admin/screens/scan_schedule_screen.dart';
import 'package:provider/provider.dart';

import 'fake_backend.dart';

Future<ApiClient> loggedIn(FakeBackend backend) async {
  final store = MemoryTokenStore()
    ..value = StoredAuth(
        baseUrl: 'http://h',
        accessToken: 'tok',
        accessExpiresAt: DateTime.now().toUtc().add(const Duration(minutes: 10)),
        refreshToken: 'refreshrefreshrefreshrefresh');
  final api = ApiClient(store: store, dio: Dio()..httpClientAdapter = backend);
  await api.restore();
  return api;
}

Widget host(ApiClient api, Widget child) =>
    Provider<ApiClient>.value(value: api, child: MaterialApp(home: child));

Map<String, Object?> idea({String status = 'new', List<Object> proposals = const [], String? lane}) => {
      'id': 'c1',
      'status': status,
      'situation': 'Hai chú dê dọn cỏ ở nhà ga khiến ai cũng dừng lại xem.',
      'why': 'Động vật đáng yêu, dễ vẽ.',
      'score': 82,
      'suggested_lane': 'either',
      'sources': [
        {'title': 'Nhà ga dùng dê dọn cỏ', 'source': 'VnExpress', 'url': 'https://vnexpress.net/x'}
      ],
      'lane': lane,
      'proposals': proposals,
    };

Map<String, Object?> rebusOption() => {
      'title': 'Ba nắng bảy mưa',
      'brief': 'Hình 1: ba ngón tay. Hình 2: mặt trời.',
      'answer': 'Ba nắng bảy mưa',
      'idiom': 'Ba chìm bảy nổi',
      'twist': 'Thay chìm nổi bằng nắng mưa.',
      'explanation': 'Hợp tin thời tiết.',
      'syllables': ['Ba', 'nắng', 'bảy', 'mưa'],
      'difficulty': 'medium',
    };

void main() {
  testWidgets('the idea list shows candidates, sources and the last scan', (tester) async {
    final api = await loggedIn(FakeBackend({
      'GET /api/ideas/candidates': (_) => (200, {
            'items': [idea()],
            'last_scan': {
              'status': 'done',
              'items_new': 62,
              'candidates': 9,
              'requested_at': DateTime.now().toUtc().toIso8601String(),
              'note': null,
            },
          }),
    }));
    await tester.pumpWidget(host(api, const Scaffold(body: IdeasScreen())));
    await tester.pumpAndSettle();
    expect(find.textContaining('Hai chú dê dọn cỏ'), findsOneWidget);
    expect(find.textContaining('VnExpress — Nhà ga dùng dê'), findsOneWidget);
    expect(find.text('82 điểm'), findsOneWidget);
    expect(find.textContaining('62 tin mới, 9 ý tưởng'), findsOneWidget);
  });

  testWidgets('scan now queues a scan and tells the owner to refresh', (tester) async {
    var scans = 0;
    final api = await loggedIn(FakeBackend({
      'GET /api/ideas/candidates': (_) => (200, {'items': [], 'last_scan': null}),
      'POST /api/ideas/scan': (_) {
        scans++;
        return (202, {'id': 'x', 'status': 'queued'});
      },
    }));
    await tester.pumpWidget(host(api, const Scaffold(body: IdeasScreen())));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Quét tin ngay'));
    await tester.pumpAndSettle();
    expect(scans, 1);
    expect(find.textContaining('Đã xếp lịch quét'), findsOneWidget);
  });

  testWidgets('picking a direction shows meme options and creates with the edited text', (tester) async {
    Map<String, dynamic>? created;
    final option = {
      'title': 'Mèo ngủ gật',
      'brief': 'Con mèo ngủ gật trên bàn làm việc, mặt đầy vẻ tội lỗi.',
      'answer': '',
      'idiom': '',
      'twist': '',
      'explanation': 'Hợp tin cuối tuần.',
      'syllables': <String>[],
      'difficulty': 'easy',
    };
    final api = await loggedIn(FakeBackend({
      'GET /api/ideas/candidates/c1': (_) => (200, idea()),
      'POST /api/ideas/candidates/c1/propose': (c) {
        expect((c.body as Map)['lane'], 'daily_life');
        return (200, idea(status: 'proposed', lane: 'daily_life', proposals: [option]));
      },
      'POST /api/ideas/candidates/c1/create': (c) {
        created = Map<String, dynamic>.from(c.body as Map);
        return (201, {'id': 'item1', 'lane': 'daily_life', 'status': 'DRAFT'});
      },
    }));
    await tester.pumpWidget(host(api, const CandidateScreen(id: 'c1')));
    await tester.pumpAndSettle();
    expect(find.text('Đố hình'), findsNothing); // the puzzle lane is closed
    await tester.tap(find.text('Gợi ý hướng làm meme'));
    await tester.pumpAndSettle();
    expect(find.text('Mèo ngủ gật'), findsWidgets);
    final create = find.widgetWithText(FilledButton, 'Tạo bài');
    await tester.scrollUntilVisible(create, 200, scrollable: find.byType(Scrollable).first);
    await tester.tap(create);
    await tester.pumpAndSettle();
    expect(created, isNull); // nothing is sent before the owner confirms
    await tester.tap(find.widgetWithText(FilledButton, 'Tạo bài').last);
    await tester.pumpAndSettle();
    expect(created!['lane'], 'daily_life');
    expect(created!['brief'], contains('mèo'));
  });

  testWidgets('the owner can star an idea in the list', (tester) async {
    Map? sent;
    final api = await loggedIn(FakeBackend({
      'GET /api/ideas/candidates': (_) => (200, {'items': [idea()], 'last_scan': null}),
      'POST /api/ideas/candidates/c1/rating': (c) {
        sent = c.body as Map?;
        return (200, {});
      },
    }));
    await tester.pumpWidget(host(api, const Scaffold(body: IdeasScreen())));
    await tester.pumpAndSettle();
    await tester.tap(find.byTooltip('4 sao cho ý tưởng này'));
    await tester.pumpAndSettle();
    expect(sent!['rating'], 4);
    expect(find.byIcon(Icons.star), findsNWidgets(4));
  });

  testWidgets('a rebus cannot be created with an empty answer', (tester) async {
    var calls = 0;
    final api = await loggedIn(FakeBackend({
      'GET /api/ideas/candidates/c1': (_) =>
          (200, idea(status: 'proposed', lane: 'rebus', proposals: [rebusOption()])),
      'POST /api/ideas/candidates/c1/create': (_) {
        calls++;
        return (201, {});
      },
    }));
    await tester.pumpWidget(host(api, const CandidateScreen(id: 'c1')));
    await tester.pumpAndSettle();
    final answerField = find.widgetWithText(TextField, 'Ba nắng bảy mưa');
    await tester.scrollUntilVisible(answerField, 200, scrollable: find.byType(Scrollable).first);
    await tester.enterText(answerField, '   ');
    final create = find.widgetWithText(FilledButton, 'Tạo bài');
    await tester.scrollUntilVisible(create, 200, scrollable: find.byType(Scrollable).first);
    await tester.tap(create);
    await tester.pumpAndSettle();
    expect(calls, 0);
    expect(find.textContaining('cần có đáp án'), findsOneWidget);
  });

  testWidgets('options asked for earlier are shown again without asking Claude again', (tester) async {
    var proposes = 0;
    final api = await loggedIn(FakeBackend({
      'GET /api/ideas/candidates/c1': (_) =>
          (200, idea(status: 'proposed', lane: 'rebus', proposals: [rebusOption()])),
      'POST /api/ideas/candidates/c1/propose': (_) {
        proposes++;
        return (200, {});
      },
    }));
    await tester.pumpWidget(host(api, const CandidateScreen(id: 'c1')));
    await tester.pumpAndSettle();
    expect(find.text('Đáp án: Ba nắng bảy mưa'), findsOneWidget);
    expect(proposes, 0);
  });

  testWidgets('a closed idea says so instead of crashing', (tester) async {
    final api = await loggedIn(FakeBackend({
      'GET /api/ideas/candidates/c1': (_) => (200, idea(status: 'created')),
    }));
    await tester.pumpWidget(host(api, const CandidateScreen(id: 'c1')));
    await tester.pumpAndSettle();
    expect(find.textContaining('đã được xử lý hoặc đã hết hạn'), findsOneWidget);
  });

  testWidgets('scan times can be added and removed, scanning switched on', (tester) async {
    var times = ['10:00', '16:00'];
    var enabled = false;
    final backend = FakeBackend({
      'GET /api/system/settings': (_) => (200, {'scan_enabled': enabled, 'scan_times': times}),
      'PUT /api/system/settings': (c) {
        final changes = Map<String, dynamic>.from((c.body as Map)['changes'] as Map);
        if (changes.containsKey('scan_enabled')) enabled = changes['scan_enabled'] as bool;
        if (changes.containsKey('scan_times')) times = List<String>.from(changes['scan_times'] as List);
        return (200, {'scan_enabled': enabled, 'scan_times': times});
      },
    });
    final api = await loggedIn(backend);
    await tester.pumpWidget(host(api, const ScanScheduleScreen()));
    await tester.pumpAndSettle();
    expect(find.text('10:00'), findsOneWidget);
    expect(find.text('16:00'), findsOneWidget);

    await tester.tap(find.byType(Switch));
    await tester.pumpAndSettle();
    expect(enabled, isTrue);

    await tester.tap(find.byTooltip('Delete').last); // the chip for 16:00
    await tester.pumpAndSettle();
    expect(times, ['10:00']);
    expect(find.text('16:00'), findsNothing);
  });
}

import 'package:dio/dio.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nhd_admin/api/api_client.dart';
import 'package:nhd_admin/api/token_store.dart';
import 'package:nhd_admin/media_saver.dart';
import 'package:nhd_admin/screens/login_screen.dart';
import 'package:nhd_admin/screens/queue_screen.dart';
import 'package:nhd_admin/screens/review_screen.dart';
import 'package:nhd_admin/screens/settings_screen.dart';
import 'package:nhd_admin/screens/status_screen.dart';
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

Map<String, Object?> ticket({
  String status = 'PENDING_APPROVAL',
  bool canApprove = true,
  bool needsHuman = false,
  String? answer,
}) =>
    {
      'id': 'abc',
      'lane': 'rebus',
      'status': status,
      'revision': {
        'id': 'r1',
        'revision_no': 2,
        'content_hash': 'a' * 64,
        'answer_text': answer,
        'clue_mapping': [
          {'clue_id': 'c1', 'visual_clue': 'đầu voi', 'maps_to': 'đầu'}
        ],
        'caption': 'Đoán xem đây là gì?',
        'hashtags': ['#doanhinh'],
        'overlay_text': 'Câu đố hôm nay',
        'narration_text': 'Đây là gì nhỉ?',
        'voice_id': 'v',
        'is_ai_generated': true,
      },
      'explanation': {
        'visual_explanation': 'Hình voi và chuột',
        'analysis': {
          'intended_viewer_inference': 'đầu voi đuôi chuột',
          'ambiguity_risks': ['có thể hiểu thành con voi']
        },
        'last_qa_issues': null,
        'notes': null,
      },
      'safety': {
        'intake': null,
        'final': {
          'decision': 'allow',
          'risk_level': 'low',
          'categories': <String>[],
          'findings': [],
          'human_review_required': needsHuman,
          'human_review_complete': false,
        }
      },
      'videos': <Object>[],
      'audio_url': null,
      'can_approve': canApprove,
      'needs_human_review_completion': needsHuman,
    };

void main() {
  scheduleTests();
  holdTests();
  rejectedTests();
  qaFlagTests();
  publishOnApproveTests();
  queueTitleTests();
  cancelTests();
  testWidgets('login shows a friendly message for a wrong password', (tester) async {
    final backend = FakeBackend({
      'POST /api/auth/login': (_) =>
          (403, {'error': {'code': 'invalid_credentials', 'message': 'x'}}),
    });
    final api = ApiClient(store: MemoryTokenStore(), dio: Dio()..httpClientAdapter = backend);
    var done = false;
    await tester.pumpWidget(host(api, LoginScreen(onLoggedIn: () => done = true)));
    await tester.enterText(find.widgetWithText(TextField, 'Mật khẩu'), 'wrong');
    await tester.tap(find.text('Đăng nhập'));
    await tester.pumpAndSettle();
    expect(find.text('Sai tên đăng nhập hoặc mật khẩu.'), findsOneWidget);
    expect(done, isFalse);
  });

  testWidgets('the queue lists what needs the owner and renders text as plain text', (tester) async {
    final backend = FakeBackend({
      'GET /api/content': (_) => (200, {
            'items': [
              {
                'id': '1',
                'lane': 'daily_life',
                'status': 'PENDING_APPROVAL',
                'overlay_text': '<b>Mèo</b> lười',
                'caption': 'c',
              }
            ]
          }),
    });
    final api = await loggedIn(backend);
    await tester.pumpWidget(host(api, const Scaffold(body: QueueScreen())));
    await tester.pumpAndSettle();
    expect(find.text('<b>Mèo</b> lười'), findsOneWidget); // shown literally, never as markup
    expect(find.textContaining('Chờ bạn duyệt'), findsOneWidget);
  });

  testWidgets('an empty queue says so', (tester) async {
    final api = await loggedIn(FakeBackend({'GET /api/content': (_) => (200, {'items': []})}));
    await tester.pumpWidget(host(api, const Scaffold(body: QueueScreen())));
    await tester.pumpAndSettle();
    expect(find.text('Không có bài nào.'), findsOneWidget);
  });

  testWidgets('approve is disabled until the server says the item can be approved', (tester) async {
    var t = ticket(canApprove: false, needsHuman: true);
    final backend = FakeBackend({'GET /api/content/abc/review': (_) => (200, t)});
    final api = await loggedIn(backend);
    await tester.pumpWidget(host(api, const ReviewScreen(id: 'abc')));
    await tester.pumpAndSettle();
    final approve = find.widgetWithText(FilledButton, 'Duyệt');
    expect(tester.widget<FilledButton>(approve).onPressed, isNull);
    expect(find.text('Tôi đã xem xét kỹ'), findsOneWidget);
    await tester.scrollUntilVisible(find.textContaining('Bắt buộc bạn tự xem xét'), 200, scrollable: find.byType(Scrollable).first);
    expect(find.textContaining('Bắt buộc bạn tự xem xét'), findsOneWidget);

    t = ticket(canApprove: true);
    await tester.drag(find.byType(Scrollable).first, const Offset(0, 1500)); // back to the top
    await tester.pumpAndSettle();
    await tester.drag(find.byType(Scrollable).first, const Offset(0, 400)); // pull to refresh
    await tester.pumpAndSettle();
    expect(tester.widget<FilledButton>(find.widgetWithText(FilledButton, 'Duyệt')).onPressed, isNotNull);
  });

  testWidgets('the rebus answer and the explanation are shown to the owner', (tester) async {
    final api = await loggedIn(FakeBackend({
      'GET /api/content/abc/review': (_) => (200, ticket(answer: 'Đầu voi đuôi chuột')),
    }));
    await tester.pumpWidget(host(api, const ReviewScreen(id: 'abc')));
    await tester.pumpAndSettle();
    expect(find.text('Đầu voi đuôi chuột'), findsOneWidget);
    expect(find.textContaining('chỉ bạn thấy, không đăng'), findsOneWidget);
    expect(find.text('Hình voi và chuột'), findsOneWidget);
    expect(find.textContaining('có thể hiểu thành con voi'), findsOneWidget);
  });

  testWidgets('approving asks for confirmation and then calls the API once', (tester) async {
    var approvals = 0;
    final backend = FakeBackend({
      'GET /api/content/abc/review': (_) => (200, ticket()),
      'POST /api/content/abc/approve': (_) {
        approvals++;
        return (200, {});
      },
    });
    final api = await loggedIn(backend);
    await tester.pumpWidget(host(api, const ReviewScreen(id: 'abc')));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Duyệt'));
    await tester.pumpAndSettle();
    expect(approvals, 0); // nothing happens before the owner confirms
    await tester.tap(find.widgetWithText(FilledButton, 'Duyệt').last);
    await tester.pumpAndSettle();
    expect(approvals, 1);
  });

  testWidgets('an approved item offers scheduling, not approval', (tester) async {
    final api = await loggedIn(FakeBackend({
      'GET /api/content/abc/review': (_) => (200, ticket(status: 'APPROVED')),
    }));
    await tester.pumpWidget(host(api, const ReviewScreen(id: 'abc')));
    await tester.pumpAndSettle();
    expect(find.text('Lên lịch đăng'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Duyệt'), findsNothing);
  });

  testWidgets('the stop button works without a password and resume asks for one', (tester) async {
    var stopped = false;
    var resumedWith = '';
    final backend = FakeBackend({
      'GET /api/system/settings': (_) => (200, {
            'publishing_kill_switch': stopped,
            'automation_enabled': false,
            'generation_paused': false,
          }),
      'GET /api/costs': (_) => (200, {
            'global': {
              'daily_limit': '3',
              'monthly_limit': '3',
              'spent_today': '0.3',
              'spent_month': '0.3'
            },
            'providers': {
              'fal': {'monthly_limit_usd': '4', 'spent_month_usd': '0.04', 'daily_limit_usd': null}
            },
          }),
      'POST /api/system/publishing-stop': (_) {
        stopped = true;
        return (200, {});
      },
      'POST /api/system/publishing-resume': (c) {
        resumedWith = (c.body as Map)['password'] as String;
        stopped = false;
        return (200, {});
      },
    });
    final api = await loggedIn(backend);
    await tester.pumpWidget(host(api, const Scaffold(body: StatusScreen())));
    await tester.pumpAndSettle();
    expect(find.text('Đăng bài: đang cho phép'), findsOneWidget);
    await tester.tap(find.text('DỪNG ĐĂNG NGAY'));
    await tester.pumpAndSettle();
    expect(find.text('ĐĂNG BÀI: ĐANG DỪNG'), findsOneWidget);

    await tester.tap(find.text('Bật lại đăng bài'));
    await tester.pumpAndSettle();
    await tester.enterText(find.byType(TextField).last, 'my password');
    await tester.tap(find.text('Xác nhận'));
    await tester.pumpAndSettle();
    expect(resumedWith, 'my password');
    expect(find.text('Đăng bài: đang cho phép'), findsOneWidget);
  });

  group('settings', () {
    Map<String, Object?> settings() => {
          'facebook_page_id': null,
          'youtube_channel_id': null,
          'elevenlabs_voice_id': 'OwnVoice',
          'env_defaults': {
            'facebook_page_id': '1514689985052394',
            'youtube_channel_id': null,
            'elevenlabs_voice_id': 'EnvVoice'
          },
          'effective': {
            'facebook_page_id': '1514689985052394',
            'youtube_channel_id': null,
            'elevenlabs_voice_id': 'OwnVoice'
          },
          'daily_budget_limit': '3.0000',
          'monthly_budget_limit': '3.0000',
          'max_publishes_per_day': 3,
          'max_image_regenerations': 3,
          'max_lateness_minutes': 30,
          'prepare_lead_minutes': 20,
        };

    FakeBackend backend(List<Call> puts) => FakeBackend({
          'GET /api/system/settings': (_) => (200, settings()),
          'GET /api/costs': (_) => (200, {
                'global': {},
                'providers': <String, Object?>{},
              }),
          'PUT /api/system/settings': (c) {
            puts.add(c);
            return (200, settings());
          },
        });

    testWidgets('the .env default is shown as the starting value', (tester) async {
      final api = await loggedIn(backend([]));
      await tester.pumpWidget(host(api, const Scaffold(body: SettingsScreen())));
      await tester.pumpAndSettle();
      expect(find.widgetWithText(TextField, '1514689985052394'), findsOneWidget);
      expect(find.textContaining('Đang dùng giá trị mặc định từ .env'), findsOneWidget);
      expect(find.textContaining('Đã đặt riêng'), findsOneWidget); // the voice
    });

    testWidgets('saving without editing never copies the default into the database', (tester) async {
      final puts = <Call>[];
      final api = await loggedIn(backend(puts));
      await tester.pumpWidget(host(api, const Scaffold(body: SettingsScreen())));
      await tester.pumpAndSettle();
      await tester.scrollUntilVisible(find.text('Lưu'), 200, scrollable: find.byType(Scrollable).first);
      await tester.tap(find.text('Lưu'));
      await tester.pumpAndSettle();
      expect(puts, isEmpty);
      expect(find.text('Không có thay đổi.'), findsOneWidget);
    });
  });

  group('video download', () {
    Map<String, Object?> withVideos() => {
          ...ticket(),
          'videos': [
            {'target': 'reel_9x16', 'mode': 'fill', 'width': 1080, 'height': 1920, 'duration_s': '12.0',
             'url': '/api/content/abcdef12/media/reel_9x16'},
            {'target': 'youtube_16x9', 'mode': 'fit', 'width': 1920, 'height': 1080, 'duration_s': '12.0',
             'url': '/api/content/abcdef12/media/youtube_16x9'},
          ],
        };

    testWidgets('the owner can save the selected video to the phone before approving', (tester) async {
      final saver = _FakeSaver();
      final api = await loggedIn(FakeBackend({'GET /api/content/abcdef12/review': (_) => (200, withVideos())}));
      await tester.pumpWidget(MultiProvider(
        providers: [Provider<ApiClient>.value(value: api), Provider<MediaSaver>.value(value: saver)],
        child: const MaterialApp(home: ReviewScreen(id: 'abcdef12')),
      ));
      await tester.pumpAndSettle();
      final button = find.text('Tải video này về máy');
      await tester.scrollUntilVisible(button, 200, scrollable: find.byType(Scrollable).first);
      await tester.ensureVisible(button);
      await tester.pumpAndSettle();
      await tester.tap(button);
      await tester.pumpAndSettle();
      expect(saver.saved, ['/api/content/abcdef12/media/reel_9x16 -> nhd_abcdef12_reel_9x16.mp4']);
      expect(find.textContaining('Đã lưu vào thư viện ảnh'), findsOneWidget);
    });

    testWidgets('a failed download is reported, not swallowed', (tester) async {
      final saver = _FakeSaver(fail: true);
      final api = await loggedIn(FakeBackend({'GET /api/content/abcdef12/review': (_) => (200, withVideos())}));
      await tester.pumpWidget(MultiProvider(
        providers: [Provider<ApiClient>.value(value: api), Provider<MediaSaver>.value(value: saver)],
        child: const MaterialApp(home: ReviewScreen(id: 'abcdef12')),
      ));
      await tester.pumpAndSettle();
      final button = find.text('Tải video này về máy');
      await tester.scrollUntilVisible(button, 200, scrollable: find.byType(Scrollable).first);
      await tester.ensureVisible(button);
      await tester.pumpAndSettle();
      await tester.tap(button);
      await tester.pumpAndSettle();
      expect(find.textContaining('Không kết nối được máy chủ'), findsOneWidget);
      expect(find.text('Tải video này về máy'), findsOneWidget); // the button is usable again
    });
  });
}


void scheduleTests() {
  testWidgets('scheduling asks for the platforms; YouTube is locked while switched off', (tester) async {
    Map<String, dynamic>? sent;
    final api = await loggedIn(FakeBackend({
      'GET /api/content/abc/review': (_) => (200, ticket(status: 'APPROVED')),
      'GET /api/system/settings': (_) => (200, {'youtube_enabled': false}),
      'POST /api/content/abc/schedule': (c) {
        sent = Map<String, dynamic>.from(c.body as Map);
        return (201, {'records': []});
      },
    }));
    await tester.pumpWidget(host(api, const ReviewScreen(id: 'abc')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Lên lịch đăng'));
    await tester.pumpAndSettle();
    expect(find.text('Facebook Reels (9:16)'), findsOneWidget);
    final youtube = find.widgetWithText(CheckboxListTile, 'YouTube (16:9)');
    expect(tester.widget<CheckboxListTile>(youtube).onChanged, isNull); // switched off
    expect(find.textContaining('Đang tắt. Bật ở Cài đặt'), findsOneWidget);
    expect(sent, isNull); // nothing is sent before a time is chosen
  });

  testWidgets('with YouTube on, both platforms are preselected and sent', (tester) async {
    final api = await loggedIn(FakeBackend({
      'GET /api/content/abc/review': (_) => (200, ticket(status: 'APPROVED')),
      'GET /api/system/settings': (_) => (200, {'youtube_enabled': true}),
    }));
    await tester.pumpWidget(host(api, const ReviewScreen(id: 'abc')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Lên lịch đăng'));
    await tester.pumpAndSettle();
    final youtube = find.widgetWithText(CheckboxListTile, 'YouTube (16:9)');
    expect(tester.widget<CheckboxListTile>(youtube).value, isTrue);
    expect(tester.widget<CheckboxListTile>(youtube).onChanged, isNotNull);
    // at least one platform must stay chosen
    await tester.tap(find.widgetWithText(CheckboxListTile, 'Facebook Reels (9:16)'));
    await tester.tap(youtube);
    await tester.pumpAndSettle();
    final next = find.widgetWithText(FilledButton, 'Tiếp tục');
    expect(tester.widget<FilledButton>(next).onPressed, isNull);
  });
}


void holdTests() {
  Map<String, Object?> held({String stage = 'render', bool canResume = true}) => {
        ...ticket(status: 'HELD_FOR_HUMAN', canApprove: false),
        'hold': {
          'kind': 'held',
          'stage': stage,
          'reason': 'render_failed: ffmpeg could not run: TimeoutExpired',
          'can_resume': canResume,
        },
        'source': {'brief': 'Một con mèo lười dậy sớm', 'answer': 'Ôm quạt đợi khuya'},
      };

  testWidgets('a held item explains why and offers retry, edit and reject', (tester) async {
    var resumed = 0;
    final api = await loggedIn(FakeBackend({
      'GET /api/content/abc/review': (_) => (200, held()),
      'POST /api/content/abc/resume': (_) {
        resumed++;
        return (200, {});
      },
    }));
    await tester.pumpWidget(host(api, const ReviewScreen(id: 'abc')));
    await tester.pumpAndSettle();
    expect(find.text('Vì sao bài bị giữ lại'), findsOneWidget);
    expect(find.textContaining('Dựng video bị lỗi'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Thử lại'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Sửa và tạo lại'), findsOneWidget);
    expect(find.widgetWithText(OutlinedButton, 'Từ chối'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Duyệt'), findsNothing); // nothing to approve yet
    await tester.tap(find.widgetWithText(FilledButton, 'Thử lại'));
    await tester.pumpAndSettle();
    expect(resumed, 1);
  });

  testWidgets('a hold from a review finding cannot be retried, only corrected or rejected',
      (tester) async {
    final api = await loggedIn(FakeBackend({
      'GET /api/content/abc/review': (_) => (200, held(stage: 'analyze', canResume: false)),
    }));
    await tester.pumpWidget(host(api, const ReviewScreen(id: 'abc')));
    await tester.pumpAndSettle();
    expect(find.widgetWithText(FilledButton, 'Thử lại'), findsNothing);
    expect(find.widgetWithText(FilledButton, 'Sửa và tạo lại'), findsOneWidget);
  });

  testWidgets('edit and recreate opens the form with the old idea filled in', (tester) async {
    Map<String, dynamic>? created;
    final api = await loggedIn(FakeBackend({
      'GET /api/content/abc/review': (_) => (200, held(stage: 'generate')),
      'POST /api/content/ideas': (c) {
        created = Map<String, dynamic>.from(c.body as Map);
        return (201, {'id': 'n', 'lane': 'rebus', 'status': 'DRAFT'});
      },
    }));
    await tester.pumpWidget(host(api, const ReviewScreen(id: 'abc')));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Sửa và tạo lại'));
    await tester.pumpAndSettle();
    expect(find.text('Một con mèo lười dậy sớm'), findsOneWidget);
    expect(find.text('Ôm quạt đợi khuya'), findsOneWidget);
    await tester.enterText(find.widgetWithText(TextField, 'Một con mèo lười dậy sớm'),
        'Một con mèo lười dậy sớm, vẽ thật đơn giản');
    await tester.tap(find.widgetWithText(FilledButton, 'Tạo bài'));
    await tester.pumpAndSettle();
    expect(created!['lane'], 'rebus');
    expect(created!['brief'], contains('đơn giản'));
    expect(created!['answer'], 'Ôm quạt đợi khuya');
  });
}


void rejectedTests() {
  testWidgets('an automatically rejected item says why and offers to correct and recreate it',
      (tester) async {
    final api = await loggedIn(FakeBackend({
      'GET /api/content/abc/review': (_) => (200, {
            ...ticket(status: 'REJECTED', canApprove: false),
            'hold': {
              'kind': 'rejected',
              'stage': 'qa',
              'reason': 'qa_rejected: score 58.75',
              'can_resume': false,
            },
            'source': {'brief': 'Cô giáo hỏi cả lớp', 'answer': null},
          }),
    }));
    await tester.pumpWidget(host(api, const ReviewScreen(id: 'abc')));
    await tester.pumpAndSettle();
    expect(find.text('Vì sao bài bị loại'), findsOneWidget);
    expect(find.textContaining('chấm 58.75 điểm (dưới 65)'), findsOneWidget);
    expect(find.textContaining('không cần bạn bấm gì'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Sửa và tạo lại'), findsOneWidget);
    expect(find.widgetWithText(FilledButton, 'Thử lại'), findsNothing);
    expect(find.widgetWithText(FilledButton, 'Duyệt'), findsNothing);
  });

  testWidgets('a paused item explains the budget wait', (tester) async {
    final api = await loggedIn(FakeBackend({
      'GET /api/content/abc/review': (_) => (200, {
            ...ticket(status: 'QA_PENDING', canApprove: false),
            'waiting': {'reason': 'waiting: budget_global_daily', 'retry_after': 'x'},
          }),
    }));
    await tester.pumpWidget(host(api, const ReviewScreen(id: 'abc')));
    await tester.pumpAndSettle();
    expect(find.textContaining('hết ngân sách (trong ngày)'), findsOneWidget);
    expect(find.textContaining('Chạy lại ngay'), findsWidgets);
  });
}


void qaFlagTests() {
  testWidgets('a poor automatic check is shown to the owner as a warning, approval stays possible',
      (tester) async {
    final api = await loggedIn(FakeBackend({
      'GET /api/content/abc/review': (_) {
        final t = ticket();
        (t['explanation'] as Map<String, Object?>)['qa_flag'] = {
          'score': 58.75,
          'issues': ['c1: không có học sinh thì thầm'],
          'reason': 'unfixable',
        };
        return (200, t);
      },
    }));
    await tester.pumpWidget(host(api, const ReviewScreen(id: 'abc')));
    await tester.pumpAndSettle();
    expect(find.text('Cảnh báo chất lượng'), findsOneWidget);
    expect(find.textContaining('chấm 58.75 điểm'), findsOneWidget);
    expect(find.textContaining('không có học sinh thì thầm'), findsOneWidget);
    expect(tester.widget<FilledButton>(find.widgetWithText(FilledButton, 'Duyệt')).onPressed,
        isNotNull);
  });
}


void publishOnApproveTests() {
  Future<FakeBackend> approving(Map<String, Object?> settings, Map<String, Object?> publish) async =>
      FakeBackend({
        'GET /api/content/abc/review': (_) => (200, ticket()),
        'GET /api/system/settings': (_) => (200, settings),
        'POST /api/content/abc/approve': (_) => (200, {'publish': publish}),
      });

  Future<void> openApproveDialog(WidgetTester tester, FakeBackend backend) async {
    final api = await loggedIn(backend);
    await tester.pumpWidget(host(api, const ReviewScreen(id: 'abc')));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Duyệt'));
    await tester.pumpAndSettle();
  }

  testWidgets('the approve dialog says the post goes out at once, on which platforms', (tester) async {
    final backend = await approving({
      'publish_on_approve': true,
      'publishing_kill_switch': false,
      'automation_enabled': true,
      'youtube_enabled': true,
    }, {'queued': true, 'platforms': ['facebook', 'youtube']});
    await openApproveDialog(tester, backend);
    expect(find.textContaining('ĐĂNG NGAY lên Facebook và YouTube'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Duyệt').last);
    await tester.pumpAndSettle();
    expect(find.textContaining('xếp hàng đăng ngay lên Facebook + YouTube'), findsOneWidget);
  });

  testWidgets('with the kill switch on the dialog warns that nothing will be published',
      (tester) async {
    final backend = await approving({
      'publish_on_approve': true,
      'publishing_kill_switch': true,
      'automation_enabled': true,
      'youtube_enabled': false,
    }, {'queued': false, 'reason': 'kill_switch'});
    await openApproveDialog(tester, backend);
    expect(find.textContaining('Công tắc đăng đang TẮT'), findsOneWidget);
    await tester.tap(find.widgetWithText(FilledButton, 'Duyệt').last);
    await tester.pumpAndSettle();
    expect(find.textContaining('Chưa đăng vì công tắc đăng đang tắt'), findsOneWidget);
  });

  testWidgets('with auto-publish off, approving only approves', (tester) async {
    final backend = await approving({'publish_on_approve': false},
        {'queued': false, 'reason': 'disabled'});
    await openApproveDialog(tester, backend);
    expect(find.textContaining('bạn chọn giờ đăng sau'), findsOneWidget);
  });
}


void queueTitleTests() {
  testWidgets('a row with no text yet shows the idea it was made from', (tester) async {
    final api = await loggedIn(FakeBackend({
      'GET /api/content': (_) => (200, {
            'items': [
              {'id': '1', 'lane': 'daily_life', 'status': 'DRAFT', 'overlay_text': null,
               'caption': null, 'brief': 'Một con mèo béo cố chui vào hộp nhỏ'},
              {'id': '2', 'lane': 'rebus', 'status': 'DRAFT', 'overlay_text': '  ',
               'caption': 'Đoán xem đây là gì?', 'brief': 'x'},
              {'id': '3', 'lane': 'rebus', 'status': 'DRAFT'},
            ]
          }),
    }));
    await tester.pumpWidget(host(api, const Scaffold(body: QueueScreen())));
    await tester.pumpAndSettle();
    expect(find.text('Một con mèo béo cố chui vào hộp nhỏ'), findsOneWidget);
    expect(find.text('Đoán xem đây là gì?'), findsOneWidget); // blank text falls through to caption
    expect(find.text('(chưa có nội dung)'), findsOneWidget); // only when there is truly nothing
  });
}


void cancelTests() {
  testWidgets('a scheduled post can be cancelled after confirmation', (tester) async {
    var cancelled = 0;
    final api = await loggedIn(FakeBackend({
      'GET /api/content/abc/review': (_) => (200, ticket(status: 'SCHEDULED', canApprove: false)),
      'POST /api/content/abc/cancel-schedule': (_) {
        cancelled++;
        return (200, {'cancelled': ['facebook'], 'status': 'APPROVED'});
      },
    }));
    await tester.pumpWidget(host(api, const ReviewScreen(id: 'abc')));
    await tester.pumpAndSettle();
    expect(find.widgetWithText(FilledButton, 'Duyệt'), findsNothing);
    await tester.tap(find.text('Hủy lịch đăng'));
    await tester.pumpAndSettle();
    expect(cancelled, 0); // nothing before the owner confirms
    await tester.tap(find.widgetWithText(FilledButton, 'Hủy lịch'));
    await tester.pumpAndSettle();
    expect(cancelled, 1);
  });

  testWidgets('an approved item can have its approval withdrawn', (tester) async {
    String? note;
    final api = await loggedIn(FakeBackend({
      'GET /api/content/abc/review': (_) => (200, ticket(status: 'APPROVED', canApprove: false)),
      'POST /api/content/abc/reject': (c) {
        note = (c.body as Map)['note'] as String?;
        return (200, {});
      },
    }));
    await tester.pumpWidget(host(api, const ReviewScreen(id: 'abc')));
    await tester.pumpAndSettle();
    expect(find.text('Lên lịch đăng'), findsOneWidget);
    await tester.tap(find.text('Hủy duyệt'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Đồng ý'));
    await tester.pumpAndSettle();
    expect(note, 'Hủy duyệt');
  });

  testWidgets('taking a published post down needs a reason and the password', (tester) async {
    Map<String, dynamic>? sent;
    final api = await loggedIn(FakeBackend({
      'GET /api/content/abc/review': (_) => (200, ticket(status: 'PUBLISHED', canApprove: false)),
      'POST /api/content/abc/retract': (c) {
        sent = Map<String, dynamic>.from(c.body as Map);
        return (200, {'results': {'facebook': 'retracted'}});
      },
    }));
    await tester.pumpWidget(host(api, const ReviewScreen(id: 'abc')));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Gỡ bài đã đăng'));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Gỡ bài'));
    await tester.pumpAndSettle();
    expect(sent, isNull); // nothing is sent without a reason and a password
    expect(find.textContaining('Cần nhập lý do'), findsOneWidget);
    await tester.tap(find.text('Gỡ bài đã đăng'));
    await tester.pumpAndSettle();
    await tester.enterText(find.widgetWithText(TextField, 'Lý do gỡ (bắt buộc)'), 'Sai nội dung');
    await tester.enterText(find.widgetWithText(TextField, 'Mật khẩu'), 'my password');
    await tester.tap(find.widgetWithText(FilledButton, 'Gỡ bài'));
    await tester.pumpAndSettle();
    expect(sent, {'reason': 'Sai nội dung', 'password': 'my password'});
  });
}

class _FakeSaver implements MediaSaver {
  _FakeSaver({this.fail = false});

  final bool fail;
  final saved = <String>[];

  @override
  Future<void> saveVideo({
    required ApiClient api,
    required String path,
    required String name,
    void Function(double progress)? onProgress,
  }) async {
    if (fail) throw ApiException(0, 'network', 'x');
    saved.add('$path -> $name');
  }
}

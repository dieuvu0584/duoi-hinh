import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';

import 'token_store.dart';

/// Error returned by the backend (`{"error": {"code", "message", "fields"}}`) or a network failure.
class ApiException implements Exception {
  ApiException(this.status, this.code, this.message);

  final int status; // 0 = no response (network)
  final String code;
  final String message;

  bool get isNetwork => status == 0;
  bool get needsReauth => code == 'reauth_required';

  @override
  String toString() => 'ApiException($status, $code)'; // never include request data
}

/// Talks to the backend. Holds the tokens, refreshes them (one refresh at a time, because refresh
/// tokens rotate and a reused one would revoke the device) and never logs a token.
class ApiClient {
  ApiClient({required this.store, Dio? dio, this.onLoggedOut})
      : _dio = dio ?? Dio(BaseOptions(connectTimeout: const Duration(seconds: 15)));

  final TokenStore store;
  final Dio _dio;
  void Function()? onLoggedOut; // set by the app shell so a revoked login returns to the login page

  StoredAuth? _auth;
  Future<void>? _refreshing;

  bool get isLoggedIn => _auth != null;
  String? get baseUrl => _auth?.baseUrl;

  /// Loads saved credentials; true when the owner is still logged in.
  Future<bool> restore() async {
    _auth = await store.read();
    return _auth != null;
  }

  static String normaliseBaseUrl(String raw) {
    var url = raw.trim();
    if (!url.startsWith('http://') && !url.startsWith('https://')) url = 'http://$url';
    while (url.endsWith('/')) {
      url = url.substring(0, url.length - 1);
    }
    return url;
  }

  Future<SavedLogin?> savedLogin() => store.readLogin();

  /// Tries the remembered login (only when a password was kept); true when it worked.
  Future<bool> loginWithSaved() async {
    final saved = await store.readLogin();
    if (saved == null || saved.password == null || saved.password!.isEmpty) return false;
    try {
      await login(
          baseUrl: saved.baseUrl,
          username: saved.username,
          password: saved.password!,
          remember: true);
      return true;
    } on ApiException {
      return false;
    }
  }

  Future<void> login({
    required String baseUrl,
    required String username,
    required String password,
    String deviceLabel = 'Điện thoại của chủ',
    bool remember = false,
  }) async {
    final url = normaliseBaseUrl(baseUrl);
    final body = await _send('POST', '$url/api/auth/login',
        data: {'username': username, 'password': password, 'device_label': deviceLabel},
        authenticated: false);
    _auth = _fromTokens(url, body as Map<String, dynamic>);
    await store.write(_auth!);
    // Server and user name are always remembered for the next sign-in; the password only when
    // the owner asked for it.
    await store.writeLogin(
        SavedLogin(baseUrl: url, username: username, password: remember ? password : null));
  }

  /// Explicit log out: the tokens and any remembered password are wiped (server and user name stay
  /// so the next sign-in is quick).
  Future<void> logout() async {
    try {
      if (_auth != null) await _request('POST', '/api/auth/logout');
    } on ApiException {
      // The device is forgotten locally either way.
    }
    final saved = await store.readLogin();
    if (saved != null) {
      await store.writeLogin(SavedLogin(baseUrl: saved.baseUrl, username: saved.username));
    }
    await _forget();
  }

  Future<void> _forget() async {
    _auth = null;
    await store.clear();
    onLoggedOut?.call();
  }

  StoredAuth _fromTokens(String baseUrl, Map<String, dynamic> t) => StoredAuth(
        baseUrl: baseUrl,
        accessToken: t['access_token'] as String,
        accessExpiresAt: DateTime.parse(t['access_expires_at'] as String),
        refreshToken: t['refresh_token'] as String,
      );

  Future<void> _refresh() {
    final running = _refreshing;
    if (running != null) return running;
    final started = _doRefresh().whenComplete(() => _refreshing = null);
    _refreshing = started;
    return started;
  }

  Future<void> _doRefresh() async {
    final current = _auth;
    if (current == null) throw ApiException(401, 'not_authenticated', 'Chưa đăng nhập');
    try {
      final body = await _send('POST', '${current.baseUrl}/api/auth/refresh',
          data: {'refresh_token': current.refreshToken}, authenticated: false);
      _auth = _fromTokens(current.baseUrl, body as Map<String, dynamic>);
      await store.write(_auth!);
    } on ApiException catch (e) {
      if (!e.isNetwork) await _forget(); // revoked or expired: log in again
      rethrow;
    }
  }

  /// `Authorization` header for media requests (video player), refreshed when about to expire.
  Future<Map<String, String>> authHeaders() async {
    await _ensureFresh();
    return {'Authorization': 'Bearer ${_auth!.accessToken}'};
  }

  String mediaUrl(String path) => '${_auth!.baseUrl}$path';

  /// Downloads a media file (with the owner's token) to [savePath].
  Future<void> downloadMedia(String path, String savePath,
      {void Function(double progress)? onProgress}) async {
    await _ensureFresh();
    try {
      final res = await _dio.download(
        mediaUrl(path),
        savePath,
        options: Options(
          headers: {'Authorization': 'Bearer ${_auth!.accessToken}'},
          receiveTimeout: const Duration(minutes: 5),
        ),
        onReceiveProgress: (received, total) {
          if (total > 0) onProgress?.call(received / total);
        },
      );
      if (res.statusCode != 200) {
        throw ApiException(res.statusCode ?? 0, 'download_failed', 'Không tải được video');
      }
    } on DioException {
      throw ApiException(0, 'network', 'Không kết nối được máy chủ');
    }
  }

  Future<void> _ensureFresh() async {
    final a = _auth;
    if (a == null) throw ApiException(401, 'not_authenticated', 'Chưa đăng nhập');
    if (a.accessExpiresAt.isBefore(DateTime.now().toUtc().add(const Duration(seconds: 30)))) {
      await _refresh();
    }
  }

  Future<dynamic> _request(String method, String path,
      {Object? data, Map<String, dynamic>? query}) async {
    await _ensureFresh();
    try {
      return await _send(method, '${_auth!.baseUrl}$path',
          data: data, query: query, token: _auth!.accessToken);
    } on ApiException catch (e) {
      if (e.status != 401) rethrow;
      await _refresh(); // the server said the token is no longer valid: one retry
      return _send(method, '${_auth!.baseUrl}$path',
          data: data, query: query, token: _auth!.accessToken);
    }
  }

  Future<dynamic> _send(String method, String url,
      {Object? data, Map<String, dynamic>? query, String? token, bool authenticated = true}) async {
    try {
      final res = await _dio.request<dynamic>(
        url,
        data: data,
        queryParameters: query,
        options: Options(
          method: method,
          headers: {'Authorization': ?(token == null ? null : 'Bearer $token')},
          validateStatus: (_) => true,
          receiveTimeout: const Duration(seconds: 60),
        ),
      );
      final status = res.statusCode ?? 0;
      if (status >= 200 && status < 300) return res.data;
      final err = (res.data is Map ? (res.data as Map)['error'] : null);
      throw ApiException(
        status,
        err is Map ? '${err['code']}' : 'http_$status',
        err is Map ? '${err['message']}' : 'Lỗi máy chủ ($status)',
      );
    } on DioException {
      throw ApiException(0, 'network', 'Không kết nối được máy chủ');
    }
  }

  // ------------------------------------------------------------------ endpoints
  Future<List<Map<String, dynamic>>> queue({String? status}) async {
    final body = await _request('GET', '/api/content',
        query: {'status': ?status, 'limit': 100});
    return List<Map<String, dynamic>>.from((body as Map)['items'] as List);
  }

  Future<Map<String, dynamic>> ticket(String id) async =>
      Map<String, dynamic>.from(await _request('GET', '/api/content/$id/review') as Map);

  /// Approves an item whose automatic review could not finish (the owner's own review stands in).
  Future<Map<String, dynamic>> approveHeld(String id, {String? note}) async =>
      Map<String, dynamic>.from(
          await _request('POST', '/api/content/$id/approve-held', data: {'note': note}) as Map);

  /// Approves; the answer says whether the post was also queued for publishing right away.
  Future<Map<String, dynamic>> approve(String id, {String? note}) async =>
      Map<String, dynamic>.from(
          await _request('POST', '/api/content/$id/approve', data: {'note': note}) as Map);

  Future<void> reject(String id, {String? note}) =>
      _request('POST', '/api/content/$id/reject', data: {'note': note});

  Future<void> completeHumanReview(String id) =>
      _request('POST', '/api/content/$id/human-review/complete');

  Future<void> resumeHeld(String id, {String? instructions}) => _request(
      'POST', '/api/content/$id/resume',
      data: {'instructions': ?_nonEmpty(instructions)});

  /// Takes a queued/scheduled post back before it is public (drafts at the platforms are deleted).
  Future<Map<String, dynamic>> cancelSchedule(String id) async => Map<String, dynamic>.from(
      await _request('POST', '/api/content/$id/cancel-schedule') as Map);

  /// Takes a PUBLISHED post down. Needs the password and a reason.
  Future<Map<String, dynamic>> retractPublished(String id,
          {required String reason, required String password}) async =>
      Map<String, dynamic>.from(await _request('POST', '/api/content/$id/retract',
          data: {'reason': reason, 'password': password}) as Map);

  Future<void> retryNow(String id, {String? instructions}) => _request(
      'POST', '/api/content/$id/retry',
      data: {'instructions': ?_nonEmpty(instructions)});

  static String? _nonEmpty(String? s) => s == null || s.trim().isEmpty ? null : s.trim();

  Future<void> schedule(String id, DateTime when, {List<String> platforms = const ['facebook']}) =>
      _request('POST', '/api/content/$id/schedule',
          data: {'scheduled_for': when.toUtc().toIso8601String(), 'platforms': platforms});

  Future<Map<String, dynamic>> createIdea(
          {required String lane,
          required String brief,
          String? answer,
          String? instructions}) async =>
      Map<String, dynamic>.from(await _request('POST', '/api/content/ideas', data: {
        'lane': lane,
        'brief': brief,
        'answer': ?(answer == null || answer.trim().isEmpty ? null : answer.trim()),
        'instructions':
            ?(instructions == null || instructions.trim().isEmpty ? null : instructions.trim()),
      }) as Map);

  Future<Map<String, dynamic>> settings() async =>
      Map<String, dynamic>.from(await _request('GET', '/api/system/settings') as Map);

  Future<Map<String, dynamic>> putSettings(Map<String, dynamic> changes,
          {String? password}) async =>
      Map<String, dynamic>.from(await _request('PUT', '/api/system/settings', data: {
        'changes': changes,
        'password': ?password,
      }) as Map);

  Future<Map<String, dynamic>> stopPublishing() async =>
      Map<String, dynamic>.from(await _request('POST', '/api/system/publishing-stop') as Map);

  Future<Map<String, dynamic>> resumePublishing(String password) async =>
      Map<String, dynamic>.from(await _request('POST', '/api/system/publishing-resume',
          data: {'password': password}) as Map);

  Future<Map<String, dynamic>> costs() async =>
      Map<String, dynamic>.from(await _request('GET', '/api/costs') as Map);

  Future<void> putProviderBudget(
          {required String provider,
          required String monthly,
          String? daily,
          String? password}) =>
      _request('PUT', '/api/system/provider-budgets', data: {
        'provider': provider,
        'monthly_limit_usd': monthly,
        'daily_limit_usd': ?daily,
        'password': ?password,
      });

  // ------------------------------------------------------------------ idea inbox
  Future<Map<String, dynamic>> ideaCandidates() async =>
      Map<String, dynamic>.from(await _request('GET', '/api/ideas/candidates') as Map);

  Future<Map<String, dynamic>> ideaCandidate(String id) async =>
      Map<String, dynamic>.from(await _request('GET', '/api/ideas/candidates/$id') as Map);

  Future<Map<String, dynamic>> proposeIdea(String id, String lane) async =>
      Map<String, dynamic>.from(
          await _request('POST', '/api/ideas/candidates/$id/propose', data: {'lane': lane}) as Map);

  Future<Map<String, dynamic>> createFromIdea(
          {required String id,
          required String lane,
          required String brief,
          String? answer,
          String? instructions}) async =>
      Map<String, dynamic>.from(await _request('POST', '/api/ideas/candidates/$id/create', data: {
        'lane': lane,
        'brief': brief,
        'instructions':
            ?(instructions == null || instructions.trim().isEmpty ? null : instructions.trim()),
        'answer': ?(answer == null || answer.trim().isEmpty ? null : answer.trim()),
      }) as Map);

  Future<void> dismissIdea(String id) => _request('POST', '/api/ideas/candidates/$id/dismiss');

  Future<void> scanNow() => _request('POST', '/api/ideas/scan');

  Future<List<Map<String, dynamic>>> feedSources() async {
    final body = await _request('GET', '/api/ideas/sources');
    return List<Map<String, dynamic>>.from((body as Map)['items'] as List);
  }

  Future<void> addFeedSource(String name, String url) =>
      _request('POST', '/api/ideas/sources', data: {'name': name, 'url': url});

  Future<void> setFeedSourceEnabled(String id, bool enabled) =>
      _request('PATCH', '/api/ideas/sources/$id', data: {'enabled': enabled});

  Future<void> deleteFeedSource(String id) => _request('DELETE', '/api/ideas/sources/$id');

  Future<List<Map<String, dynamic>>> idioms() async {
    final body = await _request('GET', '/api/ideas/idioms');
    return List<Map<String, dynamic>>.from((body as Map)['items'] as List);
  }

  Future<void> addIdiom(String text, String meaning) =>
      _request('POST', '/api/ideas/idioms', data: {'text': text, 'meaning': meaning});

  Future<void> deleteIdiom(String id) => _request('DELETE', '/api/ideas/idioms/$id');

  Future<List<Map<String, dynamic>>> promptsForOtherTools(
      {required String lane, required String brief, String? instructions}) async {
    final body = await _request('POST', '/api/ideas/prompts', data: {
      'lane': lane,
      'brief': brief,
      'instructions': ?_nonEmpty(instructions),
    });
    return List<Map<String, dynamic>>.from((body as Map)['prompts'] as List);
  }

  /// Drafts a meme idea from a picture and the owner's own explanation (a few cents).
  Future<Map<String, dynamic>> ideaFromImage(Uint8List image, {String? note}) async =>
      Map<String, dynamic>.from(await _request('POST', '/api/ideas/from-image', data: {
        'image_base64': base64Encode(image),
        'note': ?_nonEmpty(note),
      }) as Map);

  Future<void> rateIdea(String id, int rating) =>
      _request('POST', '/api/ideas/candidates/$id/rating', data: {'rating': rating});

  Future<void> saveFeedback(String id,
          {required int rating,
          required List<String> labels,
          String? note,
          Map<String, Map<String, int>> scores = const {}}) =>
      _request('POST', '/api/content/$id/feedback', data: {
        'rating': rating,
        'labels': labels,
        'note': (note ?? '').trim(),
        'scores': scores,
      });

  Future<Map<String, dynamic>> feedbackSummary() async =>
      Map<String, dynamic>.from(await _request('GET', '/api/feedback/summary') as Map);

  Future<List<Map<String, dynamic>>> lessons() async {
    final body = await _request('GET', '/api/lessons');
    return List<Map<String, dynamic>>.from((body as Map)['lessons'] as List);
  }

  Future<void> addLesson(String text, {String lane = 'all'}) =>
      _request('POST', '/api/lessons', data: {'text': text, 'lane': lane});

  Future<void> setLessonStatus(String id, String status) =>
      _request('PATCH', '/api/lessons/$id', data: {'status': status});

  Future<void> deleteLesson(String id) => _request('DELETE', '/api/lessons/$id');

  /// Returns how many new lessons Claude proposed (nothing is applied until approved).
  Future<int> distillLessons() async {
    final body = await _request('POST', '/api/lessons/distill');
    return ((body as Map)['proposed'] as List).length;
  }
}

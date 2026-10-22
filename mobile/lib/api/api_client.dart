import 'dart:async';

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

  Future<void> login({
    required String baseUrl,
    required String username,
    required String password,
    String deviceLabel = 'Điện thoại của chủ',
  }) async {
    final url = normaliseBaseUrl(baseUrl);
    final body = await _send('POST', '$url/api/auth/login',
        data: {'username': username, 'password': password, 'device_label': deviceLabel},
        authenticated: false);
    _auth = _fromTokens(url, body as Map<String, dynamic>);
    await store.write(_auth!);
  }

  Future<void> logout() async {
    try {
      if (_auth != null) await _request('POST', '/api/auth/logout');
    } on ApiException {
      // The device is forgotten locally either way.
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

  Future<void> approve(String id, {String? note}) =>
      _request('POST', '/api/content/$id/approve', data: {'note': note});

  Future<void> reject(String id, {String? note}) =>
      _request('POST', '/api/content/$id/reject', data: {'note': note});

  Future<void> completeHumanReview(String id) =>
      _request('POST', '/api/content/$id/human-review/complete');

  Future<void> retryNow(String id) => _request('POST', '/api/content/$id/retry');

  Future<void> schedule(String id, DateTime when) => _request('POST', '/api/content/$id/schedule',
      data: {'scheduled_for': when.toUtc().toIso8601String()});

  Future<Map<String, dynamic>> createIdea(
          {required String lane, required String brief, String? answer}) async =>
      Map<String, dynamic>.from(await _request('POST', '/api/content/ideas', data: {
        'lane': lane,
        'brief': brief,
        if (answer != null && answer.trim().isNotEmpty) 'answer': answer.trim(),
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
}

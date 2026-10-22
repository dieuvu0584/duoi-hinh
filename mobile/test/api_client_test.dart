import 'dart:io';

import 'package:dio/dio.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:nhd_admin/api/api_client.dart';
import 'package:nhd_admin/api/token_store.dart';

import 'fake_backend.dart';

const refreshOne = 'ref1refreshrefreshrefresh1';

ApiClient clientWith(FakeBackend backend, MemoryTokenStore store,
        {void Function()? onLoggedOut}) =>
    ApiClient(store: store, dio: Dio()..httpClientAdapter = backend, onLoggedOut: onLoggedOut);

StoredAuth saved({required Duration expiresIn}) => StoredAuth(
    baseUrl: 'http://h',
    accessToken: 'old',
    accessExpiresAt: DateTime.now().toUtc().add(expiresIn),
    refreshToken: refreshOne);

void main() {
  test('login stores the tokens and sends the bearer header', () async {
    final backend = FakeBackend({
      'POST /api/auth/login': (_) => (200, tokens('acc1', refreshOne)),
      'GET /api/content': (_) => (200, {'items': []}),
    });
    final store = MemoryTokenStore();
    final api = clientWith(backend, store);
    await api.login(baseUrl: '127.0.0.1:8000/', username: 'owner', password: 'pw');
    expect(api.baseUrl, 'http://127.0.0.1:8000');
    expect(store.value!.accessToken, 'acc1');
    await api.queue();
    expect(backend.calls.last.auth, 'Bearer acc1');
  });

  test('an expired access token is refreshed once, even for concurrent requests', () async {
    var refreshes = 0;
    final backend = FakeBackend({
      'POST /api/auth/refresh': (_) {
        refreshes++;
        return (200, tokens('acc2', 'ref2refreshrefreshrefresh2'));
      },
      'GET /api/content': (_) => (200, {'items': []}),
    });
    final store = MemoryTokenStore()..value = saved(expiresIn: const Duration(minutes: -1));
    final api = clientWith(backend, store);
    await api.restore();
    await Future.wait([api.queue(), api.queue(), api.queue()]);
    expect(refreshes, 1); // refresh tokens rotate: a second use would look like theft
    expect(store.value!.refreshToken, 'ref2refreshrefreshrefresh2');
    final listCalls = backend.calls.where((c) => c.path == '/api/content');
    expect(listCalls.every((c) => c.auth == 'Bearer acc2'), isTrue);
  });

  test('a 401 from the server triggers one refresh and one retry', () async {
    var first = true;
    final backend = FakeBackend({
      'GET /api/content': (_) {
        if (first) {
          first = false;
          return (401, {'error': {'code': 'token_invalid', 'message': 'x'}});
        }
        return (200, {'items': [{'id': '1'}]});
      },
      'POST /api/auth/refresh': (_) => (200, tokens('acc3', 'ref3refreshrefreshrefresh3')),
    });
    final store = MemoryTokenStore()..value = saved(expiresIn: const Duration(minutes: 10));
    final api = clientWith(backend, store);
    await api.restore();
    final items = await api.queue();
    expect(items.length, 1);
    expect(backend.calls.where((c) => c.path == '/api/auth/refresh').length, 1);
  });

  test('a refused refresh logs the owner out and clears the stored tokens', () async {
    var loggedOut = false;
    final backend = FakeBackend({
      'POST /api/auth/refresh': (_) =>
          (401, {'error': {'code': 'refresh_invalid', 'message': 'x'}}),
    });
    final store = MemoryTokenStore()..value = saved(expiresIn: const Duration(minutes: -1));
    final api = clientWith(backend, store, onLoggedOut: () => loggedOut = true);
    await api.restore();
    await expectLater(api.queue(), throwsA(isA<ApiException>()));
    expect(loggedOut, isTrue);
    expect(store.value, isNull);
    expect(api.isLoggedIn, isFalse);
  });

  test('being offline is a network error and does not log the owner out', () async {
    final backend = FakeBackend({})..offline = true;
    final store = MemoryTokenStore()..value = saved(expiresIn: const Duration(minutes: -1));
    final api = clientWith(backend, store);
    await api.restore();
    await expectLater(api.queue(), throwsA(predicate((e) => e is ApiException && e.isNetwork)));
    expect(store.value, isNotNull);
  });

  test('server errors keep their code, and exceptions never contain secrets', () async {
    final backend = FakeBackend({
      'POST /api/auth/login': (_) =>
          (403, {'error': {'code': 'invalid_credentials', 'message': 'no'}}),
    });
    final api = clientWith(backend, MemoryTokenStore());
    try {
      await api.login(baseUrl: 'http://h', username: 'o', password: 'secretpw');
      fail('should throw');
    } on ApiException catch (e) {
      expect(e.code, 'invalid_credentials');
      expect(e.toString().contains('secretpw'), isFalse);
    }
  });

  test('a re-auth request is recognised', () {
    expect(ApiException(403, 'reauth_required', 'm').needsReauth, isTrue);
  });

  test('a media download sends the token and writes the file', () async {
    final backend = FakeBackend({
      'GET /api/content/abc/media/reel_9x16': (_) => (200, {'fake': 'video bytes'}),
    });
    final store = MemoryTokenStore()..value = saved(expiresIn: const Duration(minutes: 10));
    final api = clientWith(backend, store);
    await api.restore();
    final dir = await Directory.systemTemp.createTemp('nhd_dl');
    final target = '${dir.path}/v.mp4';
    try {
      var last = -1.0;
      await api.downloadMedia('/api/content/abc/media/reel_9x16', target, onProgress: (p) => last = p);
      expect(File(target).existsSync(), isTrue);
      expect(backend.calls.last.auth, 'Bearer old');
      expect(last, anyOf(-1.0, 1.0));
    } finally {
      await dir.delete(recursive: true);
    }
  });

  test('a download of a missing file fails with an ApiException', () async {
    final backend = FakeBackend({});
    final store = MemoryTokenStore()..value = saved(expiresIn: const Duration(minutes: 10));
    final api = clientWith(backend, store);
    await api.restore();
    final dir = await Directory.systemTemp.createTemp('nhd_dl');
    try {
      await expectLater(api.downloadMedia('/api/content/x/media/reel_9x16', '${dir.path}/v.mp4'),
          throwsA(isA<ApiException>()));
    } finally {
      await dir.delete(recursive: true);
    }
  });
}

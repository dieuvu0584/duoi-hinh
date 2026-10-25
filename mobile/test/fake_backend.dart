import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:dio/dio.dart';

class Call {
  Call(this.method, this.path, this.auth, this.body, [this.query = const {}]);
  final String method;
  final String path;
  final String? auth;
  final Object? body;
  final Map<String, String> query;
}

typedef Handler = (int, Object?) Function(Call call);

/// A scripted backend: routes are `"METHOD /path"` -> handler returning (status, json).
class FakeBackend implements HttpClientAdapter {
  FakeBackend(this.routes);

  final Map<String, Handler> routes;
  final calls = <Call>[];
  bool offline = false;

  @override
  Future<ResponseBody> fetch(RequestOptions options, Stream<Uint8List>? requestStream,
      Future<void>? cancelFuture) async {
    if (offline) {
      throw DioException.connectionError(requestOptions: options, reason: 'offline');
    }
    final call = Call(
        options.method, options.uri.path, options.headers['Authorization'] as String?, options.data,
        options.uri.queryParameters);
    calls.add(call);
    await Future<void>.delayed(Duration.zero);
    final handler = routes['${options.method} ${options.uri.path}'];
    final (status, json) = handler == null
        ? (404, {'error': {'code': 'not_found', 'message': 'no route'}})
        : handler(call);
    return ResponseBody.fromString(jsonEncode(json), status,
        headers: {Headers.contentTypeHeader: ['application/json']});
  }

  @override
  void close({bool force = false}) {}
}

Map<String, Object?> tokens(String access, String refresh,
        {Duration ttl = const Duration(minutes: 15)}) =>
    {
      'access_token': access,
      'access_expires_at': DateTime.now().toUtc().add(ttl).toIso8601String(),
      'refresh_token': refresh,
      'refresh_expires_at': DateTime.now().toUtc().add(const Duration(days: 30)).toIso8601String(),
      'device_id': '00000000-0000-0000-0000-000000000001',
      'token_type': 'Bearer',
    };

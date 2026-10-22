import 'dart:convert';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

class StoredAuth {
  StoredAuth({
    required this.baseUrl,
    required this.accessToken,
    required this.accessExpiresAt,
    required this.refreshToken,
  });

  final String baseUrl;
  final String accessToken;
  final DateTime accessExpiresAt;
  final String refreshToken;

  Map<String, dynamic> toJson() => {
        'baseUrl': baseUrl,
        'accessToken': accessToken,
        'accessExpiresAt': accessExpiresAt.toUtc().toIso8601String(),
        'refreshToken': refreshToken,
      };

  static StoredAuth? fromJson(Map<String, dynamic> j) {
    try {
      return StoredAuth(
        baseUrl: j['baseUrl'] as String,
        accessToken: j['accessToken'] as String,
        accessExpiresAt: DateTime.parse(j['accessExpiresAt'] as String),
        refreshToken: j['refreshToken'] as String,
      );
    } catch (_) {
      return null; // damaged entry: treat as logged out
    }
  }
}

/// Where tokens live. The real implementation is the platform secure storage (Android Keystore);
/// tokens are never written to shared preferences or logs.
abstract class TokenStore {
  Future<StoredAuth?> read();
  Future<void> write(StoredAuth auth);
  Future<void> clear();
}

class SecureTokenStore implements TokenStore {
  SecureTokenStore([FlutterSecureStorage? storage])
      : _storage = storage ?? const FlutterSecureStorage();

  static const _key = 'nhd_auth';
  final FlutterSecureStorage _storage;

  @override
  Future<StoredAuth?> read() async {
    final raw = await _storage.read(key: _key);
    if (raw == null) return null;
    try {
      return StoredAuth.fromJson(jsonDecode(raw) as Map<String, dynamic>);
    } catch (_) {
      return null;
    }
  }

  @override
  Future<void> write(StoredAuth auth) =>
      _storage.write(key: _key, value: jsonEncode(auth.toJson()));

  @override
  Future<void> clear() => _storage.delete(key: _key);
}

class MemoryTokenStore implements TokenStore {
  StoredAuth? value;

  @override
  Future<StoredAuth?> read() async => value;

  @override
  Future<void> write(StoredAuth auth) async => value = auth;

  @override
  Future<void> clear() async => value = null;
}

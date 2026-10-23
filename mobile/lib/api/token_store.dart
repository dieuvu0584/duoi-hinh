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

/// What "remember my login" keeps on this phone (in the platform secure storage). The password is
/// only kept when the owner ticked the box, and is wiped on an explicit log out.
class SavedLogin {
  SavedLogin({required this.baseUrl, required this.username, this.password});

  final String baseUrl;
  final String username;
  final String? password;

  Map<String, dynamic> toJson() =>
      {'baseUrl': baseUrl, 'username': username, 'password': password};

  static SavedLogin? fromJson(Map<String, dynamic> j) {
    try {
      return SavedLogin(
        baseUrl: j['baseUrl'] as String,
        username: j['username'] as String,
        password: j['password'] as String?,
      );
    } catch (_) {
      return null;
    }
  }
}

/// Where tokens live. The real implementation is the platform secure storage (Android Keystore);
/// tokens are never written to shared preferences or logs.
abstract class TokenStore {
  Future<StoredAuth?> read();
  Future<void> write(StoredAuth auth);
  Future<void> clear();
  Future<SavedLogin?> readLogin();
  Future<void> writeLogin(SavedLogin login);
  Future<void> clearLogin();
}

class SecureTokenStore implements TokenStore {
  SecureTokenStore([FlutterSecureStorage? storage])
      : _storage = storage ?? const FlutterSecureStorage();

  static const _key = 'nhd_auth';
  static const _loginKey = 'nhd_login';
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

  @override
  Future<SavedLogin?> readLogin() async {
    final raw = await _storage.read(key: _loginKey);
    if (raw == null) return null;
    try {
      return SavedLogin.fromJson(jsonDecode(raw) as Map<String, dynamic>);
    } catch (_) {
      return null;
    }
  }

  @override
  Future<void> writeLogin(SavedLogin login) =>
      _storage.write(key: _loginKey, value: jsonEncode(login.toJson()));

  @override
  Future<void> clearLogin() => _storage.delete(key: _loginKey);
}

class MemoryTokenStore implements TokenStore {
  StoredAuth? value;
  SavedLogin? login;

  @override
  Future<SavedLogin?> readLogin() async => login;

  @override
  Future<void> writeLogin(SavedLogin l) async => login = l;

  @override
  Future<void> clearLogin() async => login = null;

  @override
  Future<StoredAuth?> read() async => value;

  @override
  Future<void> write(StoredAuth auth) async => value = auth;

  @override
  Future<void> clear() async => value = null;
}

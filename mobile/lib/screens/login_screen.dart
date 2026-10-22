import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import '../ui_helpers.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({super.key, required this.onLoggedIn});

  final VoidCallback onLoggedIn;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  final _server = TextEditingController(text: 'http://127.0.0.1:8000');
  final _user = TextEditingController(text: 'owner');
  final _password = TextEditingController();
  bool _busy = false;
  String? _error;

  Future<void> _submit() async {
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      await context.read<ApiClient>().login(
            baseUrl: _server.text,
            username: _user.text.trim(),
            password: _password.text,
          );
      _password.clear();
      widget.onLoggedIn();
    } catch (e) {
      if (mounted) setState(() => _error = friendlyError(e));
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: SafeArea(
        child: Center(
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 420),
            child: ListView(
              padding: const EdgeInsets.all(24),
              shrinkWrap: true,
              children: [
                Text('Nhìn Hình Đoán Chuyện',
                    style: Theme.of(context).textTheme.headlineSmall,
                    textAlign: TextAlign.center),
                const SizedBox(height: 4),
                Text('Ứng dụng của chủ kênh',
                    textAlign: TextAlign.center, style: Theme.of(context).textTheme.bodyMedium),
                const SizedBox(height: 24),
                TextField(
                  controller: _server,
                  keyboardType: TextInputType.url,
                  decoration: const InputDecoration(
                      labelText: 'Địa chỉ máy chủ', helperText: 'Ví dụ: http://100.x.y.z:8000 (Tailscale)'),
                ),
                const SizedBox(height: 12),
                TextField(
                    controller: _user,
                    decoration: const InputDecoration(labelText: 'Tên đăng nhập')),
                const SizedBox(height: 12),
                TextField(
                  controller: _password,
                  obscureText: true,
                  decoration: const InputDecoration(labelText: 'Mật khẩu'),
                  onSubmitted: (_) => _busy ? null : _submit(),
                ),
                if (_error != null) ...[
                  const SizedBox(height: 12),
                  Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
                ],
                const SizedBox(height: 20),
                FilledButton(
                  onPressed: _busy ? null : _submit,
                  child: _busy
                      ? const SizedBox(
                          height: 20, width: 20, child: CircularProgressIndicator(strokeWidth: 2))
                      : const Text('Đăng nhập'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  @override
  void dispose() {
    _server.dispose();
    _user.dispose();
    _password.dispose();
    super.dispose();
  }
}

import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'api/api_client.dart';
import 'api/token_store.dart';
import 'screens/home_screen.dart';
import 'screens/login_screen.dart';

void main() {
  runApp(NhdApp(client: ApiClient(store: SecureTokenStore())));
}

class NhdApp extends StatefulWidget {
  const NhdApp({super.key, required this.client});

  final ApiClient client;

  @override
  State<NhdApp> createState() => _NhdAppState();
}

class _NhdAppState extends State<NhdApp> {
  late Future<bool> _restored;
  bool _loggedIn = false;

  @override
  void initState() {
    super.initState();
    widget.client.onLoggedOut = () => _setLoggedIn(false);
    _restored = widget.client.restore().then((ok) => _loggedIn = ok);
  }

  void _setLoggedIn(bool value) {
    if (mounted) setState(() => _loggedIn = value);
  }

  @override
  Widget build(BuildContext context) {
    return Provider<ApiClient>.value(
      value: widget.client,
      child: MaterialApp(
        title: 'Nhìn Hình Đoán Chuyện',
        debugShowCheckedModeBanner: false,
        theme: ThemeData(colorSchemeSeed: const Color(0xFF3B6BFF), useMaterial3: true),
        darkTheme: ThemeData(
            colorSchemeSeed: const Color(0xFF3B6BFF),
            brightness: Brightness.dark,
            useMaterial3: true),
        home: FutureBuilder<bool>(
          future: _restored,
          builder: (context, snap) {
            if (snap.connectionState != ConnectionState.done) {
              return const Scaffold(body: Center(child: CircularProgressIndicator()));
            }
            return _loggedIn
                ? HomeScreen(onLoggedOut: () => _setLoggedIn(false))
                : LoginScreen(onLoggedIn: () => _setLoggedIn(true));
          },
        ),
      ),
    );
  }
}

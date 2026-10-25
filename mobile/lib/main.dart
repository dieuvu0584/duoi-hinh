import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'api/api_client.dart';
import 'api/token_store.dart';
import 'image_chooser.dart';
import 'media_saver.dart';
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
    // A session that was revoked or expired signs in again by itself when the owner chose to
    // remember the login; otherwise the login page appears.
    widget.client.onLoggedOut = () async {
      _setLoggedIn(await widget.client.loginWithSaved());
    };
    _restored = widget.client.restore().then((ok) async {
      if (!ok) ok = await widget.client.loginWithSaved();
      return _loggedIn = ok;
    });
  }

  void _setLoggedIn(bool value) {
    if (mounted) setState(() => _loggedIn = value);
  }

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        Provider<ApiClient>.value(value: widget.client),
        Provider<MediaSaver>.value(value: const GalleryMediaSaver()),
        Provider<ImageChooser>.value(value: const GalleryImageChooser()),
      ],
      child: MaterialApp(
        title: 'Memes On Air',
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

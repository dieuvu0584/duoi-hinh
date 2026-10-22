import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../api/api_client.dart';
import 'ideas_screen.dart';
import 'queue_screen.dart';
import 'settings_screen.dart';
import 'status_screen.dart';

class HomeScreen extends StatefulWidget {
  const HomeScreen({super.key, required this.onLoggedOut});

  final VoidCallback onLoggedOut;

  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> {
  int _index = 0;

  static const _titles = ['Bài viết', 'Ý tưởng', 'Hệ thống', 'Cài đặt'];

  Future<void> _logout() async {
    await context.read<ApiClient>().logout();
    widget.onLoggedOut();
  }

  @override
  Widget build(BuildContext context) {
    final pages = <Widget>[
      const QueueScreen(),
      const IdeasScreen(),
      const StatusScreen(),
      const SettingsScreen(),
    ];
    return Scaffold(
      appBar: AppBar(
        title: Text(_titles[_index]),
        actions: [
          IconButton(
              tooltip: 'Đăng xuất', onPressed: _logout, icon: const Icon(Icons.logout_outlined)),
        ],
      ),
      body: IndexedStack(index: _index, children: pages),
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (i) => setState(() => _index = i),
        destinations: const [
          NavigationDestination(icon: Icon(Icons.inbox_outlined), label: 'Bài viết'),
          NavigationDestination(icon: Icon(Icons.lightbulb_outline), label: 'Ý tưởng'),
          NavigationDestination(icon: Icon(Icons.monitor_heart_outlined), label: 'Hệ thống'),
          NavigationDestination(icon: Icon(Icons.settings_outlined), label: 'Cài đặt'),
        ],
      ),
    );
  }
}

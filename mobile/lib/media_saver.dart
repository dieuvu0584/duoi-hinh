import 'dart:io';

import 'package:gal/gal.dart';
import 'package:path_provider/path_provider.dart';

import 'api/api_client.dart';

/// Saves a rendered video from the server into the phone's gallery. The video is downloaded with
/// the owner's token (never a public link) to a temporary file, copied into the gallery, and the
/// temporary file is deleted.
abstract class MediaSaver {
  Future<void> saveVideo({
    required ApiClient api,
    required String path,
    required String name,
    void Function(double progress)? onProgress,
  });
}

class GalleryMediaSaver implements MediaSaver {
  const GalleryMediaSaver();

  @override
  Future<void> saveVideo({
    required ApiClient api,
    required String path,
    required String name,
    void Function(double progress)? onProgress,
  }) async {
    if (!await Gal.hasAccess(toAlbum: true)) {
      if (!await Gal.requestAccess(toAlbum: true)) {
        throw ApiException(0, 'gallery_denied', 'Chưa được cấp quyền lưu vào thư viện ảnh.');
      }
    }
    final dir = await getTemporaryDirectory();
    final file = File('${dir.path}/$name');
    try {
      await api.downloadMedia(path, file.path, onProgress: onProgress);
      await Gal.putVideo(file.path, album: 'Memes On Air');
    } finally {
      if (await file.exists()) await file.delete();
    }
  }
}

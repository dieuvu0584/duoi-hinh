import 'dart:typed_data';

import 'package:image_picker/image_picker.dart';

/// Lets the owner pick a picture from the phone's gallery. It is an interface so tests can
/// supply a picture without opening the system picker.
abstract class ImageChooser {
  /// Returns the picture's bytes, or null when the owner cancels.
  Future<Uint8List?> pick();
}

class GalleryImageChooser implements ImageChooser {
  const GalleryImageChooser();

  @override
  Future<Uint8List?> pick() async {
    // The system photo picker needs no storage permission. The picture is shrunk before it is
    // sent so the upload stays small; the server limits and re-checks it anyway.
    final file = await ImagePicker().pickImage(
      source: ImageSource.gallery,
      maxWidth: 2000,
      maxHeight: 2000,
      imageQuality: 85,
    );
    return file?.readAsBytes();
  }
}

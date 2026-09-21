import tempfile
import unittest
from pathlib import Path

from astrbot_plugin_feeding.local_paths import local_image_path


class LocalPathTests(unittest.TestCase):
    def test_native_absolute_path_and_encoded_file_uri(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '食物 图片 #1.png'
            path.write_bytes(b'test')
            self.assertEqual(local_image_path(str(path)), path.resolve())
            self.assertEqual(local_image_path(path.as_uri()), path.resolve())
            uri = path.as_uri().replace('file:///', 'file://localhost/', 1)
            self.assertEqual(local_image_path(uri), path.resolve())

    def test_network_and_relative_paths_are_rejected(self):
        for source in ('file://server/share/photo.png', '\\\\server\\share\\photo.png',
                       '//server/share/photo.png', 'relative/photo.png',
                       'file:///photo.png?secret=1', 'file:///photo.png#fragment'):
            with self.subTest(source=source), self.assertRaises(ValueError):
                local_image_path(source)

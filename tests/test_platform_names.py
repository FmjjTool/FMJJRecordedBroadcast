import unittest

from toolbox import platform_names as names


class PlatformNamesTests(unittest.TestCase):
    def test_shared_maps(self):
        self.assertEqual(names.MEDIA_PLATFORM_NAMES["bilibili"], "B站")
        self.assertEqual(names.MUSIC_PLATFORM_NAMES["netease"], "网易云")
        self.assertEqual(names.DIRECTORY_PLATFORM_NAMES["haokan"], "好看视频")
        self.assertEqual(names.ALL_PLATFORM_NAMES["kugou"], "酷狗")

    def test_account_modules_use_shared_maps(self):
        from tracker import accounts as media
        from music_account import accounts as music
        from toolbox import utils
        self.assertEqual(media.PLATFORM_NAMES, names.MEDIA_PLATFORM_NAMES)
        self.assertEqual(music.PLATFORM_NAMES, names.MUSIC_PLATFORM_NAMES)
        self.assertEqual(utils.PLATFORM_CN, names.DIRECTORY_PLATFORM_NAMES)


if __name__ == "__main__":
    unittest.main()

import unittest
from types import SimpleNamespace
from astrbot_plugin_feeding.guest_commands import GuestShortcutFilter, parse_shortcut


class ShortcutTests(unittest.TestCase):
    def test_dynamic_names_and_notes(self):
        self.assertEqual(parse_shortcut('投喂千小妹'),('千小妹',''))
        self.assertEqual(parse_shortcut(' 投喂角色B  这份给你 '),('角色B','这份给你'))

    def test_existing_commands_do_not_double_match(self):
        for text in ('投喂','投喂 今天吃饭','投喂角色 千小妹','投喂状态','投喂大肥鱼','普通聊天'):
            self.assertIsNone(parse_shortcut(text))

    def test_wake_required_like_native_commands(self):
        event=SimpleNamespace(is_at_or_wake_command=False,get_message_str=lambda:'投喂千小妹')
        f=GuestShortcutFilter()
        self.assertFalse(f.filter(event,{}))
        event.is_at_or_wake_command=True
        self.assertTrue(f.filter(event,{}))

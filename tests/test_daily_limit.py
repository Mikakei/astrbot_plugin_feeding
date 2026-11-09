import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from astrbot_plugin_feeding.core import FeedingError, Journal


class DailyLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'tasks.json'
        self.journal = Journal(self.path)

    def admit(self, mid, **kwargs):
        return self.journal.admit(mid, mid, 0, 100, **kwargs)

    def test_groups_and_admin_are_independent(self):
        for i in range(30):
            self.admit(str(i), group='bot:A')
        with self.assertRaises(FeedingError):
            self.admit('overflow', group='bot:A')
        self.admit('other', group='bot:B')
        self.admit('other-bot', group='another:A')
        self.admit('admin', group='bot:A', admin=True)
        self.admit('private')
        self.admit('admin-first', group='bot:C', admin=True)
        self.admit('normal', group='bot:C', daily_limit=1)

    def test_restart_failure_and_duplicate_do_not_refund_or_double_count(self):
        mid = self.admit('first', group='A', daily_limit=1)
        self.journal.finish(mid, 'failed')
        self.journal = Journal(self.path)
        self.assertEqual(self.admit('first', group='A', daily_limit=1), 'duplicate')
        with self.assertRaises(FeedingError):
            self.admit('second', group='A', daily_limit=1)

    def test_beijing_midnight_resets(self):
        before = datetime(2026, 11, 9, 15, 59, 59, tzinfo=timezone.utc).timestamp()
        with patch('astrbot_plugin_feeding.core.time.time', return_value=before):
            self.admit('first', group='A', daily_limit=1)
            with self.assertRaises(FeedingError):
                self.admit('second', group='A', daily_limit=1)
        with patch('astrbot_plugin_feeding.core.time.time', return_value=before + 1):
            self.admit('next-day', group='A', daily_limit=1)

    def test_disabled_limit_and_config_change(self):
        self.admit('first', group='A', daily_limit=0)
        self.admit('second', group='A', daily_limit=0)
        with self.assertRaises(FeedingError):
            self.admit('third', group='A', daily_limit=2)
        self.admit('third', group='A', daily_limit=3)

    def test_queue_rejection_does_not_consume_quota(self):
        self.admit('first', group='A')
        with self.assertRaises(FeedingError):
            self.journal.admit('second', 'second', 0, 1, group='B', daily_limit=1)
        self.admit('second', group='B', daily_limit=1)

    def test_user_quota_is_per_group_and_persistent(self):
        for i in range(6):
            mid=self.journal.admit(str(i),'user',0,100,group='A')
            self.journal.finish(mid,'done')
        self.journal=Journal(self.path)
        with self.assertRaisesRegex(FeedingError,'明天再来'):
            self.journal.admit('seventh','user',0,100,group='A')
        self.journal.admit('other-user','other',0,100,group='A')
        mid=self.journal.admit('other-group','user',0,100,group='B')
        self.journal.finish(mid,'done')
        self.journal.admit('admin','user',0,100,group='A',admin=True)

    def test_user_quota_midnight_and_rounding(self):
        before=datetime(2026,11,9,15,59,59,tzinfo=timezone.utc).timestamp()
        with patch('astrbot_plugin_feeding.core.time.time',return_value=before):
            for i in range(6):
                mid=self.journal.admit(str(i),'user',0,100,group='A',daily_limit=34)
                self.journal.finish(mid,'failed')
            with self.assertRaises(FeedingError):
                self.journal.admit('seventh','user',0,100,group='A',daily_limit=34)
        with patch('astrbot_plugin_feeding.core.time.time',return_value=before+1):
            self.journal.admit('tomorrow','user',0,100,group='A',daily_limit=34)

    def test_small_group_unlimited_and_private(self):
        mid=self.journal.admit('small','user',0,100,group='small',daily_limit=3)
        self.journal.finish(mid,'done')
        with self.assertRaises(FeedingError):
            self.journal.admit('small-again','user',0,100,group='small',daily_limit=3)
        for group in ('unlimited',''):
            for i in range(8):
                mid=self.journal.admit(group+str(i),'user',0,100,group=group,daily_limit=0 if group else 30)
                self.journal.finish(mid,'done')

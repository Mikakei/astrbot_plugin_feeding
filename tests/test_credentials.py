"""Credential regression tests, runnable without an AstrBot installation."""
import unittest
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from astrbot_plugin_feeding.connections import resolve_image_credentials, migrate_connection_config


class CredentialTests(unittest.TestCase):
    def setUp(self):
        self.provider = SimpleNamespace(
            provider_config={'api_base': 'https://provider.example/v1/'},
            get_keys=lambda: ['provider-test-key'],
        )
        self.lookup = Mock(return_value=self.provider)

    def test_incomplete_custom_connection_never_reads_provider(self):
        for fields in ({'image_base_url': 'https://custom.example/v1'},
                       {'image_api_key': 'custom-test-key'}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                resolve_image_credentials({'image_provider_id': 'saved', 'advanced': fields}, self.lookup)
        self.lookup.assert_not_called()

    def test_complete_custom_connection_is_independent(self):
        result = resolve_image_credentials({
            'advanced': {'image_base_url': ' https://custom.example/v1/ ',
                         'image_api_key': ' custom-test-key '}, 'image_provider_id': 'saved',
        }, self.lookup)
        self.assertEqual(result, ('https://custom.example/v1', 'custom-test-key'))
        self.lookup.assert_not_called()

    def test_selected_provider_and_updates_are_read_as_a_pair(self):
        config = {'image_provider_id': 'saved'}
        self.assertEqual(resolve_image_credentials(config, self.lookup),
                         ('https://provider.example/v1', 'provider-test-key'))
        self.lookup.assert_called_with('saved')
        self.provider.provider_config['api_base'] = 'https://updated.example/v1'
        self.provider.get_keys = lambda: 'updated-test-key'
        self.assertEqual(resolve_image_credentials(config, self.lookup),
                         ('https://updated.example/v1', 'updated-test-key'))

    def test_missing_selection_does_not_pick_an_arbitrary_provider(self):
        with self.assertRaises(ValueError):
            resolve_image_credentials({}, self.lookup)
        self.lookup.assert_not_called()

    def test_deleted_or_incomplete_provider_is_rejected(self):
        for provider in (None, SimpleNamespace(provider_config={}, get_keys=lambda: ['key']),
                         SimpleNamespace(provider_config={'api_base': 'https://example.com'},
                                         get_keys=lambda: [None, '', '  '])):
            with self.subTest(provider=provider), self.assertRaises(ValueError):
                resolve_image_credentials({'image_provider_id': 'saved'}, lambda _: provider)

    def test_schema_uses_native_provider_selectors_and_portable_defaults(self):
        schema = json.loads((Path(__file__).parents[1] / '_conf_schema.json').read_text(encoding='utf-8'))
        for field in ('image_provider_id', 'vision_provider_id'):
            self.assertEqual(schema[field]['_special'], 'select_provider')
            self.assertEqual(schema[field]['default'], '')
        self.assertEqual(schema['advanced']['type'], 'object')
        for field in ('image_base_url', 'image_api_key'):
            self.assertIn(field, schema['advanced']['items'])
            self.assertTrue(schema[field]['invisible'])

    def test_legacy_connection_migrates_once_and_can_be_cleared(self):
        config = {'image_base_url': 'https://legacy.example/v1',
                  'image_api_key': 'legacy-test-key', 'image_provider_id': 'saved',
                  'advanced': {'image_base_url': '', 'image_api_key': ''}}
        self.assertTrue(migrate_connection_config(config))
        self.assertEqual(resolve_image_credentials(config, self.lookup),
                         ('https://legacy.example/v1', 'legacy-test-key'))
        self.assertEqual(config['image_api_key'], '')
        self.assertFalse(migrate_connection_config(config))
        config['advanced'] = {}
        self.assertFalse(migrate_connection_config(config))
        self.assertEqual(resolve_image_credentials(config, self.lookup),
                         ('https://provider.example/v1', 'provider-test-key'))

    def test_migration_never_merges_partial_new_and_old_connections(self):
        config = {'image_base_url': 'https://legacy.example/v1',
                  'image_api_key': 'legacy-test-key',
                  'advanced': {'image_base_url': 'https://new.example/v1'}}
        self.assertTrue(migrate_connection_config(config))
        with self.assertRaises(ValueError):
            resolve_image_credentials(config, self.lookup)
        self.lookup.assert_not_called()

    def test_partial_legacy_connection_stays_invalid_after_migration(self):
        config = {'image_base_url': 'https://legacy.example/v1'}
        self.assertTrue(migrate_connection_config(config))
        with self.assertRaises(ValueError):
            resolve_image_credentials(config, self.lookup)
        self.lookup.assert_not_called()


if __name__ == '__main__':
    unittest.main()

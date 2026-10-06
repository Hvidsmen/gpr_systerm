import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.runtime import env_bool, get_secret_key


class RuntimeConfigTests(SimpleTestCase):
    def test_development_secret_is_stable_across_restarts(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            key = get_secret_key(directory, debug=True)
            self.assertGreaterEqual(len(key), 50)
            self.assertEqual(get_secret_key(directory, debug=True), key)
            self.assertTrue((Path(directory) / '.dev-secret-key').exists())

    def test_production_requires_an_external_secret(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ImproperlyConfigured):
                get_secret_key(directory, debug=False)
            self.assertFalse((Path(directory) / '.dev-secret-key').exists())

    def test_production_rejects_weak_and_old_development_keys(self):
        for key in ('short', 'django-insecure-' + 'x' * 60):
            with self.subTest(key_kind='short' if len(key) < 50 else 'insecure'), patch.dict(os.environ, {'DJANGO_SECRET_KEY': key}, clear=True):
                with self.assertRaises(ImproperlyConfigured):
                    get_secret_key('.', debug=False)

    def test_external_secret_takes_precedence(self):
        with patch.dict(os.environ, {'DJANGO_SECRET_KEY': 'test-configuration-key-' * 4}, clear=True):
            self.assertEqual(get_secret_key('.', debug=False), os.environ['DJANGO_SECRET_KEY'])

    def test_boolean_settings_are_explicit(self):
        for value, expected in [('1', True), ('true', True), ('0', False), ('false', False)]:
            with patch.dict(os.environ, {'TEST_FLAG': value}):
                self.assertEqual(env_bool('TEST_FLAG'), expected)
        with patch.dict(os.environ, {'TEST_FLAG': 'invalid'}):
            with self.assertRaises(ImproperlyConfigured):
                env_bool('TEST_FLAG')

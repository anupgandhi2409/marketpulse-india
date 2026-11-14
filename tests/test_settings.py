import os
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

from marketpulse.config.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_default_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()

        self.assertEqual(settings.environment, "development")

    def test_default_environment_isolated_from_system_environment(self):
        with patch.dict(os.environ, {"MARKETPULSE_ENV": "production"}):
            with patch.dict(os.environ, {}, clear=True):
                settings = Settings()

        self.assertEqual(settings.environment, "development")

    def test_environment_override(self):
        with patch.dict(os.environ, {"MARKETPULSE_ENV": "testing"}):
            settings = Settings()

        self.assertEqual(settings.environment, "testing")

    def test_kotak_consumer_key_defaults_to_none(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()

        self.assertIsNone(settings.kotak_consumer_key)

    def test_kotak_environment_defaults_to_prod(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()

        self.assertEqual(settings.kotak_environment, "prod")

    def test_kotak_consumer_key_environment_override(self):
        with patch.dict(os.environ, {"KOTAK_CONSUMER_KEY": "local-test-key"}, clear=True):
            settings = Settings()

        self.assertEqual(settings.kotak_consumer_key, "local-test-key")

    def test_kotak_environment_override(self):
        with patch.dict(os.environ, {"KOTAK_ENVIRONMENT": "uat"}, clear=True):
            settings = Settings()

        self.assertEqual(settings.kotak_environment, "uat")

    def test_project_root_is_repository_root(self):
        settings = Settings()
        expected_root = Path(__file__).resolve().parents[1]

        self.assertEqual(settings.project_root, expected_root)

    def test_data_directory_is_under_project_root(self):
        settings = Settings()

        self.assertEqual(settings.data_dir, settings.project_root / "data")

    def test_settings_are_frozen(self):
        settings = Settings()

        with self.assertRaises(FrozenInstanceError):
            settings.app_name = "Changed"


if __name__ == "__main__":
    unittest.main()

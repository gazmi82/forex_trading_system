from __future__ import annotations

import unittest
from pathlib import Path

from app.core import (
    BASE_DIR as PackagedBaseDirFromInit,
    CHROMA_DIR as PackagedChromaDirFromInit,
    LOGS_DIR as PackagedLogsDirFromInit,
    validate_config as PackagedValidateConfigFromInit,
)
from app.core.config import BASE_DIR, CHROMA_DIR, LOGS_DIR, validate_config
from config import (
    BASE_DIR as RootBaseDir,
    CHROMA_DIR as RootChromaDir,
    LOGS_DIR as RootLogsDir,
    validate_config as RootValidateConfig,
)


class ConfigImportTests(unittest.TestCase):
    def test_root_config_reexports_packaged_config(self):
        self.assertIs(RootValidateConfig, validate_config)
        self.assertIs(PackagedValidateConfigFromInit, validate_config)
        self.assertEqual(RootBaseDir, BASE_DIR)
        self.assertEqual(PackagedBaseDirFromInit, BASE_DIR)
        self.assertEqual(RootChromaDir, CHROMA_DIR)
        self.assertEqual(PackagedChromaDirFromInit, CHROMA_DIR)
        self.assertEqual(RootLogsDir, LOGS_DIR)
        self.assertEqual(PackagedLogsDirFromInit, LOGS_DIR)
        self.assertEqual(BASE_DIR, Path(__file__).resolve().parents[1])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from app.cli import (
    main as packaged_main_from_init,
    run_demo_loop as packaged_run_demo_loop_from_init,
    setup_oanda as packaged_setup_oanda_from_init,
)
from app.cli.main import main, run_demo_loop, setup_oanda
from main import (
    main as root_main,
    run_demo_loop as root_run_demo_loop,
    setup_oanda as root_setup_oanda,
)


class MainEntrypointImportTests(unittest.TestCase):
    def test_root_main_reexports_packaged_cli_helpers(self):
        self.assertIs(root_main, main)
        self.assertIs(packaged_main_from_init, main)
        self.assertIs(root_run_demo_loop, run_demo_loop)
        self.assertIs(packaged_run_demo_loop_from_init, run_demo_loop)
        self.assertIs(root_setup_oanda, setup_oanda)
        self.assertIs(packaged_setup_oanda_from_init, setup_oanda)


if __name__ == "__main__":
    unittest.main()

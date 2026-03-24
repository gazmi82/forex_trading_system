from __future__ import annotations

import unittest

from app.execution import TradeExecutor as PackagedExecutionTradeExecutor
from app.execution.trade_executor import TradeExecutor


class TradeExecutorImportTests(unittest.TestCase):
    def test_app_execution_init_reexports_executor(self):
        self.assertIs(PackagedExecutionTradeExecutor, TradeExecutor)


if __name__ == "__main__":
    unittest.main()

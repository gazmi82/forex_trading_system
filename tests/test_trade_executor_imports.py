from __future__ import annotations

import unittest

from app.execution import TradeExecutor as PackagedExecutionTradeExecutor
from app.execution.trade_executor import TradeExecutor
from trade_executor import TradeExecutor as RootTradeExecutor


class TradeExecutorImportTests(unittest.TestCase):
    def test_root_trade_executor_reexports_packaged_executor(self):
        self.assertIs(RootTradeExecutor, TradeExecutor)
        self.assertIs(PackagedExecutionTradeExecutor, TradeExecutor)


if __name__ == "__main__":
    unittest.main()

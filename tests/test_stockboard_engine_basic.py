import unittest

import stockboard_engine as engine


class StockboardEngineBasicTest(unittest.TestCase):
    def test_stock_code_normalizes_kiwoom_prefix_and_market_suffix(self):
        cases = {
            "A005930": "005930",
            "005930_AL": "005930",
            "005930_NX": "005930",
            " 005930 ": "005930",
            "5930": "005930",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(engine._stock_code(raw), expected)

    def test_stock_code_rejects_empty_or_invalid_values(self):
        for raw in (None, "", "ABC", "005930AL", "005930-KRX"):
            with self.subTest(raw=raw):
                self.assertIsNone(engine._stock_code(raw))

    def test_trade_value_eok_converts_million_krw_to_eok(self):
        cases = {
            "1,200": 12,
            "+150": 1.5,
            0: 0,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(engine._trade_value_eok(raw), expected)

    def test_trade_value_eok_returns_none_for_missing_or_invalid_values(self):
        for raw in (None, "", "not-a-number"):
            with self.subTest(raw=raw):
                self.assertIsNone(engine._trade_value_eok(raw))

    def test_normalize_kiwoom_price_uses_absolute_numeric_price(self):
        cases = {
            "-328,500": 328500,
            "+328500": 328500,
            "328500": 328500,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(engine.normalize_kiwoom_price(raw), expected)


if __name__ == "__main__":
    unittest.main()

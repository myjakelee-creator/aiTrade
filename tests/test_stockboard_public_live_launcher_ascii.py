from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "stockboard_public_live_v2.ps1"


class PublicLiveLauncherAsciiTests(unittest.TestCase):
    def test_windows_powershell_launcher_is_ascii_only(self):
        raw = SCRIPT.read_bytes()
        self.assertTrue(raw)
        self.assertTrue(all(byte < 128 for byte in raw))

    def test_launcher_uses_ascii_ui_contract_markers(self):
        text = SCRIPT.read_text(encoding="ascii")
        self.assertIn("stockboard_public_live_launcher_ascii_v3_20260722", text)
        self.assertIn("trade_value_1m_eok", text)
        self.assertIn("strength_5m", text)
        self.assertNotIn("1\ubd84\ub300\uae08", text)
        self.assertNotIn("5\ubd84\uac15\ub3c4", text)


if __name__ == "__main__":
    unittest.main()

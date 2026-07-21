from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CMD = ROOT / "stockboard_public.cmd"
SCRIPT = ROOT / "scripts" / "stockboard_public_live_v2.ps1"


class PublicLiveLauncherV2Tests(unittest.TestCase):
    def test_cmd_keeps_execution_in_current_console(self):
        text = CMD.read_text(encoding="utf-8-sig")
        self.assertIn("stockboard_public_live_v2.ps1", text)
        self.assertNotIn("Start-Process -FilePath '%~f0'", text)
        self.assertIn("The detailed error above is intentionally kept visible", text)

    def test_script_uses_only_fresh_port_for_local_restart(self):
        text = SCRIPT.read_text(encoding="utf-8-sig")
        self.assertIn("$GatewayPort = 8767", text)
        self.assertIn("function Stop-LiveGateway", text)
        self.assertNotIn("$LegacyGatewayPort", text)
        self.assertIn("PUBLIC_ERROR_FILE=", text)
        self.assertIn("PUBLIC_GATEWAY_PY_COMPILE=True", text)


if __name__ == "__main__":
    unittest.main()

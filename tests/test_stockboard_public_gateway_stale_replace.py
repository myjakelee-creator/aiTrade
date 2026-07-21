from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "realtime_v2" / "public_gateway.py"
LAUNCHER = ROOT / "scripts" / "stockboard_public_gateway_current_ui.ps1"
CMD = ROOT / "stockboard_public.cmd"


class StockBoardPublicGatewayStaleReplaceTests(unittest.TestCase):
    def test_gateway_declares_current_ui_version_and_live_html_source(self):
        text = GATEWAY.read_text(encoding="utf-8-sig")
        self.assertIn("stockboard_public_current_ui_v2_20260721", text)
        self.assertIn("live_private_worker_html", text)
        self.assertIn('"1분대금"', text)
        self.assertIn('"5분강도"', text)
        self.assertIn("STOCKBOARD_PUBLIC_CURRENT_UI_V2_20260721", text)

    def test_launcher_replaces_stale_process_before_start_and_publish(self):
        text = LAUNCHER.read_text(encoding="utf-8-sig")
        self.assertIn("Stop-StalePublicGateway", text)
        self.assertIn("Start-CurrentUiGateway", text)
        self.assertIn("Assert-CurrentUiGateway", text)
        self.assertIn('"publish" {\n            Start-CurrentUiGateway', text)
        self.assertIn("PUBLIC_UI_CURRENT_SYNC=True", text)

    def test_cmd_routes_to_current_ui_guard(self):
        text = CMD.read_text(encoding="utf-8-sig")
        self.assertIn("stockboard_public_gateway_current_ui.ps1", text)
        self.assertIn("Publish current UI", text)


if __name__ == "__main__":
    unittest.main()

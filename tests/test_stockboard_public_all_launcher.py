from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CMD_PATH = ROOT / "stockboard_public.cmd"
ALL_PS1_PATH = ROOT / "scripts" / "stockboard_public_all.ps1"
PUBLIC_PS1_PATH = ROOT / "scripts" / "stockboard_public_live_v2.ps1"


class StockBoardPublicAllLauncherTests(unittest.TestCase):
    def test_visible_launcher_routes_reboot_start_to_all_orchestrator(self):
        cmd = CMD_PATH.read_text(encoding="utf-8-sig")
        self.assertIn("Start everything and publish - recommended after reboot", cmd)
        self.assertIn('set "ACTION=all-start"', cmd)
        self.assertIn('set "ALL_ACTION=start"', cmd)
        self.assertIn("scripts\\stockboard_public_all.ps1", cmd)

    def test_all_orchestrator_preserves_required_start_order(self):
        script = ALL_PS1_PATH.read_text(encoding="ascii")
        body = re.search(
            r"function Start-All \{(?P<body>.*?)\n\}",
            script,
            flags=re.DOTALL,
        )
        self.assertIsNotNone(body)
        text = body.group("body")
        tailscale = text.index("Ensure-TailscaleRunning")
        private = text.index("Start-PrivateWorker")
        public = text.index("Publish-PublicGateway")
        self.assertLess(tailscale, private)
        self.assertLess(private, public)
        self.assertIn("127\\.0\\.0\\.1:8767", text)
        self.assertIn("ALL_PUBLIC_READY=True", text)
        self.assertIn("Start-Process $url", text)

    def test_private_start_is_async_visible_and_reports_progress(self):
        script = ALL_PS1_PATH.read_text(encoding="ascii")
        self.assertIn("Get-PrivateReadiness", script)
        self.assertIn("Test-PrivateReady", script)
        self.assertIn("PRIVATE_WORKER_ALREADY_RUNNING=True", script)
        self.assertIn("PRIVATE_START_WAIT", script)
        self.assertIn("Check Alt+Tab for the Kiwoom login window", script)
        self.assertIn("Start-Process", script)
        self.assertIn("-FilePath $env:ComSpec", script)
        self.assertIn("-NoNewWindow", script)
        self.assertIn("-PassThru", script)
        self.assertIn("PRIVATE_LAUNCH_PROCESS_PID", script)
        self.assertIn("PRIVATE_LAUNCH_EXIT_CODE", script)
        self.assertIn("stockboard_v2_large.cmd start-fast", script)
        self.assertIn("LoginState -eq \"connected\"", script)
        self.assertIn("RealRegSucceeded", script)
        self.assertIn("RegisteredCount -gt 0", script)
        self.assertNotIn("function Invoke-Cmd", script)

    def test_publish_is_noninteractive_and_checks_current_cleanup_contract(self):
        script = ALL_PS1_PATH.read_text(encoding="ascii")
        self.assertIn('$env:STOCKBOARD_PUBLIC_CONFIRM = "PUBLIC"', script)
        self.assertIn(
            "stockboard_public_chrome_cleanup_v3_20260722",
            script,
        )
        self.assertIn("PUBLIC_GATEWAY_READY=True", script)

    def test_all_launcher_is_ascii_only_for_windows_powershell_51(self):
        raw = ALL_PS1_PATH.read_bytes()
        self.assertTrue(raw)
        self.assertTrue(all(byte < 128 for byte in raw))

    def test_powershell_51_variable_before_colon_is_delimited(self):
        script = ALL_PS1_PATH.read_text(encoding="ascii")
        self.assertNotIn("$code:", script)
        self.assertIn("stockboard_public_all_v3_20260722", script)

    def test_public_launcher_opens_canonical_root_url(self):
        script = PUBLIC_PS1_PATH.read_text(encoding="ascii")
        self.assertIn("Start-Process $url | Out-Null", script)
        self.assertNotIn('Start-Process "$url/?v=', script)


if __name__ == "__main__":
    unittest.main()

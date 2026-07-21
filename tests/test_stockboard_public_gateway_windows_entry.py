from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / "scripts" / "stockboard_public_gateway_entry.ps1"
CMD = ROOT / "stockboard_public.cmd"


class StockBoardPublicGatewayWindowsEntryTests(unittest.TestCase):
    def test_entry_replaces_empty_listener_generic_list_path(self):
        text = ENTRY.read_text(encoding="utf-8-sig")
        self.assertIn("function Get-NetTCPConnection", text)
        self.assertIn("netstat -ano -p tcp", text)
        self.assertNotIn("Generic.List[object]", text.replace('empty Generic.List[object]', ''))

    def test_entry_reports_script_line_and_stack(self):
        text = ENTRY.read_text(encoding="utf-8-sig")
        self.assertIn("ERROR_LINE=", text)
        self.assertIn("ERROR_STACK=", text)

    def test_cmd_routes_through_windows_entry(self):
        text = CMD.read_text(encoding="utf-8-sig")
        self.assertIn("stockboard_public_gateway_entry.ps1", text)
        self.assertNotIn('set "SCRIPT=%~dp0scripts\\stockboard_public_gateway.ps1"', text)


if __name__ == "__main__":
    unittest.main()

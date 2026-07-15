from __future__ import annotations

import ast
from pathlib import Path

from realtime_v2.stockboard_global_sort_patch import MARKER, apply_global_sort


ROOT = Path(__file__).resolve().parents[1]
PATCH_PATH = ROOT / "realtime_v2" / "stockboard_global_sort_patch.py"
INIT_PATH = ROOT / "realtime_v2" / "__init__.py"


def test_global_sort_patch_is_valid_python():
    ast.parse(PATCH_PATH.read_text(encoding="utf-8"))


def test_all_visible_rows_are_sorted_before_the_two_tables_are_split():
    source = (
        "ranked=displayOrderPaused?raw:byCandidateScore(raw),"
        "__sbv2ClientSort=__sbv2ClientSortActive(displayOrderPaused),"
        "focusBase=ranked.slice(0,20),poolBase=ranked.slice(20),"
        "focusRows=__sbv2ClientSort?sortedRows(focusBase):focusBase,"
        "poolRows=__sbv2ClientSort?sortedRows(poolBase):poolBase,"
        "selectedRows=selectedCode?raw.filter(Boolean):[];"
    )

    patched = apply_global_sort(source)

    assert MARKER in patched
    assert "orderedRows=__sbv2ClientSort?sortedRows(ranked):ranked" in patched
    assert "focusRows=orderedRows.slice(0,20)" in patched
    assert "poolRows=orderedRows.slice(20)" in patched
    assert "sortedRows(focusBase)" not in patched
    assert "sortedRows(poolBase)" not in patched


def test_patch_is_idempotent():
    source = (
        "__sbv2ClientSort=__sbv2ClientSortActive(displayOrderPaused),"
        "focusBase=ranked.slice(0,20),poolBase=ranked.slice(20),"
        "focusRows=__sbv2ClientSort?sortedRows(focusBase):focusBase,"
        "poolRows=__sbv2ClientSort?sortedRows(poolBase):poolBase,selectedRows="
    )
    once = apply_global_sort(source)
    twice = apply_global_sort(once)
    assert twice == once
    assert twice.count(MARKER) == 1


def test_runtime_package_installs_the_global_sort_wrapper():
    source = INIT_PATH.read_text(encoding="utf-8")
    assert "install_stockboard_global_sort" in source
    assert "install_stockboard_global_sort()" in source

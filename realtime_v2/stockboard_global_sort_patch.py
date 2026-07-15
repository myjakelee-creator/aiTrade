from __future__ import annotations


MARKER = "STOCKBOARD_V2_GLOBAL_HEADER_SORT_100_20260714"

_SPLIT_SORT = (
    "__sbv2ClientSort=__sbv2ClientSortActive(displayOrderPaused),"
    "focusBase=ranked.slice(0,20),poolBase=ranked.slice(20),"
    "focusRows=__sbv2ClientSort?sortedRows(focusBase):focusBase,"
    "poolRows=__sbv2ClientSort?sortedRows(poolBase):poolBase,"
    "selectedRows="
)

_GLOBAL_SORT = (
    f"/* {MARKER} */"
    "__sbv2ClientSort=__sbv2ClientSortActive(displayOrderPaused),"
    "orderedRows=__sbv2ClientSort?sortedRows(ranked):ranked,"
    "focusRows=orderedRows.slice(0,20),"
    "poolRows=orderedRows.slice(20),"
    "selectedRows="
)


def apply_global_sort(html: str) -> str:
    """Sort the complete visible StockBoard set before splitting the two tables.

    The server candidate order remains the source array. When client header sorting is
    active, all visible rows are sorted once, then rows 1-20 are rendered in the upper
    table and rows 21-100 in the lower table. This keeps ArrowUp/Down navigation in the
    same global order because the DOM tables are traversed upper then lower.
    """

    if MARKER in html:
        return html
    if _SPLIT_SORT not in html:
        return html
    return html.replace(_SPLIT_SORT, _GLOBAL_SORT, 1)


def install() -> None:
    from realtime_v2 import html_header_sort_patch as header_sort

    if getattr(header_sort, "_stockboard_global_header_sort_installed", False):
        return

    original_apply = header_sort.apply_header_sort_patch

    def patched_apply_header_sort(html: str) -> str:
        return apply_global_sort(original_apply(html))

    header_sort.apply_header_sort_patch = patched_apply_header_sort
    header_sort._stockboard_global_header_sort_installed = True

from __future__ import annotations

MARKER = "STOCKBOARD_V2_PORTABLE_REBUILD_STATUS_20260720"


def install() -> None:
    """Show closed-board rebuild progress without adding data requests or timers."""

    from realtime_v2 import worker64_guarded_large as large

    if getattr(large, "_portable_rebuild_status_html_installed", False):
        return
    original_ui_safety_patch = large._ui_safety_patch

    def patched_ui_safety_patch(html: str) -> str:
        patched = original_ui_safety_patch(html)
        if MARKER in patched:
            return patched

        render_anchor = "  function render(payload,mode='stream',opt={}){"
        if render_anchor not in patched:
            # Isolated component tests may provide only candle HTML. This optional
            # status patch must not block unrelated display transformations.
            return patched

        helper = r'''
  function portableBoardEmptyMessage(payload,fallback){
    const status=payload?.status||{};
    const basis=String(status.board_display_basis||'');
    if(!basis.startsWith('blocked_'))return fallback;
    const refresh=String(status.portable_board_refresh_status||'');
    const target=String(status.portable_board_target_trading_date||status.board_expected_trading_date||'');
    const completed=Number(status.portable_board_completed_count||0);
    const requested=Number(status.portable_board_requested_count||status.universe_count||0);
    const progress=requested>0?`${completed}/${requested}`:`${completed}`;
    if(refresh==='building')return `직전 거래일 보드 재구성 중 · ${progress}${target?` · 대상 ${target}`:''}`;
    if(refresh==='retry_wait'){
      const next=String(status.portable_board_next_retry_at||'');
      const error=String(status.portable_board_last_error||'').trim();
      return `직전 거래일 보드 재구성 재시도 대기${target?` · 대상 ${target}`:''}${next?` · 다음 ${next.slice(11,19)}`:''}${error?` · ${error}`:''}`;
    }
    if(refresh==='bootstrap_arguments_unavailable')return `직전 거래일 보드 재구성 준비 중${target?` · 대상 ${target}`:''}`;
    return `직전 거래일 exact 보드 대기 중${target?` · 대상 ${target}`:''}`;
  }
'''
        patched = patched.replace(render_anchor, helper + render_anchor, 1)

        focus_text = "'집중 후보 수신 대기 중입니다.'"
        pool_text = "'Top300 Pool 수신 대기 중입니다.'"
        if focus_text in patched:
            patched = patched.replace(
                focus_text,
                "portableBoardEmptyMessage(payload,'집중 후보 수신 대기 중입니다.')",
                1,
            )
        if pool_text in patched:
            patched = patched.replace(
                pool_text,
                "portableBoardEmptyMessage(payload,'Top300 Pool 수신 대기 중입니다.')",
                1,
            )
        return patched.replace("<script>", f"<script>\n  /* {MARKER} */", 1)

    large._ui_safety_patch = patched_ui_safety_patch
    large._portable_rebuild_status_html_installed = True

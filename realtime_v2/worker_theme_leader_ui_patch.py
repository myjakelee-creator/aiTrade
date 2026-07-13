from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MARKER = "THEMEBOARD_SERVER_LEADER_SCORE_UI_20260713"

_STYLE = r"""
<style>
/* THEMEBOARD_SERVER_LEADER_SCORE_UI_20260713 */
.leader-score{font-weight:900}.leader-warmup{color:#64748b;font-size:10px}
</style>
"""

_SCRIPT = r"""
<script>
/* THEMEBOARD_SERVER_LEADER_SCORE_UI_20260713 */
memberRowHtml=function(member){
  const leaderScore=member.leadership_score_text??'-';
  const leaderBasis=(member.stock_change_momentum_1m_text&&member.stock_change_momentum_1m_text!=='-')
    ?`${member.stock_change_momentum_1m_text}/${member.stock_change_persistence_5m_text||'-'}`
    :'워밍업';
  return `<tr data-code="${esc(member.stock_code)}">
    <td class="center"><span class="role ${roleClass(member.role_class)}">${esc(member.leadership_role)}</span></td>
    <td class="center"><span class="grade ${gradeClass(`grade-${String(member.candidate_grade||'F').toLowerCase()}`)}">${esc(member.candidate_grade)}</span></td>
    <td class="num"><span class="leader-score">${esc(leaderScore)}</span><br><span class="leader-warmup">${esc(leaderBasis)}</span></td>
    <td class="name">${esc(member.stock_name)}</td>
    <td class="num">${esc(member.price_text)}</td>
    <td class="num ${tone(member.change_rate_tone)}">${esc(member.change_rate_text)}</td>
    <td class="num">${esc(member.trade_value_text)}</td>
    <td class="num">${esc(member.trade_value_1m_text)}</td>
    <td class="num">${esc(member.trade_value_5m_text)}</td>
    <td class="num">${esc(member.contribution_text)}</td>
    <td class="num">${esc(member.amount_ratio_text)}</td>
    <td class="num">${esc(member.execution_strength_text)}</td>
    <td class="num">${esc(member.strength_5m_text)}</td>
    <td class="num ${tone(member.program_net_tone)}">${esc(member.program_net_text)}</td>
    <td class="num ${tone(member.large_trade_tone)}">${esc(member.large_trade_text)}</td>
  </tr>`;
};
const __tbDetailHead=document.querySelector('#detailTable thead tr');
if(__tbDetailHead&&__tbDetailHead.children[2]){
  __tbDetailHead.children[2].textContent='주도점수';
  __tbDetailHead.children[2].title='등락률 30 + 1·5분 상승지속 25 + 대금비 20 + 최근대금 15 + 강도·수급 10';
}
</script>
"""


def _patch_html(html: str) -> str:
    if MARKER in html:
        return html
    html = html.replace("</head>", f"{_STYLE}\n</head>", 1)
    html = html.replace("</body>", f"{_SCRIPT}\n</body>", 1)
    return html


def install(base) -> None:
    handler_class = base.WebHandler
    if getattr(handler_class, "_stockboard_theme_leader_ui_installed", False):
        return
    original_do_get = handler_class.do_GET

    def patched_do_get(self) -> None:
        parsed = base.urlparse(self.path)
        if parsed.path not in {"/theme", "/themeboard", "/themeboard.html"}:
            return original_do_get(self)
        path = ROOT / "docs" / "themeboard.html"
        if not path.is_file():
            return original_do_get(self)
        body = _patch_html(path.read_text(encoding="utf-8")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    handler_class.do_GET = patched_do_get
    handler_class._stockboard_theme_leader_ui_installed = True

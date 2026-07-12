from __future__ import annotations

# Keep this module ASCII-only. Korean UI labels are emitted through JavaScript
# Unicode escapes or HTML numeric entities so repository encoding changes cannot
# break Board Platform imports again.

SHELL_CSS = r"""
#bp-shell-nav{display:inline-flex;align-items:center;gap:3px;flex-wrap:wrap}
#bp-shell-nav .bp-tab,#bp-shell-nav .bp-popout{display:inline-flex;align-items:center;justify-content:center;min-height:21px;padding:1px 6px;border:1px solid #9aa8b5;border-radius:3px;background:#edf3f8;color:#111827;text-decoration:none;font:800 11px "Malgun Gothic",Arial,sans-serif;white-space:nowrap}
#bp-shell-nav .bp-tab.active{color:#fff;border-color:#1d4ed8;background:#1d4ed8}
#bp-shell-nav .bp-tab.disabled{color:#94a3b8;background:#f1f5f9;cursor:not-allowed}
#bp-shell-nav .bp-popout{background:#fff;cursor:pointer}
.bp-shell-ready .topbar>.tab{display:none!important}
.bp-shell-ready #topbar.topbar{height:136px!important;min-height:136px!important;max-height:136px!important}
#bp-speed-strip{order:999;display:flex;flex:1 0 100%;width:100%;min-width:0;gap:3px;align-items:center;overflow-x:auto;padding:2px 0 0;border-top:1px solid rgba(100,116,139,.28);scrollbar-width:thin}
#bp-speed-strip .bp-speed{display:inline-flex;align-items:center;gap:3px;min-height:18px;padding:0 4px;border:1px solid #cbd5e1;border-radius:3px;background:#fff;color:#334155;font:700 10px "Malgun Gothic",Arial,sans-serif;white-space:nowrap}
#bp-speed-strip .bp-speed b{font-weight:900}
#bp-speed-strip .good,#bp-speed-strip .green{color:#067a2a;border-color:#86efac;background:#f0fdf4}
#bp-speed-strip .warn,#bp-speed-strip .yellow{color:#92400e;border-color:#facc15;background:#fef9c3}
#bp-speed-strip .bad,#bp-speed-strip .red{color:#b91c1c;border-color:#fca5a5;background:#fef2f2}
#bp-speed-strip .muted{color:#64748b;background:#f8fafc}
.bp-board-hub{max-width:1100px;margin:0 auto;padding:8px}
.bp-board-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px}
.bp-board-card{padding:8px;border:1px solid #cbd5e1;border-radius:5px;background:#fff}
.bp-board-card h2{margin:0 0 4px;font-size:15px}.bp-board-card p{margin:3px 0;color:#475569}
.bp-board-actions{display:flex;gap:4px;margin-top:6px;align-items:center}
.bp-board-actions a,.bp-board-actions button{padding:3px 8px;border:1px solid #94a3b8;border-radius:3px;background:#fff;color:#111827;text-decoration:none;font:700 12px "Malgun Gothic",Arial,sans-serif}
.bp-board-card.disabled{opacity:.6}
@media(max-width:700px){.bp-board-grid{grid-template-columns:1fr}}
"""

SHELL_JS = r"""
(function(){
'use strict';
const path=location.pathname;
const boardId=path==='/theme'||path==='/stockboard_theme_v1.html'?'themeboard':path==='/strategy'?'strategyboard':path==='/boards'?'boards':'stockboard';
let renderSamples=[];
function esc(v){return String(v==null?'':v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function rootTopbar(){return document.querySelector('.topbar')||document.getElementById('topbar');}
function insertNav(bar,nav){
 const title=bar.querySelector('.title');
 if(title&&title.parentNode){
   const parent=title.parentNode;
   if(title.nextSibling)parent.insertBefore(nav,title.nextSibling);else parent.appendChild(nav);
   return;
 }
 if(bar.firstChild)bar.insertBefore(nav,bar.firstChild);else bar.appendChild(nav);
}
function buildShell(){
 const bar=rootTopbar();if(!bar)return;
 document.body.classList.add('bp-shell-ready');
 let nav=document.getElementById('bp-shell-nav');
 if(!nav){
   nav=document.createElement('span');nav.id='bp-shell-nav';
   try{insertNav(bar,nav);}catch(_e){bar.appendChild(nav);}
 }
 let speed=document.getElementById('bp-speed-strip');
 if(!speed){speed=document.createElement('div');speed.id='bp-speed-strip';speed.innerHTML='<span class="bp-speed muted"><b>\uC18D\uB3C4</b> \uC218\uC2E0 \uB300\uAE30</span>';bar.appendChild(speed);}
 fetch('/api/v2/boards',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error(String(r.status));return r.json();}).then(data=>{
   const boards=Array.isArray(data.boards)?data.boards:[];
   nav.innerHTML=boards.map(b=>{
     const active=b.board_id===boardId?' active':'';
     if(!b.enabled)return `<span class="bp-tab disabled" title="${esc(b.description)}">${esc(b.label)} \uC900\uBE44\uC911</span>`;
     return `<a class="bp-tab${active}" href="${esc(b.url)}" title="${esc(b.description)}">${esc(b.label)}</a>`;
   }).join('')+'<button type="button" class="bp-popout" title="\uD604\uC7AC \uBCF4\uB4DC\uB97C \uC0C8 \uCC3D\uC73C\uB85C \uC5F4\uAE30">\uC0C8 \uCC3D</button>';
   const pop=nav.querySelector('.bp-popout');if(pop)pop.addEventListener('click',()=>window.open(location.href,'_blank','noopener'));
 }).catch(()=>{nav.innerHTML='<a class="bp-tab" href="/">StockBoard</a><a class="bp-tab" href="/theme">ThemeBoard</a>';});
}
function hookRender(){
 const fn=window.render;
 if(typeof fn!=='function'||fn.__bpMeasured)return false;
 function wrapped(){const start=performance.now();try{return fn.apply(this,arguments);}finally{const ms=performance.now()-start;renderSamples.push(ms);if(renderSamples.length>40)renderSamples.shift();}}
 wrapped.__bpMeasured=true;window.render=wrapped;return true;
}
function renderClientMetric(){
 if(!renderSamples.length)return {key:'browser_render',label:'\uB80C\uB354',text:'-',tone:'muted'};
 const last=renderSamples[renderSamples.length-1];
 const ordered=renderSamples.slice().sort((a,b)=>a-b);
 const p95=ordered[Math.min(ordered.length-1,Math.floor(ordered.length*.95))];
 const tone=p95<=16?'good':p95<=40?'warn':'bad';
 return {key:'browser_render',label:'\uB80C\uB354',text:`${last.toFixed(1)}ms p95 ${p95.toFixed(1)}`,tone};
}
function drawMetrics(metrics,rtt){
 const el=document.getElementById('bp-speed-strip');if(!el)return;
 const all=[...(Array.isArray(metrics)?metrics:[]),{key:'api_rtt',label:'API',text:`${rtt.toFixed(1)}ms`,tone:rtt<=10?'good':rtt<=50?'warn':'bad'},renderClientMetric()];
 el.innerHTML=all.map(m=>`<span class="bp-speed ${esc(m.tone||'muted')}"><b>${esc(m.label)}</b> ${esc(m.text)}</span>`).join('');
}
async function pollPerformance(){
 const start=performance.now();
 try{const response=await fetch(`/api/v2/boards/performance?board_id=${encodeURIComponent(boardId)}`,{cache:'no-store'});if(!response.ok)throw new Error(String(response.status));const data=await response.json();drawMetrics(data.display_metrics||[],performance.now()-start);}
 catch(_e){const el=document.getElementById('bp-speed-strip');if(el)el.innerHTML='<span class="bp-speed bad"><b>\uC18D\uB3C4</b> API \uC5F0\uACB0 \uC2E4\uD328</span>';}
 setTimeout(pollPerformance,document.hidden?5000:2000);
}
async function loadHub(){
 const grid=document.getElementById('bp-board-grid');if(!grid)return;
 try{
   const data=await fetch('/api/v2/boards',{cache:'no-store'}).then(r=>{if(!r.ok)throw new Error(String(r.status));return r.json();});
   grid.innerHTML=(data.boards||[]).filter(b=>b.board_id!=='boards').map(b=>`<section class="bp-board-card ${b.enabled?'':'disabled'}"><h2>${esc(b.label)}</h2><p>${esc(b.description)}</p><p>\uC0C1\uD0DC <b>${esc(b.state||'-')}</b> &middot; \uC811\uC18D ${esc(b.clients||0)} &middot; cache ${esc(b.cache_version||0)}</p><p>\uAC31\uC2E0 ${esc(b.last_updated_at||'-')}</p><div class="bp-board-actions">${b.enabled?`<a href="${esc(b.url)}">\uC5F4\uAE30</a><button type="button" data-pop="${esc(b.url)}">\uC0C8 \uCC3D</button>`:'<span>\uC900\uBE44\uC911</span>'}</div></section>`).join('');
   grid.querySelectorAll('[data-pop]').forEach(btn=>btn.addEventListener('click',()=>window.open(btn.dataset.pop,'_blank','noopener')));
 }catch(_e){grid.innerHTML='<section class="bp-board-card"><h2>Board Hub \uC624\uB958</h2><p>\uC0C1\uD0DC API\uB97C \uC77D\uC9C0 \uBABB\uD588\uC2B5\uB2C8\uB2E4.</p></section>';}
 setTimeout(loadHub,5000);
}
function start(){buildShell();let tries=0;const timer=setInterval(()=>{tries++;if(hookRender()||tries>20)clearInterval(timer);},250);pollPerformance();loadHub();}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
"""

BOARDS_HTML = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>aiTrade Board Hub</title>
<link rel="stylesheet" href="/api/v2/boards/shell.css">
<style>
html,body{margin:0;background:#f1f5f9;color:#111827;font:12px "Malgun Gothic",Arial,sans-serif}
.topbar{position:sticky;top:0;display:flex;flex-wrap:wrap;align-items:center;gap:5px;padding:6px 8px;border-bottom:1px solid #94a3b8;background:#dce7f1}
.title{font-size:14px;font-weight:900}
</style>
</head>
<body>
<div class="topbar"><span class="title">aiTrade Board Hub</span></div>
<main class="bp-board-hub">
<h1>aiTrade Boards</h1>
<p>&#48372;&#46300; &#49345;&#53468;&#50752; &#51060;&#46041;&#47564; &#51228;&#44277;&#54616;&#45716; &#44221;&#47049; &#54728;&#48652;&#51077;&#45768;&#45796;.</p>
<div id="bp-board-grid" class="bp-board-grid"><section class="bp-board-card"><h2>Loading</h2></section></div>
</main>
<script src="/api/v2/boards/shell.js"></script>
</body>
</html>
"""

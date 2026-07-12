from __future__ import annotations

SHELL_CSS = r"""
#bp-shell-nav{display:inline-flex;align-items:center;gap:3px;flex-wrap:wrap}
#bp-shell-nav .bp-tab,#bp-shell-nav .bp-popout{display:inline-flex;align-items:center;justify-content:center;min-height:21px;padding:1px 6px;border:1px solid #9aa8b5;border-radius:3px;background:#edf3f8;color:#111827;text-decoration:none;font:800 11px "Malgun Gothic",Arial,sans-serif;white-space:nowrap}
#bp-shell-nav .bp-tab.active{color:#fff;border-color:#1d4ed8;background:#1d4ed8}
#bp-shell-nav .bp-tab.disabled{color:#94a3b8;background:#f1f5f9;cursor:not-allowed}
#bp-shell-nav .bp-popout{background:#fff;cursor:pointer}
.bp-shell-ready .topbar>.tab{display:none!important}
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
.bp-board-actions{display:flex;gap:4px;margin-top:6px}
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
function buildShell(){
 const bar=rootTopbar();if(!bar)return;
 document.body.classList.add('bp-shell-ready');
 let nav=document.getElementById('bp-shell-nav');
 if(!nav){nav=document.createElement('span');nav.id='bp-shell-nav';const title=bar.querySelector('.title');if(title&&title.nextSibling)bar.insertBefore(nav,title.nextSibling);else bar.insertBefore(nav,bar.firstChild);}
 let speed=document.getElementById('bp-speed-strip');
 if(!speed){speed=document.createElement('div');speed.id='bp-speed-strip';speed.innerHTML='<span class="bp-speed muted"><b>ì†ë„</b> ìˆ˜ì‹  ëŒ€ê¸°</span>';bar.appendChild(speed);}
 fetch('/api/v2/boards',{cache:'no-store'}).then(r=>r.json()).then(data=>{
   const boards=Array.isArray(data.boards)?data.boards:[];
   nav.innerHTML=boards.map(b=>{
     const active=b.board_id===boardId?' active':'';
     if(!b.enabled)return `<span class="bp-tab disabled" title="${esc(b.description)}">${esc(b.label)} ì¤€ë¹„ì¤‘</span>`;
     return `<a class="bp-tab${active}" href="${esc(b.url)}" title="${esc(b.description)}">${esc(b.label)}</a>`;
   }).join('')+`<button type="button" class="bp-popout" title="í˜„ì¬ ë³´ë“œë¥¼ ìƒˆ ì°½ìœ¼ë¡œ ì—´ê¸°">ìƒˆ ì°½</button>`;
   const pop=nav.querySelector('.bp-popout');if(pop)pop.addEventListener('click',()=>window.open(location.href,'_blank','noopener'));
 }).catch(()=>{nav.innerHTML='<a class="bp-tab" href="/">StockBoard</a><a class="bp-tab" href="/theme">ThemeBoard</a>';});
}
function hookRender(){
 const fn=window.render;
 if(typeof fn!=='function'||fn.__bpMeasured)return false;
 function wrapped(){
   const start=performance.now();
   try{return fn.apply(this,arguments);}
   finally{const ms=performance.now()-start;renderSamples.push(ms);if(renderSamples.length>40)renderSamples.shift();}
 }
 wrapped.__bpMeasured=true;window.render=wrapped;return true;
}
function renderClientMetric(){
 if(!renderSamples.length)return {key:'browser_render',label:'ë Œë”',text:'-',tone:'muted'};
 const last=renderSamples[renderSamples.length-1];
 const ordered=renderSamples.slice().sort((a,b)=>a-b);
 const p95=ordered[Math.min(ordered.length-1,Math.floor(ordered.length*.95))];
 const tone=p95<=16?'good':p95<=40?'warn':'bad';
 return {key:'browser_render',label:'ë Œë”',text:`${last.toFixed(1)}ms p95 ${p95.toFixed(1)}`,tone};
}
function drawMetrics(metrics,rtt){
 const el=document.getElementById('bp-speed-strip');if(!el)return;
 const all=[...(Array.isArray(metrics)?metrics:[]),{key:'api_rtt',label:'API',text:`${rtt.toFixed(1)}ms`,tone:rtt<=10?'good':rtt<=50?'warn':'bad'},renderClientMetric()];
 el.innerHTML=all.map(m=>`<span class="bp-speed ${esc(m.tone||'muted')}"><b>${esc(m.label)}</b> ${esc(m.text)}</span>`).join('');
}
async function pollPerformance(){
 const start=performance.now();
 try{
   const response=await fetch(`/api/v2/boards/performance?board_id=${encodeURIComponent(boardId)}`,{cache:'no-store'});
   const data=await response.json();
   drawMetrics(data.display_metrics||[],performance.now()-start);
 }catch(_e){
   const el=document.getElementById('bp-speed-strip');if(el)el.innerHTML='<span class="bp-speed bad"><b>ì†ë„</b> API ì—°ê²° ì‹¤íŒ¨</span>';
 }
 setTimeout(pollPerformance,document.hidden?5000:2000);
}
async function loadHub(){
 const grid=document.getElementById('bp-board-grid');if(!grid)return;
 try{
   const data=await fetch('/api/v2/boards',{cache:'no-store'}).then(r=>r.json());
   grid.innerHTML=(data.boards||[]).filter(b=>b.board_id!=='boards').map(b=>`<section class="bp-board-card ${b.enabled?'':'disabled'}"><h2>${esc(b.label)}</h2><p>${esc(b.description)}</p><p>ìƒíƒœ <b>${esc(b.state||'-')}</b> Â· ì ‘ì† ${esc(b.clients||0)} Â· cache ${esc(b.cache_version||0)}</p><p>ê°±ì‹  ${esc(b.last_updated_at||'-')}</p><div class="bp-board-actions">${b.enabled?`<a href="${esc(b.url)}">ì—´ê¸°</a><button type="button" data-pop="${esc(b.url)}">ìƒˆ ì°½</button>`:'<span>ì¤€ë¹„ì¤‘</span>'}</div></section>`).join('');
   grid.querySelectorAll('[data-pop]').forEach(btn=>btn.addEventListener('click',()=>window.open(btn.dataset.pop,'_blank','noopener')));
 }catch(_e){grid.innerHTML='<section class="bp-board-card"><h2>Board Hub ì˜¤ë¥˜</h2><p>ìƒíƒœ APIë¥¼ ì½ì§€ ëª»í–ˆìŠµë‹ˆë‹¤.</p></section>';}
 setTimeout(loadHub,5000);
}
function start(){
 buildShell();
 let tries=0;const timer=setInterval(()=>{tries++;if(hookRender()||tries>20)clearInterval(timer);},250);
 pollPerformance();loadHub();
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
"""

BOARDS_HTML = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>aiTrade Board Hub</title><link rel="stylesheet" href="/api/v2/boards/shell.css">
<style>html,body{margin:0;background:#f1f5f9;color:#111827;font:12px "Malgun Gothic",Arial,sans-serif}.topbar{position:sticky;top:0;display:flex;flex-wrap:wrap;align-item\Ìé•¹Ñ•Èí…ÀèÕÁàíÁ…‘‘¥¹œèÙÁàí‰½É‘•Èµ‰½ÑÑ½´èÅÁàÍ½±¥€ŒäÑ„Íˆàí‰…­É½Õ¹è‘”İ˜Åô¹Ñ¥Ñ±•í™½¹ĞµÍ¥é”èÄÑÁàí™½¹Ğµİ•¥¡ĞèäÀÁôğ½ÍÑå±”ø(ğ½¡•…øñ‰½‘äøñ‘¥Ø±…ÍÌô‰Ñ½Á‰…ÈˆøñÍÁ…¸±…ÍÌô‰Ñ¥Ñ±”ˆù…¥QÉ…‘”	½…É!Õˆğ½ÍÁ…¸øğ½‘¥Øøñµ…¥¸±…ÍÌô‰‰Àµ‰½…Éµ¡Õˆˆøñ‘¥Ø¥ô‰‰Àµ‰½…ÉµÉ¥ˆ±…ÍÌô‰‰Àµ‰½…ÉµÉ¥ˆøñÍ•Ñ¥½¸±…ÍÌô‰‰Àµ‰½…Éµ…Éˆøñ Èû®ÎÓ®Npƒ²¶pƒ²"c².€ƒ®2ªâÀğ½ Èøğ½Í•Ñ¥½¸øğ½‘¥Øøğ½µ…¥¸øñÍÉ¥ÁĞÍÉŒôˆ½…Á¤½ØÈ½‰½…É‘Ì½Í¡•±°¹©Ìˆøğ½ÍÉ¥ÁĞøğ½‰½‘äøğ½¡Ñµ°øˆˆˆ(
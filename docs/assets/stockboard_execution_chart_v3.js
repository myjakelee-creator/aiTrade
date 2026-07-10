(() => {
  'use strict';

  const API_BASES = ['', 'http://127.0.0.1:8010'];
  const CACHE = '20260629_viewport';
  const STORAGE_FIT = 'stockboard.executionChart.fit.v3';
  const STORAGE_WIDTH = 'stockboard.executionChart.minuteWidth.v3';
  const COLORS = {
    text:'#17202a', muted:'#5f6f80', grid:'#d8e0e7', border:'#9aa8b5',
    red:'#d71920', blue:'#1266d6', green:'#18a558', gold:'#b8860b',
    gray:'#7f8c8d', cross:'#d71920',
    premarket:'rgba(251,191,36,.08)', regular:'rgba(24,165,88,.06)',
    closing_call:'rgba(215,25,32,.07)', aftermarket:'rgba(18,102,214,.06)'
  };
  const nf = new Intl.NumberFormat('ko-KR');
  const S = {
    apiBase:null, index:null, payload:null, series:[],
    pad:{left:58,right:18,top:18,bottom:24},
    fit:true, minuteWidth:4.2, start:0, count:0, hoverIndex:null,
    renderQueued:false, meta:{}
  };

  const $ = id => document.getElementById(id);
  const num = v => {
    if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null;
    const n = Number(String(v).replace(/,/g,''));
    return Number.isFinite(n) ? n : null;
  };
  const pos = v => { const n = num(v); return n !== null && n > 0 ? n : null; };
  const intText = v => { const n = num(v); return n === null ? '-' : nf.format(Math.round(n)); };
  const fixedText = (v,d=2) => { const n = num(v); return n === null ? '-' : n.toLocaleString('ko-KR',{minimumFractionDigits:d,maximumFractionDigits:d}); };
  const setStatus = t => { const e = $('status'); if (e) e.textContent = t; };
  const setHover = t => { const e = $('hover'); if (e) e.textContent = t; };
  const error = t => { setStatus('오류'); setHover(t); console.warn(t); };
  const cacheUrl = u => `${u}${u.includes('?')?'&':'?'}v=${CACHE}&t=${Date.now()}`;
  const apiBases = () => S.apiBase ? [S.apiBase, ...API_BASES.filter(x => x !== S.apiBase)] : API_BASES;

  async function apiJson(path, opts = {}) {
    let last = null;
    for (const base of apiBases()) {
      const url = `${base}${path}`;
      try {
        const res = await fetch(opts.method === 'POST' ? url : cacheUrl(url), {cache:'no-store', ...opts});
        const text = await res.text();
        if (!res.ok) throw new Error(text || String(res.status));
        S.apiBase = base;
        return text ? JSON.parse(text) : {};
      } catch(e) { last = e; }
    }
    throw last || new Error(path + ' failed');
  }

  function rowToObject(cols,row) {
    if (!Array.isArray(row)) return row || {};
    const out = {};
    cols.forEach((k,i) => out[k] = row[i] ?? null);
    return out;
  }
  function normalize(payload) {
    const cols = Array.isArray(payload.columns) ? payload.columns : [];
    const rows = Array.isArray(payload.series) ? payload.series : [];
    return {...payload, columns:cols, series:rows.map(r => rowToObject(cols,r))};
  }

  function dateItems() {
    const map = new Map();
    (S.index?.items || []).forEach(x => { if (!map.has(x.date)) map.set(x.date, x.date_label || x.date); });
    return [...map].map(([date,label]) => ({date,label}));
  }
  function itemsForDate(date) { return (S.index?.items || []).filter(x => x.date === date); }
  function selectedItem() {
    const date = $('date')?.value, code = $('code')?.value;
    return itemsForDate(date).find(x => x.stock_code === code) || itemsForDate(date)[0] || null;
  }
  function fillDates() {
    const sel = $('date'); if (!sel) return;
    sel.innerHTML = '';
    const rows = dateItems();
    if (!rows.length) { sel.innerHTML = '<option value="">저장 데이터 없음</option>'; return; }
    rows.forEach(r => { const o = document.createElement('option'); o.value = r.date; o.textContent = r.label; sel.appendChild(o); });
  }
  function fillCodes() {
    const sel = $('code'), date = $('date')?.value; if (!sel) return;
    sel.innerHTML = '';
    const rows = itemsForDate(date);
    if (!rows.length) { sel.innerHTML = '<option value="">저장 종목 없음</option>'; return; }
    rows.forEach(r => { const o = document.createElement('option'); o.value = r.stock_code; o.textContent = `${r.stock_code} ${r.stock_name || ''}`.trim(); sel.appendChild(o); });
  }

  function setupCanvas(canvas,w,h) {
    const dpr = window.devicePixelRatio || 1;
    canvas.style.width = w + 'px'; canvas.style.height = h + 'px';
    canvas.width = Math.round(w*dpr); canvas.height = Math.round(h*dpr);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr,0,0,dpr,0,0);
    ctx.clearRect(0,0,w,h);
    ctx.font = '10px Malgun Gothic, Arial, sans-serif';
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    return ctx;
  }
  function viewportWidth() {
    const el = $('viewport');
    return Math.max(320, el?.clientWidth || 760);
  }
  function computeView() {
    const n = S.series.length;
    const w = viewportWidth();
    const plot = Math.max(80, w - S.pad.left - S.pad.right);
    if (!n) { S.start = 0; S.count = 0; return {w, plot, start:0, end:0, count:0}; }
    if (S.fit) {
      S.start = 0; S.count = n;
    } else {
      const count = Math.max(5, Math.min(n, Math.floor(plot / Math.max(1.5, S.minuteWidth)) + 1));
      S.count = count;
      S.start = Math.max(0, Math.min(S.start, n - count));
    }
    return {w, plot, start:S.start, end:Math.min(n-1, S.start + S.count - 1), count:S.count};
  }
  function visibleSeries() {
    const v = computeView();
    return S.series.slice(v.start, v.end + 1);
  }
  function xFor(index, view) {
    const count = Math.max(1, view.count - 1);
    return S.pad.left + (index - view.start) * (view.plot / count);
  }
  function yScale(min,max,h) {
    const span = max - min || 1;
    return val => h - S.pad.bottom - ((val-min)/span) * (h-S.pad.top-S.pad.bottom);
  }
  function domain(vals,opt={}) {
    const fn = opt.positiveOnly ? pos : num;
    const valid = vals.concat(opt.extras || []).map(fn).filter(v => v !== null);
    let min = Math.min(...valid), max = Math.max(...valid);
    if (!Number.isFinite(min) || !Number.isFinite(max)) return [0,1];
    if (min === max) { const p = Math.max(1, Math.abs(min)*0.01); return [min-p,max+p]; }
    const pad = Math.max(1, (max-min)*(opt.padRatio ?? 0.025));
    return [min-pad,max+pad];
  }
  function ticks(vals) {
    const seen = new Set();
    return vals.filter(v => { const k = Math.round(v*10000)/10000; if (!Number.isFinite(v) || seen.has(k)) return false; seen.add(k); return true; });
  }
  function clip(ctx,w,h,fn) {
    ctx.save();
    ctx.beginPath();
    ctx.rect(S.pad.left,S.pad.top,w-S.pad.left-S.pad.right,h-S.pad.top-S.pad.bottom);
    ctx.clip();
    fn();
    ctx.restore();
  }
  function drawFrame(ctx,w,h,tickList,fmt) {
    ctx.strokeStyle = COLORS.grid; ctx.fillStyle = COLORS.muted;
    tickList.forEach(t => {
      ctx.beginPath(); ctx.moveTo(S.pad.left,t.y); ctx.lineTo(w-S.pad.right,t.y); ctx.stroke();
      ctx.fillText(fmt(t.value),4,t.y+3);
    });
    ctx.strokeStyle = COLORS.border;
    ctx.strokeRect(S.pad.left,S.pad.top,w-S.pad.left-S.pad.right,h-S.pad.top-S.pad.bottom);
  }
  function drawSessions(ctx,w,h,view) {
    let a = view.start;
    while (a <= view.end) {
      const sess = S.series[a].session;
      let b = a + 1;
      while (b <= view.end && S.series[b].session === sess) b++;
      ctx.fillStyle = COLORS[sess] || 'transparent';
      ctx.fillRect(xFor(a,view), S.pad.top, Math.max(2, xFor(b-1,view)-xFor(a,view)), h-S.pad.top-S.pad.bottom);
      a = b;
    }
  }
  function drawLine(ctx,w,h,key,scale,color,view,dash=null) {
    clip(ctx,w,h,() => {
      ctx.save(); ctx.strokeStyle = color; ctx.lineWidth = 1.5; if (dash) ctx.setLineDash(dash);
      ctx.beginPath(); let ok = false;
      for (let i=view.start; i<=view.end; i++) {
        const val = num(S.series[i]?.[key]);
        if (val === null) { ok = false; continue; }
        const x = xFor(i,view), y = scale(val);
        if (!ok) { ctx.moveTo(x,y); ok = true; } else ctx.lineTo(x,y);
      }
      ctx.stroke(); ctx.restore();
    });
  }
  function drawTimes(ctx,w,h,view) {
    ctx.fillStyle = COLORS.muted;
    ['08:00','09:00','12:00','15:30','15:40','19:59'].forEach(label => {
      const i = S.series.findIndex(p => p.time === label);
      if (i >= view.start && i <= view.end) ctx.fillText(label, xFor(i,view)-13, h-7);
    });
  }

  function renderPrice(view) {
    const c = $('price-canvas'); if (!c) return;
    const w = view.w, h = 220, ctx = setupCanvas(c,w,h);
    drawSessions(ctx,w,h,view);
    const vals = S.series.slice(view.start, view.end+1).flatMap(p => [p.open,p.high,p.low,p.close]);
    const [min,max] = domain(vals,{positiveOnly:true,padRatio:.018});
    const scale = yScale(min,max,h);
    drawFrame(ctx,w,h,ticks([min,(min+max)/2,max]).map(v => ({value:v,y:scale(v)})),intText);
    const step = Math.max(2, view.plot / Math.max(1, view.count-1));
    const cw = Math.max(1, Math.min(12, step*.58));
    clip(ctx,w,h,() => {
      for (let i=view.start; i<=view.end; i++) {
        const p = S.series[i], o = pos(p.open), hi = pos(p.high), lo = pos(p.low), cl = pos(p.close);
        if ([o,hi,lo,cl].some(v => v === null)) continue;
        const x = xFor(i,view), yo = scale(o), yh = scale(hi), yl = scale(lo), yc = scale(cl), up = cl >= o;
        ctx.strokeStyle = up ? COLORS.red : COLORS.blue; ctx.fillStyle = ctx.strokeStyle;
        ctx.beginPath(); ctx.moveTo(x,yh); ctx.lineTo(x,yl); ctx.stroke();
        ctx.fillRect(x-cw/2, Math.min(yo,yc), cw, Math.max(1,Math.abs(yc-yo)));
      }
    });
    drawLine(ctx,w,h,'vwap',scale,COLORS.gold,view);
    drawLine(ctx,w,h,'regular_open_line',scale,COLORS.green,view,[5,4]);
    drawTimes(ctx,w,h,view);
    S.meta.price = {ctx,w,h,view, y:i => { const p=S.series[i], val=pos(p?.close) ?? pos(p?.trade_price); return val===null?null:scale(val); }};
  }
  function renderStrength(view) {
    const c = $('strength-canvas'); if (!c) return;
    const w = view.w, h = 170, ctx = setupCanvas(c,w,h);
    drawSessions(ctx,w,h,view);
    const vals = S.series.slice(view.start,view.end+1).map(p=>num(p.strength)).filter(v=>v!==null);
    const [min,max] = domain(vals,{padRatio:.08});
    const scale = yScale(min,max,h);
    drawFrame(ctx,w,h,ticks([min,100,max]).map(v => ({value:v,y:scale(v)})),v=>fixedText(v,1));
    ctx.save(); ctx.strokeStyle = COLORS.gray; ctx.setLineDash([4,4]); ctx.beginPath(); ctx.moveTo(S.pad.left,scale(100)); ctx.lineTo(w-S.pad.right,scale(100)); ctx.stroke(); ctx.restore();
    drawLine(ctx,w,h,'strength',scale,COLORS.text,view);
    drawTimes(ctx,w,h,view);
    S.meta.strength = {ctx,w,h,view, y:i => { const val=num(S.series[i]?.strength); return val===null?null:scale(val); }};
  }
  function renderVolume(view) {
    const c = $('volume-canvas'); if (!c) return;
    const w = view.w, h = 170, ctx = setupCanvas(c,w,h);
    drawSessions(ctx,w,h,view);
    const totals = S.series.slice(view.start,view.end+1).map(p => (num(p.sell_volume)||0)+(num(p.buy_volume)||0));
    const maxV = Math.max(1,...totals);
    const base = h-S.pad.bottom, inner = h-S.pad.top-S.pad.bottom;
    const step = Math.max(2, view.plot / Math.max(1, view.count-1));
    const bw = Math.max(1, Math.min(12, step*.58));
    drawFrame(ctx,w,h,ticks([0,maxV/2,maxV]).map(v => ({value:v,y:base-v/maxV*inner})),intText);
    clip(ctx,w,h,() => {
      for (let i=view.start; i<=view.end; i++) {
        const p = S.series[i], sell = num(p.sell_volume)||0, buy = num(p.buy_volume)||0;
        if (!sell && !buy) continue;
        const x = xFor(i,view), bh = buy/maxV*inner, sh = sell/maxV*inner;
        ctx.fillStyle = COLORS.red; ctx.fillRect(x-bw/2,base-bh,bw,Math.max(1,bh));
        ctx.fillStyle = COLORS.blue; ctx.fillRect(x-bw/2,base-bh-sh,bw,Math.max(1,sh));
      }
    });
    drawTimes(ctx,w,h,view);
    S.meta.volume = {ctx,w,h,view, y:i => { const p=S.series[i], t=(num(p?.sell_volume)||0)+(num(p?.buy_volume)||0); return t ? base-t/maxV*inner : null; }};
  }
  function drawCross() {
    const i = S.hoverIndex;
    if (i === null || i < S.start || i >= S.start+S.count) return;
    Object.values(S.meta).forEach(m => {
      if (!m?.ctx) return;
      const ctx = m.ctx, x = xFor(i,m.view), y = m.y ? m.y(i) : null;
      ctx.save(); ctx.strokeStyle = COLORS.cross; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(Math.round(x)+.5,S.pad.top); ctx.lineTo(Math.round(x)+.5,m.h-S.pad.bottom); ctx.stroke();
      if (Number.isFinite(y)) { ctx.beginPath(); ctx.moveTo(S.pad.left,Math.round(y)+.5); ctx.lineTo(m.w-S.pad.right,Math.round(y)+.5); ctx.stroke(); }
      ctx.restore();
    });
  }
  function renderSummary() {
    const p = S.payload || {}, m = p.meta || {}, s = p.summary || {}, el = $('summary');
    if (!el) return;
    el.innerHTML = [
      `<span><b>종목</b> ${m.stock_code||'-'} ${m.stock_name||''}</span>`,
      `<span><b>구간</b> ${s.time_range||'-'} · ${s.chart_points||S.series.length}/${s.row_count||S.series.length}점</span>`,
      `<span><b>시가선</b> ${intText(s.regular_open_price)}</span>`,
      `<span><b>VWAP</b> ${intText(s.last_vwap)}</span>`,
      `<span><b>고가/저가</b> ${intText(s.high)} / ${intText(s.low)}</span>`,
      `<span><b>거래대금</b> ${fixedText(s.total_trade_value_eok,1)}억</span>`
    ].join('');
  }
  function updateRange(view) {
    const r = $('range'), label = $('range-label');
    if (!r || !label) return;
    const maxStart = Math.max(0, S.series.length - view.count);
    r.max = String(maxStart); r.value = String(S.start); r.disabled = maxStart <= 0;
    const a = S.series[view.start]?.time || '-', b = S.series[view.end]?.time || '-';
    label.textContent = S.fit ? '구간 이동: 전체' : `구간 이동: ${a}~${b}`;
  }
  function updateZoom() {
    const el = $('zoom-label');
    if (el) el.textContent = S.fit ? '화면맞춤' : `분폭 ${S.minuteWidth.toFixed(1)}px`;
  }
  function renderCharts() {
    if (!S.payload || !S.series.length) return;
    renderSummary();
    const view = computeView();
    S.meta = {};
    renderPrice(view); renderStrength(view); renderVolume(view); drawCross();
    updateRange(view); updateZoom();
    const sm = S.payload.summary || {};
    setStatus(`${sm.chart_points||S.series.length}/${sm.row_count||S.series.length}점 JSON`);
    if (S.hoverIndex === null) setHover(`${S.payload.meta?.stock_code||''} ${S.payload.meta?.stock_name||''} · ${sm.time_range||''}\n전체 ${sm.chart_points||S.series.length}분 JSON 로드 완료.`);
  }
  function scheduleRender(){ if (S.renderQueued) return; S.renderQueued = true; requestAnimationFrame(() => { S.renderQueued = false; renderCharts(); }); }
  function hoverText(p) {
    if (!p) return '차트 위에 마우스를 올리면 해당 분 정보를 표시합니다.';
    return [
      `${p.time_full||p.time} · ${p.session||'-'}`,
      `OHLC ${intText(p.open)} / ${intText(p.high)} / ${intText(p.low)} / ${intText(p.close)}`,
      `VWAP ${intText(p.vwap)} · 09시 시가선 ${intText(p.regular_open_line)}`,
      `체결가 ${intText(p.trade_price)} · 체결강도 ${fixedText(p.strength,2)}`,
      `매수 ${intText(p.buy_volume)} · 매도 ${intText(p.sell_volume)} · 합계 ${intText(p.total_execution_volume)}`,
      `분봉 거래대금 ${num(p.trade_value_eok)===null?'-':fixedText(p.trade_value_eok,1)+'억'}`
    ].join('\n');
  }
  function attachHover() {
    const v = $('viewport');
    if (!v || v.dataset.ready === '1') return;
    v.dataset.ready = '1';
    v.addEventListener('mousemove', e => {
      const c = e.target?.closest?.('canvas'); if (!c) return;
      const rect = c.getBoundingClientRect(), view = computeView();
      const x = e.clientX - rect.left, ratio = (x-S.pad.left)/Math.max(1,view.plot);
      const i = Math.max(view.start, Math.min(view.end, Math.round(view.start + ratio*(view.count-1))));
      S.hoverIndex = i; setHover(hoverText(S.series[i])); scheduleRender();
    });
    v.addEventListener('mouseleave', () => { S.hoverIndex = null; scheduleRender(); });
  }

  async function loadSelected() {
    const item = selectedItem();
    if (!item) { error('저장된 차트 JSON이 없습니다.'); return; }
    setStatus('로드중');
    try {
      const p = normalize(await apiJson(`/api/execution_chart?date=${encodeURIComponent(item.date)}&code=${encodeURIComponent(item.stock_code)}`));
      S.payload = p; S.series = p.series; S.hoverIndex = null; S.start = 0;
      renderCharts(); window.scrollTo({top:0,left:0});
    } catch(e) { error('차트 JSON 로드 실패: ' + e.message); }
  }
  function dateItems() {
    const map = new Map();
    (S.index?.items || []).forEach(x => { if (!map.has(x.date)) map.set(x.date, x.date_label || x.date); });
    return [...map].map(([date,label]) => ({date,label}));
  }
  function itemsForDate(date) { return (S.index?.items || []).filter(x => x.date === date); }
  function selectedItem() {
    const date = $('date')?.value, code = $('code')?.value;
    return itemsForDate(date).find(x => x.stock_code === code) || itemsForDate(date)[0] || null;
  }
  function fillDates() {
    const sel = $('date'); if (!sel) return; sel.innerHTML = '';
    const rows = dateItems();
    if (!rows.length) { sel.innerHTML = '<option value="">저장 데이터 없음</option>'; return; }
    rows.forEach(r => { const o = document.createElement('option'); o.value = r.date; o.textContent = r.label; sel.appendChild(o); });
  }
  function fillCodes() {
    const sel = $('code'), date = $('date')?.value; if (!sel) return; sel.innerHTML = '';
    const rows = itemsForDate(date);
    if (!rows.length) { sel.innerHTML = '<option value="">저장 종목 없음</option>'; return; }
    rows.forEach(r => { const o = document.createElement('option'); o.value = r.stock_code; o.textContent = `${r.stock_code} ${r.stock_name||''}`.trim(); sel.appendChild(o); });
  }
  async function loadIndex() {
    try { S.index = await apiJson('/api/execution_chart_index'); fillDates(); fillCodes(); await loadSelected(); }
    catch(e) { error('차트 서버 연결 실패: ' + e.message + '\nstart_stockboard_execution_chart.cmd가 실행 중인지 확인하세요.'); }
  }
  async function upload() {
    const o = $('ohlc-file')?.files?.[0], ex = $('execution-file')?.files?.[0];
    if (!o) { error('OHLC 파일을 선택하세요.'); return; }
    const f = new FormData();
    f.append('date',$('upload-date')?.value||'');
    f.append('stock_code',$('upload-code')?.value||'000660');
    f.append('stock_name',$('upload-name')?.value||'SK하이닉스');
    f.append('ohlc_file',o,o.name);
    if (ex) f.append('execution_file',ex,ex.name);
    setStatus('업로드중');
    try {
      const r = await apiJson('/api/execution_chart_upload',{method:'POST',body:f});
      S.index = r.index || await apiJson('/api/execution_chart_index');
      fillDates();
      if (r.item) { $('date').value = r.item.date; fillCodes(); $('code').value = r.item.stock_code; }
      await loadSelected();
    } catch(e) { error('업로드 실패: ' + e.message); }
  }
  function fitMode() {
    S.fit = true; S.start = 0;
    try { localStorage.setItem(STORAGE_FIT,'1'); } catch(_){}
    renderCharts();
  }
  function applyMinute(w) {
    const oldView = computeView(), center = oldView.start + oldView.count/2;
    S.fit = false;
    S.minuteWidth = Math.round(Math.max(1.5, Math.min(40,w))*10)/10;
    const newView = computeView();
    S.start = Math.max(0, Math.min(S.series.length-newView.count, Math.round(center-newView.count/2)));
    try { localStorage.setItem(STORAGE_FIT,'0'); localStorage.setItem(STORAGE_WIDTH,String(S.minuteWidth)); } catch(_){}
    renderCharts();
  }
  function restoreZoom() {
    try {
      S.fit = localStorage.getItem(STORAGE_FIT) !== '0';
      const w = Number(localStorage.getItem(STORAGE_WIDTH));
      S.minuteWidth = Number.isFinite(w) && w > 0 ? Math.max(1.5, Math.min(40,w)) : 4.2;
    } catch(_) { S.fit = true; S.minuteWidth = 4.2; }
  }
  function init() {
    restoreZoom(); attachHover();
    $('date')?.addEventListener('change', () => { fillCodes(); loadSelected(); });
    $('code')?.addEventListener('change', loadSelected);
    $('reload')?.addEventListener('click', loadSelected);
    $('upload')?.addEventListener('click', upload);
    $('zoom-fit')?.addEventListener('click', fitMode);
    $('zoom-in')?.addEventListener('click', () => applyMinute(S.minuteWidth*1.25));
    $('zoom-out')?.addEventListener('click', () => applyMinute(S.minuteWidth/1.25));
    $('range')?.addEventListener('input', e => { S.start = Number(e.target.value)||0; S.fit = false; try { localStorage.setItem(STORAGE_FIT,'0'); } catch(_){} renderCharts(); });
    window.addEventListener('resize', () => requestAnimationFrame(renderCharts));
    loadIndex();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();

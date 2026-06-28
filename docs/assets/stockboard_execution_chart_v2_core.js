(() => {
  'use strict';
  const G = window.SBX2 = window.SBX2 || {};
  G.CACHE = '20260629_v2';
  G.API_BASES = ['', 'http://127.0.0.1:8010'];
  G.COLORS = { text:'#17202a', muted:'#5f6f80', grid:'#d8e0e7', border:'#9aa8b5', red:'#d71920', blue:'#1266d6', green:'#18a558', gold:'#b8860b', gray:'#7f8c8d', cross:'#d71920', premarket:'rgba(251,191,36,.08)', regular:'rgba(24,165,88,.06)', closing_call:'rgba(215,25,32,.07)', aftermarket:'rgba(18,102,214,.06)' };
  G.nf = new Intl.NumberFormat('ko-KR');
  G.state = { apiBase:null, index:null, payload:null, series:[], pad:{left:58,right:18,top:18,bottom:24}, width:980, minuteWidth:0, fit:true, hoverIndex:null, meta:{} };
  G.$ = id => document.getElementById(id);
  G.num = v => { if (v === null || v === undefined || v === '' || typeof v === 'boolean') return null; const n = Number(String(v).replace(/,/g,'')); return Number.isFinite(n) ? n : null; };
  G.pos = v => { const n = G.num(v); return n !== null && n > 0 ? n : null; };
  G.intText = v => { const n = G.num(v); return n === null ? '-' : G.nf.format(Math.round(n)); };
  G.fixedText = (v,d=2) => { const n = G.num(v); return n === null ? '-' : n.toLocaleString('ko-KR',{minimumFractionDigits:d,maximumFractionDigits:d}); };
  G.setStatus = text => { const el = G.$('status'); if (el) el.textContent = text; };
  G.setHover = text => { const el = G.$('hover'); if (el) el.textContent = text; };
  G.err = text => { G.setStatus('오류'); G.setHover(text); console.warn(text); };
  G.cacheUrl = url => `${url}${url.includes('?')?'&':'?'}v=${G.CACHE}&t=${Date.now()}`;
  G.apiBases = () => G.state.apiBase ? [G.state.apiBase, ...G.API_BASES.filter(x => x !== G.state.apiBase)] : G.API_BASES;
  G.apiJson = async (path, opts={}) => {
    let last = null;
    for (const base of G.apiBases()) {
      const url = `${base}${path}`;
      try {
        const res = await fetch(opts.method === 'POST' ? url : G.cacheUrl(url), {cache:'no-store', ...opts});
        const text = await res.text();
        if (!res.ok) throw new Error(text || String(res.status));
        G.state.apiBase = base;
        return text ? JSON.parse(text) : {};
      } catch (e) { last = e; }
    }
    throw last || new Error(path + ' failed');
  };
  G.rowToObject = (cols,row) => { if (!Array.isArray(row)) return row || {}; const out = {}; cols.forEach((k,i)=>out[k]=row[i]??null); return out; };
  G.normalize = payload => { const cols = Array.isArray(payload.columns) ? payload.columns : []; const rows = Array.isArray(payload.series) ? payload.series : []; return {...payload, columns:cols, series:rows.map(r=>G.rowToObject(cols,r))}; };
  G.xFor = (i,w=G.state.width) => { const n = Math.max(1,G.state.series.length-1); return G.state.pad.left + i*((w-G.state.pad.left-G.state.pad.right)/n); };
  G.visibleRange = () => { const s=G.$('xscroll'); if (!s || !G.state.series.length) return [0,Math.max(0,G.state.series.length-1)]; const left=s.scrollLeft, right=left+s.clientWidth, plot=Math.max(1,G.state.width-G.state.pad.left-G.state.pad.right), n=Math.max(1,G.state.series.length-1); return [Math.max(0,Math.floor(Math.max(0,left-G.state.pad.left)/plot*n)-3), Math.min(G.state.series.length-1,Math.ceil(Math.max(0,right-G.state.pad.left)/plot*n)+3)]; };
  G.visibleSeries = () => { const [a,b]=G.visibleRange(); return G.state.series.slice(a,b+1); };
  G.domain = (vals,opt={}) => { const fn=opt.positiveOnly?G.pos:G.num; const valid=vals.concat(opt.extras||[]).map(fn).filter(v=>v!==null); let min=Math.min(...valid), max=Math.max(...valid); if(!Number.isFinite(min)||!Number.isFinite(max)) return [0,1]; if(min===max){const p=Math.max(1,Math.abs(min)*0.01); return [min-p,max+p];} const pad=Math.max(1,(max-min)*(opt.padRatio??0.025)); return [min-pad,max+pad]; };
  G.yScale = (min,max,h) => v => h-G.state.pad.bottom-((v-min)/(max-min||1))*(h-G.state.pad.top-G.state.pad.bottom);
  G.ticks = vals => { const seen=new Set(); return vals.filter(v=>{const k=Math.round(v*10000)/10000; if(!Number.isFinite(v)||seen.has(k)) return false; seen.add(k); return true;}); };
  G.setupCanvas = (canvas,w,h) => { const dpr=window.devicePixelRatio||1; canvas.style.width=w+'px'; canvas.style.height=h+'px'; canvas.width=Math.round(w*dpr); canvas.height=Math.round(h*dpr); const ctx=canvas.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0); ctx.clearRect(0,0,w,h); ctx.font='10px Malgun Gothic, Arial, sans-serif'; ctx.lineCap='round'; ctx.lineJoin='round'; return ctx; };
  G.withClip = (ctx,w,h,draw) => { const p=G.state.pad; ctx.save(); ctx.beginPath(); ctx.rect(p.left,p.top,w-p.left-p.right,h-p.top-p.bottom); ctx.clip(); draw(); ctx.restore(); };
})();

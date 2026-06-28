(() => {
  'use strict';

  const SAMPLE_ROWS = [["08:00","premarket",2917000,2955000,2865000,2884000,2901333.33,null,1984.14,2884000,103.03,34657,33638,68295],["08:30","premarket",2899000,2900000,2888000,2889000,2891306.74,null,254.875,2889000,80.4,1306,7501,8807],["09:00","regular",2850000,2879000,2843000,2870000,2864000,2850000,8294.363,2870000,70.76,35384,36893,72277],["09:30","regular",2809000,2809000,2789000,2789000,2842596.49,2850000,1475.325,2789000,73.28,11011,41698,52709],["10:00","regular",2784000,2795000,2780000,2794000,2824801.61,2850000,659.477,2794000,79.23,14052,9600,23652],["10:30","regular",2793000,2797000,2789000,2793000,2820314.39,2850000,611.388,2793000,80.89,8085,13805,21890],["10:57","regular",2769000,2769000,2758000,2760000,2815514.64,2850000,1806.55,2760000,76.03,8476,56884,65360],["11:00","regular",2764000,2774000,2761000,2764000,2814367.23,2850000,383.139,2764000,76.34,8029,5824,13853],["11:30","regular",2729000,2743000,2725000,2740000,2799802.05,2850000,520.711,2740000,73.95,12234,6810,19044],["12:00","regular",2668000,2669000,2653000,2654000,2780147.48,2850000,581.721,2654000,71.48,6863,14998,21861],["12:10","regular",2641000,2643000,2639000,2641000,2774414.85,2850000,139.101,2641000,71.26,1505,3762,5267],["12:40","regular",2641000,2650000,2640000,2640000,2771573.13,2850000,2846.48,2640000,71.19,21540,33229,54769],["13:00","regular",2675000,2680000,2672000,2679000,2755218.29,2850000,1467.525,2679000,73.42,29012,25818,54830],["13:24","regular",2648000,2649000,2637000,2638000,2746409.07,2850000,1652.747,2638000,71.83,6957,55576,62533],["13:49","regular",2601000,2602000,2600000,2600000,2735599.42,2850000,728.288,2600000,69.43,8099,19904,28003],["14:00","regular",2627000,2644000,2625000,2641500,2730702.54,2850000,665.338,2641500,69.57,15576,9680,25256],["14:14","regular",2694000,2704000,2689000,2704000,2727721.44,2850000,1165.563,2704000,71.51,27514,15691,43205],["14:30","regular",2714000,2726000,2712000,2722000,2726287.99,2850000,588.066,2722000,72.5,12397,9235,21632],["15:00","regular",2641000,2651000,2640000,2642000,2721581.81,2850000,602.639,2642000,72.63,11416,11381,22797],["15:19","regular",2665000,2666000,2656000,2656000,2716558.97,2850000,865.303,2656000,73.16,13300,19221,32521],["15:30","closing_call",2673000,2673000,2673000,2673000,2713882.88,2850000,16233.503,2673000,73.16,null,null,null],["15:35","closing_call",2673000,2673000,2673000,2673000,2713849.59,2850000,215.31,null,null,null,null,null],["15:40","aftermarket",2673000,2681000,2673000,2680000,2713682.27,2850000,1241.825,2680000,73.34,9613,4642,14255],["16:00","aftermarket",2697000,2701000,2697000,2701000,2713352.87,2850000,118.324,2701000,73.95,3663,721,4384],["17:00","aftermarket",2715000,2716000,2714000,2716000,2713396.45,2850000,33.968,2716000,74.56,658,593,1251],["18:00","aftermarket",2698000,2699000,2691000,2692000,2713192.11,2850000,86.402,2692000,74.1,971,2235,3206],["19:00","aftermarket",2696000,2696000,2695000,2696000,2713073.85,2850000,22.86,2696000,74.13,529,319,848],["19:59","aftermarket",2700000,2701000,2698000,2700000,2712822.25,2850000,465.302,2700000,74.51,5488,11747,17235]];

  const COLORS = {
    text: '#17202a', muted: '#5f6f80', grid: '#d8e0e7', border: '#9aa8b5',
    red: '#d71920', blue: '#1266d6', green: '#18a558', gold: '#b8860b', gray: '#7f8c8d',
    pre: 'rgba(251,191,36,.08)', regular: 'rgba(24,165,88,.06)', close: 'rgba(215,25,32,.07)', after: 'rgba(18,102,214,.06)'
  };
  const nf = new Intl.NumberFormat('ko-KR');
  const state = { width: 980, pad: { left: 52, right: 18, top: 18, bottom: 24 }, series: [] };

  function $(id) { return document.getElementById(id); }
  function num(value) { const n = Number(value); return Number.isFinite(n) ? n : null; }
  function intText(value) { const n = num(value); return n === null ? '-' : nf.format(Math.round(n)); }
  function fixedText(value, digits = 2) { const n = num(value); return n === null ? '-' : n.toLocaleString('ko-KR', { minimumFractionDigits: digits, maximumFractionDigits: digits }); }
  function priceText(value) { return intText(value); }

  function rowToPoint(row) {
    return {
      time: row[0], time_full: `${row[0]}:00`, session: row[1], open: row[2], high: row[3], low: row[4], close: row[5],
      vwap: row[6], regular_open_line: row[7], trade_value_eok: row[8], trade_price: row[9], strength: row[10],
      buy_volume: row[11], sell_volume: row[12], total_execution_volume: row[13]
    };
  }

  function samplePayload() {
    const series = SAMPLE_ROWS.map(rowToPoint);
    return {
      meta: { date: '20260626', stock_code: '000660', stock_name: 'SK하이닉스', market: '통합장', timeframe: '1m' },
      summary: {
        time_range: '08:00~19:59', row_count: 663, chart_points: series.length, regular_open_price: 2850000,
        first_price: 2884000, last_price: 2700000, high: 2955000, low: 2600000, last_vwap: 2712822.25,
        total_trade_value_eok: 313252.11
      },
      series
    };
  }

  function fillControls() {
    const dateSelect = $('execution-chart-date');
    const codeSelect = $('execution-chart-code');
    if (dateSelect) dateSelect.innerHTML = '<option value="20260626">2026-06-26</option>';
    if (codeSelect) codeSelect.innerHTML = '<option value="000660">000660 SK하이닉스</option>';
  }

  function renderSummary(payload) {
    const el = $('execution-chart-summary');
    const meta = payload.meta, summary = payload.summary;
    if (!el) return;
    el.innerHTML = [
      `<span><b>종목</b> ${meta.stock_code} ${meta.stock_name}</span>`,
      `<span><b>구간</b> ${summary.time_range} · ${summary.chart_points}/${summary.row_count}점</span>`,
      `<span><b>시가선</b> ${priceText(summary.regular_open_price)}</span>`,
      `<span><b>VWAP</b> ${priceText(summary.last_vwap)}</span>`,
      `<span><b>고가/저가</b> ${priceText(summary.high)} / ${priceText(summary.low)}</span>`,
      `<span><b>거래대금</b> ${fixedText(summary.total_trade_value_eok, 1)}억</span>`
    ].join('');
  }

  function setupCanvas(canvas, width, height) {
    const dpr = window.devicePixelRatio || 1;
    canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.font = '10px Malgun Gothic, Arial, sans-serif';
    ctx.lineCap = 'round'; ctx.lineJoin = 'round';
    return ctx;
  }

  function xFor(index, width) {
    const pad = state.pad, count = Math.max(1, state.series.length - 1);
    return pad.left + index * ((width - pad.left - pad.right) / count);
  }

  function yScale(min, max, height) {
    const pad = state.pad, span = max - min || 1;
    return value => height - pad.bottom - ((value - min) / span) * (height - pad.top - pad.bottom);
  }

  function domain(values, extras = []) {
    const v = values.concat(extras).filter(Number.isFinite);
    let min = Math.min(...v), max = Math.max(...v);
    if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
    if (min === max) { min -= 1; max += 1; }
    const pad = (max - min) * .06;
    return [min - pad, max + pad];
  }

  function drawFrame(ctx, width, height, ticks, formatter) {
    const pad = state.pad;
    ctx.strokeStyle = COLORS.grid; ctx.fillStyle = COLORS.muted;
    ticks.forEach(tick => {
      ctx.beginPath(); ctx.moveTo(pad.left, tick.y); ctx.lineTo(width - pad.right, tick.y); ctx.stroke();
      ctx.fillText(formatter(tick.value), 4, tick.y + 3);
    });
    ctx.strokeStyle = COLORS.border;
    ctx.strokeRect(pad.left, pad.top, width - pad.left - pad.right, height - pad.top - pad.bottom);
  }

  function drawSessions(ctx, width, height) {
    const pad = state.pad;
    const sessionColor = { premarket: COLORS.pre, regular: COLORS.regular, closing_call: COLORS.close, aftermarket: COLORS.after };
    let start = 0;
    while (start < state.series.length) {
      const session = state.series[start].session;
      let end = start + 1;
      while (end < state.series.length && state.series[end].session === session) end += 1;
      const x1 = xFor(start, width), x2 = xFor(end - 1, width);
      ctx.fillStyle = sessionColor[session] || 'transparent';
      ctx.fillRect(x1, pad.top, Math.max(2, x2 - x1), height - pad.top - pad.bottom);
      start = end;
    }
  }

  function drawLine(ctx, width, key, scale, color, dash = null) {
    ctx.save();
    ctx.strokeStyle = color; ctx.lineWidth = 1.6;
    if (dash) ctx.setLineDash(dash);
    ctx.beginPath();
    let started = false;
    state.series.forEach((point, index) => {
      const value = num(point[key]);
      if (value === null) { started = false; return; }
      const x = xFor(index, width), y = scale(value);
      if (!started) { ctx.moveTo(x, y); started = true; } else { ctx.lineTo(x, y); }
    });
    ctx.stroke(); ctx.restore();
  }

  function drawTimes(ctx, width, height) {
    ctx.fillStyle = COLORS.muted;
    ['08:00', '09:00', '12:00', '15:30', '15:40', '19:59'].forEach(label => {
      const index = state.series.findIndex(point => point.time === label);
      if (index >= 0) ctx.fillText(label, xFor(index, width) - 13, height - 7);
    });
  }

  function renderPrice(payload) {
    const canvas = $('execution-price-canvas'); if (!canvas) return;
    const width = Math.max(980, state.series.length * 32), height = 210;
    state.width = width;
    const ctx = setupCanvas(canvas, width, height);
    drawSessions(ctx, width, height);
    const values = state.series.flatMap(p => [p.open, p.high, p.low, p.close, p.vwap, p.regular_open_line]).map(num).filter(v => v !== null);
    const [min, max] = domain(values);
    const scale = yScale(min, max, height);
    drawFrame(ctx, width, height, [min, (min + max) / 2, max].map(value => ({ value, y: scale(value) })), priceText);
    const candleWidth = 9;
    state.series.forEach((point, index) => {
      const open = num(point.open), high = num(point.high), low = num(point.low), close = num(point.close);
      if ([open, high, low, close].some(value => value === null)) return;
      const x = xFor(index, width), yOpen = scale(open), yHigh = scale(high), yLow = scale(low), yClose = scale(close);
      const up = close >= open;
      ctx.strokeStyle = up ? COLORS.red : COLORS.blue; ctx.fillStyle = up ? COLORS.red : COLORS.blue;
      ctx.beginPath(); ctx.moveTo(x, yHigh); ctx.lineTo(x, yLow); ctx.stroke();
      ctx.fillRect(x - candleWidth / 2, Math.min(yOpen, yClose), candleWidth, Math.max(1, Math.abs(yClose - yOpen)));
    });
    drawLine(ctx, width, 'vwap', scale, COLORS.gold);
    drawLine(ctx, width, 'regular_open_line', scale, COLORS.green, [5, 4]);
    drawTimes(ctx, width, height);
  }

  function renderStrength() {
    const canvas = $('execution-strength-canvas'); if (!canvas) return;
    const width = state.width, height = 130;
    const ctx = setupCanvas(canvas, width, height);
    drawSessions(ctx, width, height);
    const values = state.series.map(p => num(p.strength)).filter(v => v !== null);
    const [min, max] = domain(values, [100]);
    const scale = yScale(min, max, height);
    drawFrame(ctx, width, height, [min, 100, max].map(value => ({ value, y: scale(value) })), v => fixedText(v, 1));
    ctx.save(); ctx.strokeStyle = COLORS.gray; ctx.setLineDash([4, 4]); ctx.beginPath();
    ctx.moveTo(state.pad.left, scale(100)); ctx.lineTo(width - state.pad.right, scale(100)); ctx.stroke(); ctx.restore();
    drawLine(ctx, width, 'strength', scale, COLORS.text);
    drawTimes(ctx, width, height);
  }

  function renderVolume() {
    const canvas = $('execution-volume-canvas'); if (!canvas) return;
    const width = state.width, height = 150;
    const ctx = setupCanvas(canvas, width, height);
    drawSessions(ctx, width, height);
    const max = Math.max(1, ...state.series.map(p => num(p.total_execution_volume) || 0));
    const scale = yScale(0, max * 1.08, height);
    drawFrame(ctx, width, height, [0, max / 2, max].map(value => ({ value, y: scale(value) })), intText);
    const baseY = scale(0), barWidth = 10;
    state.series.forEach((point, index) => {
      const buy = num(point.buy_volume) || 0, sell = num(point.sell_volume) || 0, total = buy + sell;
      if (!total) return;
      const x = xFor(index, width), sellTop = scale(sell), totalTop = scale(total);
      ctx.fillStyle = COLORS.blue; ctx.fillRect(x - barWidth / 2, sellTop, barWidth, baseY - sellTop);
      ctx.fillStyle = COLORS.red; ctx.fillRect(x - barWidth / 2, totalTop, barWidth, sellTop - totalTop);
    });
    drawTimes(ctx, width, height);
  }

  function hoverText(point) {
    if (!point) return '차트 위에 마우스를 올리면 해당 분 정보를 표시합니다.';
    const buy = num(point.buy_volume), sell = num(point.sell_volume), total = num(point.total_execution_volume);
    return [
      `${point.time_full || point.time} · ${point.session}`,
      `OHLC ${priceText(point.open)} / ${priceText(point.high)} / ${priceText(point.low)} / ${priceText(point.close)}`,
      `VWAP ${priceText(point.vwap)} · 09시 시가선 ${priceText(point.regular_open_line)}`,
      `체결가 ${priceText(point.trade_price)} · 체결강도 ${fixedText(point.strength, 2)}`,
      `매수 ${buy === null ? '-' : intText(buy)} · 매도 ${sell === null ? '-' : intText(sell)} · 합계 ${total === null ? '-' : intText(total)}`,
      `분봉 거래대금 ${num(point.trade_value_eok) === null ? '-' : `${fixedText(point.trade_value_eok, 1)}억`}`
    ].join('\n');
  }

  function attachHover() {
    const hover = $('execution-chart-hover');
    ['execution-price-canvas', 'execution-strength-canvas', 'execution-volume-canvas'].forEach(id => {
      const canvas = $(id); if (!canvas) return;
      canvas.onmousemove = event => {
        const rect = canvas.getBoundingClientRect();
        const x = event.clientX - rect.left;
        const ratio = (x - state.pad.left) / Math.max(1, state.width - state.pad.left - state.pad.right);
        const index = Math.max(0, Math.min(state.series.length - 1, Math.round(ratio * (state.series.length - 1))));
        if (hover) hover.textContent = hoverText(state.series[index]);
      };
    });
  }

  function render() {
    const payload = samplePayload();
    state.series = payload.series;
    renderSummary(payload);
    renderPrice(payload); renderStrength(payload); renderVolume(payload);
    attachHover();
    const status = $('execution-chart-status');
    if (status) status.textContent = `${payload.summary.chart_points}/${payload.summary.row_count}점 샘플`;
    const hover = $('execution-chart-hover');
    if (hover) hover.textContent = `${payload.meta.stock_code} ${payload.meta.stock_name} · ${payload.summary.time_range}\n간편 샘플: 원본 663분 중 핵심 시각만 축약 표시. 전체 저장/실시간은 후속 단계입니다.`;
  }

  function init() {
    fillControls();
    $('execution-chart-reload')?.addEventListener('click', render);
    window.addEventListener('resize', render);
    render();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init); else init();
})();

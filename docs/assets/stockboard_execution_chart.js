(() => {
  'use strict';

  const INDEX_URL = 'assets/stockboard_execution_chart_index.json';
  const CACHE_BUSTER = '20260628_c';
  const PANEL_WIDTH_KEY = 'stockboard.executionChart.panelWidth.v1';
  const COLORS = {
    text: '#17202a', muted: '#5f6f80', grid: '#d8e0e7', border: '#9aa8b5',
    red: '#d71920', blue: '#1266d6', green: '#18a558', gold: '#b8860b', gray: '#7f8c8d',
    premarket: 'rgba(251,191,36,.08)', regular: 'rgba(24,165,88,.06)', closing_call: 'rgba(215,25,32,.07)', aftermarket: 'rgba(18,102,214,.06)'
  };
  const nf = new Intl.NumberFormat('ko-KR');
  const state = {
    index: null,
    payload: null,
    series: [],
    width: 980,
    pad: { left: 58, right: 18, top: 18, bottom: 24 },
    dragging: false,
  };

  const $ = id => document.getElementById(id);
  const cacheUrl = url => url.startsWith('data:') ? url : `${url}${url.includes('?') ? '&' : '?'}v=${encodeURIComponent(CACHE_BUSTER)}`;

  function num(value) {
    if (value === null || value === undefined || value === '') return null;
    if (typeof value === 'boolean') return null;
    const n = Number(String(value).replace(/,/g, ''));
    return Number.isFinite(n) ? n : null;
  }
  function positiveNum(value) {
    const n = num(value);
    return n !== null && n > 0 ? n : null;
  }
  function intText(value) {
    const n = num(value);
    return n === null ? '-' : nf.format(Math.round(n));
  }
  function fixedText(value, digits = 2) {
    const n = num(value);
    return n === null ? '-' : n.toLocaleString('ko-KR', { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }
  const priceText = intText;

  function showError(message) {
    const status = $('execution-chart-status');
    const hover = $('execution-chart-hover');
    if (status) status.textContent = '오류';
    if (hover) hover.textContent = message;
    console.warn(message);
  }
  async function fetchText(url) {
    const response = await fetch(cacheUrl(url), { cache: 'no-store' });
    if (!response.ok) throw new Error(`${url} load failed: ${response.status}`);
    return response.text();
  }
  async function fetchJson(url) {
    return JSON.parse(await fetchText(url));
  }
  async function fetchPayload(item) {
    if (Array.isArray(item.chunk_urls) && item.chunk_urls.length) {
      const chunks = await Promise.all(item.chunk_urls.map(fetchText));
      const compact = chunks.join('').replace(/\s+/g, '');
      const binary = atob(compact);
      const bytes = Uint8Array.from(binary, ch => ch.charCodeAt(0));
      return JSON.parse(new TextDecoder('utf-8').decode(bytes));
    }
    return fetchJson(item.chart_url);
  }
  function rowToObject(columns, row) {
    if (!Array.isArray(row)) return row || {};
    const out = {};
    columns.forEach((key, index) => { out[key] = row[index] ?? null; });
    return out;
  }
  function normalizePayload(payload) {
    const columns = Array.isArray(payload.columns) ? payload.columns : [];
    const rows = Array.isArray(payload.series) ? payload.series : [];
    return { ...payload, columns, series: rows.map(row => rowToObject(columns, row)) };
  }

  function dateItems() {
    const map = new Map();
    (state.index?.items || []).forEach(item => {
      if (!map.has(item.date)) map.set(item.date, item.date_label || item.date);
    });
    return Array.from(map, ([date, label]) => ({ date, label }));
  }
  function itemsForDate(date) {
    return (state.index?.items || []).filter(item => item.date === date);
  }
  function selectedItem() {
    const date = $('execution-chart-date')?.value;
    const code = $('execution-chart-code')?.value;
    return itemsForDate(date).find(item => item.stock_code === code) || itemsForDate(date)[0] || null;
  }
  function fillDateSelect() {
    const select = $('execution-chart-date');
    if (!select) return;
    select.innerHTML = '';
    dateItems().forEach(item => {
      const option = document.createElement('option');
      option.value = item.date;
      option.textContent = item.label;
      select.appendChild(option);
    });
  }
  function fillCodeSelect() {
    const select = $('execution-chart-code');
    const date = $('execution-chart-date')?.value;
    if (!select) return;
    select.innerHTML = '';
    itemsForDate(date).forEach(item => {
      const option = document.createElement('option');
      option.value = item.stock_code;
      option.textContent = `${item.stock_code} ${item.stock_name || ''}`.trim();
      select.appendChild(option);
    });
  }

  function renderSummary(payload) {
    const meta = payload.meta || {};
    const summary = payload.summary || {};
    const el = $('execution-chart-summary');
    if (!el) return;
    el.innerHTML = [
      `<span><b>종목</b> ${meta.stock_code || '-'} ${meta.stock_name || ''}</span>`,
      `<span><b>구간</b> ${summary.time_range || '-'} · ${summary.chart_points || state.series.length}/${summary.row_count || state.series.length}점</span>`,
      `<span><b>시가선</b> ${priceText(summary.regular_open_price)}</span>`,
      `<span><b>VWAP</b> ${priceText(summary.last_vwap)}</span>`,
      `<span><b>고가/저가</b> ${priceText(summary.high)} / ${priceText(summary.low)}</span>`,
      `<span><b>거래대금</b> ${fixedText(summary.total_trade_value_eok, 1)}억</span>`
    ].join('');
  }

  function setupCanvas(canvas, width, height) {
    const dpr = window.devicePixelRatio || 1;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);
    ctx.font = '10px Malgun Gothic, Arial, sans-serif';
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    return ctx;
  }
  function chartWidth(canvas) {
    const scrollWidth = canvas?.parentElement?.clientWidth || 760;
    return Math.max(760, scrollWidth, state.series.length * 4.6 + state.pad.left + state.pad.right);
  }
  function xFor(index, width) {
    const count = Math.max(1, state.series.length - 1);
    return state.pad.left + index * ((width - state.pad.left - state.pad.right) / count);
  }
  function yScale(min, max, height) {
    const span = max - min || 1;
    return value => height - state.pad.bottom - ((value - min) / span) * (height - state.pad.top - state.pad.bottom);
  }
  function domain(values, options = {}) {
    const mapFn = options.positiveOnly ? positiveNum : num;
    const valid = values.concat(options.extras || []).map(mapFn).filter(value => value !== null);
    let min = Math.min(...valid);
    let max = Math.max(...valid);
    if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
    if (min === max) {
      const b = Math.max(1, Math.abs(min) * 0.01);
      return [min - b, max + b];
    }
    const pad = Math.max(1, (max - min) * (options.padRatio ?? 0.025));
    return [min - pad, max + pad];
  }
  function uniqueTicks(values) {
    const seen = new Set();
    return values.filter(value => {
      const key = Math.round(value * 10000) / 10000;
      if (!Number.isFinite(value) || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }
  function drawFrame(ctx, width, height, ticks, formatter) {
    ctx.strokeStyle = COLORS.grid;
    ctx.fillStyle = COLORS.muted;
    ticks.forEach(tick => {
      ctx.beginPath();
      ctx.moveTo(state.pad.left, tick.y);
      ctx.lineTo(width - state.pad.right, tick.y);
      ctx.stroke();
      ctx.fillText(formatter(tick.value), 4, tick.y + 3);
    });
    ctx.strokeStyle = COLORS.border;
    ctx.strokeRect(state.pad.left, state.pad.top, width - state.pad.left - state.pad.right, height - state.pad.top - state.pad.bottom);
  }
  function drawSessions(ctx, width, height) {
    let start = 0;
    while (start < state.series.length) {
      const session = state.series[start].session;
      let end = start + 1;
      while (end < state.series.length && state.series[end].session === session) end += 1;
      ctx.fillStyle = COLORS[session] || 'transparent';
      ctx.fillRect(xFor(start, width), state.pad.top, Math.max(2, xFor(end - 1, width) - xFor(start, width)), height - state.pad.top - state.pad.bottom);
      start = end;
    }
  }
  function drawLine(ctx, width, key, scale, color, dash = null) {
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = 1.5;
    if (dash) ctx.setLineDash(dash);
    ctx.beginPath();
    let started = false;
    state.series.forEach((point, index) => {
      const value = num(point[key]);
      if (value === null) {
        started = false;
        return;
      }
      const x = xFor(index, width);
      const y = scale(value);
      if (!started) {
        ctx.moveTo(x, y);
        started = true;
      } else {
        ctx.lineTo(x, y);
      }
    });
    ctx.stroke();
    ctx.restore();
  }
  function drawTimes(ctx, width, height) {
    ctx.fillStyle = COLORS.muted;
    ['08:00', '09:00', '12:00', '15:30', '15:40', '19:59'].forEach(label => {
      const index = state.series.findIndex(point => point.time === label);
      if (index >= 0) ctx.fillText(label, xFor(index, width) - 13, height - 7);
    });
  }

  function renderPrice() {
    const canvas = $('execution-price-canvas');
    if (!canvas) return;
    const width = chartWidth(canvas);
    const height = 220;
    state.width = width;
    const ctx = setupCanvas(canvas, width, height);
    drawSessions(ctx, width, height);
    const values = state.series.flatMap(point => [point.open, point.high, point.low, point.close, point.vwap, point.regular_open_line]);
    const [min, max] = domain(values, { positiveOnly: true, padRatio: 0.018 });
    const scale = yScale(min, max, height);
    drawFrame(ctx, width, height, uniqueTicks([min, (min + max) / 2, max]).map(value => ({ value, y: scale(value) })), priceText);
    const step = Math.max(2, (width - state.pad.left - state.pad.right) / Math.max(1, state.series.length - 1));
    const candleWidth = Math.max(1, Math.min(4, step * 0.55));
    state.series.forEach((point, index) => {
      const open = positiveNum(point.open);
      const high = positiveNum(point.high);
      const low = positiveNum(point.low);
      const close = positiveNum(point.close);
      if ([open, high, low, close].some(value => value === null)) return;
      const x = xFor(index, width);
      const yOpen = scale(open);
      const yHigh = scale(high);
      const yLow = scale(low);
      const yClose = scale(close);
      const up = close >= open;
      ctx.strokeStyle = up ? COLORS.red : COLORS.blue;
      ctx.fillStyle = up ? COLORS.red : COLORS.blue;
      ctx.beginPath();
      ctx.moveTo(x, yHigh);
      ctx.lineTo(x, yLow);
      ctx.stroke();
      ctx.fillRect(x - candleWidth / 2, Math.min(yOpen, yClose), candleWidth, Math.max(1, Math.abs(yClose - yOpen)));
    });
    drawLine(ctx, width, 'vwap', scale, COLORS.gold);
    drawLine(ctx, width, 'regular_open_line', scale, COLORS.green, [5, 4]);
    drawTimes(ctx, width, height);
  }
  function renderStrength() {
    const canvas = $('execution-strength-canvas');
    if (!canvas) return;
    const width = state.width || chartWidth(canvas);
    const height = 140;
    const ctx = setupCanvas(canvas, width, height);
    drawSessions(ctx, width, height);
    const values = state.series.map(point => num(point.strength)).filter(value => value !== null);
    const [min, max] = domain(values, { extras: [100], padRatio: 0.08 });
    const scale = yScale(min, max, height);
    drawFrame(ctx, width, height, uniqueTicks([min, 100, max]).map(value => ({ value, y: scale(value) })), value => fixedText(value, 1));
    ctx.save();
    ctx.strokeStyle = COLORS.gray;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(state.pad.left, scale(100));
    ctx.lineTo(width - state.pad.right, scale(100));
    ctx.stroke();
    ctx.restore();
    drawLine(ctx, width, 'strength', scale, COLORS.text);
    drawTimes(ctx, width, height);
  }
  function renderVolume() {
    const canvas = $('execution-volume-canvas');
    if (!canvas) return;
    const width = state.width || chartWidth(canvas);
    const height = 170;
    const ctx = setupCanvas(canvas, width, height);
    drawSessions(ctx, width, height);
    const maxSell = Math.max(1, ...state.series.map(point => num(point.sell_volume) || 0));
    const maxBuy = Math.max(1, ...state.series.map(point => num(point.buy_volume) || 0));
    const maxVolume = Math.max(maxSell, maxBuy);
    const midY = Math.round((state.pad.top + height - state.pad.bottom) / 2);
    const half = Math.max(1, (height - state.pad.top - state.pad.bottom) / 2 - 3);
    const step = Math.max(2, (width - state.pad.left - state.pad.right) / Math.max(1, state.series.length - 1));
    const barWidth = Math.max(1, Math.min(4, step * 0.58));
    ctx.strokeStyle = COLORS.grid;
    ctx.fillStyle = COLORS.muted;
    [state.pad.top, midY, height - state.pad.bottom].forEach(y => {
      ctx.beginPath();
      ctx.moveTo(state.pad.left, y);
      ctx.lineTo(width - state.pad.right, y);
      ctx.stroke();
    });
    ctx.fillText(`매도 ${intText(maxVolume)}`, 4, state.pad.top + 4);
    ctx.fillText('0', 4, midY + 3);
    ctx.fillText(`매수 ${intText(maxVolume)}`, 4, height - state.pad.bottom + 3);
    ctx.strokeStyle = COLORS.border;
    ctx.strokeRect(state.pad.left, state.pad.top, width - state.pad.left - state.pad.right, height - state.pad.top - state.pad.bottom);
    ctx.strokeStyle = '#64748b';
    ctx.beginPath();
    ctx.moveTo(state.pad.left, midY);
    ctx.lineTo(width - state.pad.right, midY);
    ctx.stroke();
    state.series.forEach((point, index) => {
      const sell = num(point.sell_volume);
      const buy = num(point.buy_volume);
      const x = xFor(index, width);
      if (sell !== null && sell > 0) {
        const h = Math.max(1, sell / maxVolume * half);
        ctx.fillStyle = COLORS.blue;
        ctx.fillRect(x - barWidth / 2, midY - h, barWidth, h);
      }
      if (buy !== null && buy > 0) {
        const h = Math.max(1, buy / maxVolume * half);
        ctx.fillStyle = COLORS.red;
        ctx.fillRect(x - barWidth / 2, midY, barWidth, h);
      }
    });
    drawTimes(ctx, width, height);
  }

  function hoverText(point) {
    if (!point) return '차트 위에 마우스를 올리면 해당 분 정보를 표시합니다.';
    return [
      `${point.time_full || point.time} · ${point.session || '-'}`,
      `OHLC ${priceText(point.open)} / ${priceText(point.high)} / ${priceText(point.low)} / ${priceText(point.close)}`,
      `VWAP ${priceText(point.vwap)} · 09시 시가선 ${priceText(point.regular_open_line)}`,
      `체결가 ${priceText(point.trade_price)} · 체결강도 ${fixedText(point.strength, 2)}`,
      `매도 ${intText(point.sell_volume)} · 매수 ${intText(point.buy_volume)} · 합계 ${intText(point.total_execution_volume)}`,
      `분봉 거래대금 ${num(point.trade_value_eok) === null ? '-' : `${fixedText(point.trade_value_eok, 1)}억`}`
    ].join('\n');
  }
  function attachHover() {
    const hover = $('execution-chart-hover');
    ['execution-price-canvas', 'execution-strength-canvas', 'execution-volume-canvas'].forEach(id => {
      const canvas = $(id);
      if (!canvas) return;
      canvas.onmousemove = event => {
        const rect = canvas.getBoundingClientRect();
        const x = event.clientX - rect.left;
        const ratio = (x - state.pad.left) / Math.max(1, state.width - state.pad.left - state.pad.right);
        const index = Math.max(0, Math.min(state.series.length - 1, Math.round(ratio * (state.series.length - 1))));
        if (hover) hover.textContent = hoverText(state.series[index]);
      };
      canvas.onmouseleave = () => {
        if (!hover || !state.payload) return;
        const meta = state.payload.meta || {};
        const summary = state.payload.summary || {};
        hover.textContent = `${meta.stock_code || ''} ${meta.stock_name || ''} · ${summary.time_range || ''}\n전체 ${summary.chart_points || state.series.length}분 JSON 로드 완료. 서버/API/실시간/구글드라이브는 아직 미포함입니다.`;
      };
    });
  }
  function renderCharts() {
    if (!state.payload || !state.series.length) return;
    renderSummary(state.payload);
    renderPrice();
    renderStrength();
    renderVolume();
    attachHover();
    const meta = state.payload.meta || {};
    const summary = state.payload.summary || {};
    const status = $('execution-chart-status');
    if (status) status.textContent = `${summary.chart_points || state.series.length}/${summary.row_count || state.series.length}점 JSON`;
    const hover = $('execution-chart-hover');
    if (hover) hover.textContent = `${meta.stock_code || ''} ${meta.stock_name || ''} · ${summary.time_range || ''}\n전체 ${summary.chart_points || state.series.length}분 JSON 로드 완료. 차트 위에 마우스를 올리면 분 단위 값을 확인합니다.`;
  }
  async function loadSelectedChart() {
    const item = selectedItem();
    if (!item) return showError('선택 가능한 차트 데이터가 없습니다.');
    const status = $('execution-chart-status');
    if (status) status.textContent = '로드중';
    try {
      const payload = normalizePayload(await fetchPayload(item));
      state.payload = payload;
      state.series = payload.series;
      renderCharts();
    } catch (error) {
      showError(`차트 JSON 로드 실패: ${error.message}`);
    }
  }
  async function loadIndex() {
    try {
      state.index = await fetchJson(INDEX_URL);
      fillDateSelect();
      fillCodeSelect();
      await loadSelectedChart();
    } catch (error) {
      showError(`차트 index 로드 실패: ${error.message}`);
    }
  }

  function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }
  function applyPanelWidth(width) {
    const shell = document.querySelector('.sample-shell');
    if (!shell) return;
    const maxWidth = Math.max(360, Math.min(900, window.innerWidth * 0.72));
    const clamped = Math.round(clamp(width, 360, maxWidth));
    shell.style.setProperty('--chart-panel-width', `${clamped}px`);
    try { localStorage.setItem(PANEL_WIDTH_KEY, String(clamped)); } catch (_) {}
    window.requestAnimationFrame(renderCharts);
  }
  function restorePanelWidth() {
    try {
      const stored = Number(localStorage.getItem(PANEL_WIDTH_KEY));
      if (Number.isFinite(stored) && stored > 0) applyPanelWidth(stored);
    } catch (_) {}
  }
  function setupResizer() {
    const resizer = $('sample-resizer');
    const shell = document.querySelector('.sample-shell');
    if (!resizer || !shell) return;
    resizer.addEventListener('pointerdown', event => {
      if (window.matchMedia('(max-width: 1280px)').matches) return;
      state.dragging = true;
      resizer.setPointerCapture(event.pointerId);
      document.body.classList.add('resizing-layout');
      event.preventDefault();
    });
    resizer.addEventListener('pointermove', event => {
      if (!state.dragging) return;
      const rect = shell.getBoundingClientRect();
      applyPanelWidth(rect.right - event.clientX - 4);
    });
    function stopDrag(event) {
      if (!state.dragging) return;
      state.dragging = false;
      try { resizer.releasePointerCapture(event.pointerId); } catch (_) {}
      document.body.classList.remove('resizing-layout');
    }
    resizer.addEventListener('pointerup', stopDrag);
    resizer.addEventListener('pointercancel', stopDrag);
  }
  function initEvents() {
    $('execution-chart-date')?.addEventListener('change', () => { fillCodeSelect(); loadSelectedChart(); });
    $('execution-chart-code')?.addEventListener('change', loadSelectedChart);
    $('execution-chart-reload')?.addEventListener('click', loadSelectedChart);
    window.addEventListener('resize', () => window.requestAnimationFrame(renderCharts));
    setupResizer();
    restorePanelWidth();
  }
  function init() {
    initEvents();
    loadIndex();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();

(function () {
  "use strict";

  function ensureTooltipElement(className) {
    const element = document.createElement('div');
    element.className = className;
    element.setAttribute('role', 'tooltip');
    document.body.appendChild(element);
    return element;
  }

  function setTooltipContent(element, content) {
    if (!element) return;
    element.textContent = content || '';
  }

  function positionTooltipElement(element, event) {
    if (!element || !event) return;
    const margin = 12;
    element.style.left = `${event.clientX + margin}px`;
    element.style.top = `${event.clientY + margin}px`;
    const rect = element.getBoundingClientRect();
    const left = Math.min(event.clientX + margin, window.innerWidth - rect.width - margin);
    const top = Math.min(event.clientY + margin, window.innerHeight - rect.height - margin);
    element.style.left = `${Math.max(margin, left)}px`;
    element.style.top = `${Math.max(margin, top)}px`;
  }

  function showTooltipElement(element) {
    if (!element) return;
    element.classList.add('visible');
  }

  function hideTooltipElement(element) {
    if (!element) return;
    element.classList.remove('visible');
  }

  function installInjectedStyles() {
    if (document.getElementById('stockboard-tooltip-runtime-style')) return;
    const style = document.createElement('style');
    style.id = 'stockboard-tooltip-runtime-style';
    style.textContent = `
      @keyframes stockboard-server-down-blink { 0%, 100% { opacity: 1; } 50% { opacity: .25; } }
      body.stockboard-server-down #combined-status-lamp,
      body.stockboard-server-down #api-status-lamp,
      body.stockboard-server-down #web-status-lamp {
        background: #d71920 !important;
        box-shadow: 0 0 10px #d71920 !important;
        animation: stockboard-server-down-blink .65s step-end infinite;
      }
      body.stockboard-server-down #refresh-delay,
      body.stockboard-server-down .lane-actual-speed,
      body.stockboard-server-down .lane-speed-badge {
        color: #d71920 !important;
        font-weight: 900 !important;
      }
      body.stockboard-server-down .lane-speed-badge {
        border-color: #dc8b8b !important;
        background: #fff0f0 !important;
      }
      #candidate-board th.stockboard-sortable-header,
      #top20-board th.stockboard-sortable-header,
      #top50-board th.stockboard-sortable-header {
        cursor: pointer;
        user-select: none;
      }
      #candidate-board th.stockboard-sortable-header[data-sort-dir="asc"]::after,
      #top20-board th.stockboard-sortable-header[data-sort-dir="asc"]::after,
      #top50-board th.stockboard-sortable-header[data-sort-dir="asc"]::after {
        content: " ▲";
        color: #1d4ed8;
      }
      #candidate-board th.stockboard-sortable-header[data-sort-dir="desc"]::after,
      #top20-board th.stockboard-sortable-header[data-sort-dir="desc"]::after,
      #top50-board th.stockboard-sortable-header[data-sort-dir="desc"]::after {
        content: " ▼";
        color: #b91c1c;
      }
      #top20-board td:nth-child(4),
      #top50-board td:nth-child(4),
      #top20-board .stock-name,
      #top50-board .stock-name {
        text-align: left !important;
      }
      #top20-board td:nth-child(5),
      #top20-board td:nth-child(6),
      #top20-board td:nth-child(7),
      #top20-board .number-cell,
      #top20-board .flow-number,
      #top20-board .amount-ratio-cell,
      #top50-board td:nth-child(5),
      #top50-board td:nth-child(6),
      #top50-board td:nth-child(7),
      #top50-board .number-cell,
      #top50-board .flow-number,
      #top50-board .amount-ratio-cell,
      .amount-ratio-cell {
        text-align: right !important;
        font-variant-numeric: tabular-nums;
      }
      .amount-ratio-number {
        display: inline-block;
        width: 100%;
        text-align: right;
        font: inherit;
        font-weight: 700;
        line-height: inherit;
      }
      .amount-ratio-number.amount-ratio-strong { color: var(--red) !important; }
      .amount-ratio-number.amount-ratio-weak { color: var(--blue) !important; }
      .amount-ratio-number.amount-ratio-missing { color: #4b5563 !important; }
    `;
    document.head.appendChild(style);
  }

  function installServerDisconnectGuard() {
    const idsToFreeze = [
      'refresh-delay',
      'hot-lane-speed-badge', 'hot-actual-speed',
      'candidate-lane-speed-badge',
      'top20-lane-speed-badge', 'top20-actual-speed',
      'top50-lane-speed-badge', 'top50-actual-speed',
      'top100-lane-speed-badge', 'top100-actual-speed'
    ];

    function byId(id) {
      return document.getElementById(id);
    }

    function remember(element) {
      if (!element || element.dataset.serverGuardSaved === '1') return;
      element.dataset.serverGuardSaved = '1';
      element.dataset.serverGuardText = element.textContent || '';
      element.dataset.serverGuardClass = element.className || '';
      element.dataset.serverGuardTitle = element.getAttribute('title') || '';
    }

    function freezeServerDown() {
      document.body.classList.add('stockboard-server-down');
      const lamp = byId('combined-status-lamp');
      if (lamp) {
        remember(lamp);
        lamp.className = 'lamp combined-lamp error';
        lamp.setAttribute('aria-label', '서버 끊김 / 실시간 아님');
        lamp.setAttribute('title', '서버 끊김 / 실시간 아님');
      }
      const delay = byId('refresh-delay');
      if (delay) {
        remember(delay);
        delay.textContent = '서버 끊김';
        delay.setAttribute('aria-label', '서버 끊김 / 실시간 아님');
      }
      idsToFreeze.forEach((id) => {
        const element = byId(id);
        if (!element) return;
        remember(element);
        if (id.endsWith('actual-speed')) {
          element.textContent = '실제 - / 서버 끊김';
        } else if (id !== 'refresh-delay') {
          element.textContent = '서버 끊김';
          element.classList.remove('lane-speed-fast', 'lane-speed-medium', 'lane-speed-slow', 'lane-speed-protect', 'lane-speed-waiting');
          element.classList.add('lane-speed-slow');
        }
        element.setAttribute('title', '서버 끊김 / 표시 속도는 실시간 속도가 아님');
      });
    }

    function restoreServerOk() {
      if (!document.body.classList.contains('stockboard-server-down')) return;
      document.body.classList.remove('stockboard-server-down');
      idsToFreeze.concat(['combined-status-lamp']).forEach((id) => {
        const element = byId(id);
        if (!element || element.dataset.serverGuardSaved !== '1') return;
        element.textContent = element.dataset.serverGuardText || element.textContent;
        element.className = element.dataset.serverGuardClass || element.className;
        const title = element.dataset.serverGuardTitle || '';
        if (title) element.setAttribute('title', title); else element.removeAttribute('title');
        delete element.dataset.serverGuardSaved;
        delete element.dataset.serverGuardText;
        delete element.dataset.serverGuardClass;
        delete element.dataset.serverGuardTitle;
      });
    }

    async function checkServer() {
      const controller = new AbortController();
      const timer = window.setTimeout(() => controller.abort(), 1800);
      try {
        const response = await fetch(`/api/health?ts=${Date.now()}`, {
          cache: 'no-store',
          signal: controller.signal
        });
        window.clearTimeout(timer);
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        restoreServerOk();
      } catch (_error) {
        window.clearTimeout(timer);
        freezeServerDown();
      }
    }

    checkServer();
    window.setInterval(checkServer, 1000);
  }

  function installMarketSupplyGraphFirst() {
    const row = document.querySelector('.market-supply-row');
    if (!row) return;
    const graphBlock = row.querySelector('.market-distribution');
    const supplyTable = row.querySelector('table.grid.market');
    if (!graphBlock || !supplyTable) return;
    if (row.firstElementChild !== graphBlock) row.insertBefore(graphBlock, supplyTable);
  }

  const TOP_SORT_TABLE_IDS = ['candidate-board', 'top20-board', 'top50-board'];
  const topSortState = new Map();
  let lastSortEventKey = '';
  let lastSortEventUntil = 0;
  let reapplyQueued = false;

  function topSortTables() {
    return TOP_SORT_TABLE_IDS.map(id => document.getElementById(id)).filter(Boolean);
  }

  function markTopSortHeaders() {
    topSortTables().forEach(table => {
      Array.from(table.querySelectorAll('th')).forEach(header => {
        header.classList.add('stockboard-sortable-header');
      });
    });
  }

  function normalizeSortText(text) {
    return String(text || '')
      .replace(/[↑↓▲▼]/g, '')
      .replace(/,/g, '')
      .replace(/%/g, '')
      .replace(/x\+?/gi, '')
      .trim();
  }

  function sortCellValue(row, columnIndex) {
    const text = normalizeSortText(row.children[columnIndex]?.textContent || '');
    const numeric = text.replace(/[^0-9+\-.]/g, '');
    if (numeric && /^[-+]?\d+(?:\.\d+)?$/.test(numeric)) {
      const value = Number(numeric);
      if (Number.isFinite(value)) return { type: 'number', value };
    }
    return { type: 'text', value: text.toLowerCase() };
  }

  function dataRows(table) {
    const body = table.tBodies && table.tBodies[0] ? table.tBodies[0] : table;
    return Array.from(body.querySelectorAll('tr')).filter(row => !row.querySelector('th') && row.children.length > 1);
  }

  function applyTopSort(table) {
    const state = topSortState.get(table.id);
    if (!state) return;
    const rows = dataRows(table);
    if (rows.length <= 1) return;
    rows.sort((left, right) => {
      const leftValue = sortCellValue(left, state.columnIndex);
      const rightValue = sortCellValue(right, state.columnIndex);
      let result = 0;
      if (leftValue.type === 'number' && rightValue.type === 'number') {
        result = leftValue.value - rightValue.value;
      } else {
        result = String(leftValue.value).localeCompare(String(rightValue.value), 'ko-KR', { numeric: true });
      }
      return state.direction === 'asc' ? result : -result;
    });
    const body = table.tBodies && table.tBodies[0] ? table.tBodies[0] : table;
    rows.forEach(row => body.appendChild(row));
    Array.from(table.querySelectorAll('th')).forEach((header, index) => {
      header.classList.add('stockboard-sortable-header');
      if (index === state.columnIndex) header.dataset.sortDir = state.direction;
      else delete header.dataset.sortDir;
    });
  }

  function reapplyTopSorts() {
    reapplyQueued = false;
    markTopSortHeaders();
    topSortTables().forEach(applyTopSort);
  }

  function queueReapplyTopSorts() {
    if (reapplyQueued) return;
    reapplyQueued = true;
    window.requestAnimationFrame(reapplyTopSorts);
  }

  function eventHeader(event) {
    const target = event.target;
    const header = target && target.closest ? target.closest('th') : null;
    if (!header || target.closest?.('.column-resizer')) return null;
    const table = header.closest('table');
    if (!table || !TOP_SORT_TABLE_IDS.includes(table.id)) return null;
    const columnIndex = Array.from(header.parentElement.children || []).indexOf(header);
    if (columnIndex < 0) return null;
    return { table, header, columnIndex };
  }

  function handleTopHeaderSort(event) {
    const hit = eventHeader(event);
    if (!hit) return;
    const eventKey = `${hit.table.id}:${hit.columnIndex}`;
    const now = performance.now();
    if (event.type !== 'pointerdown' && event.type !== 'mousedown' && eventKey === lastSortEventKey && now < lastSortEventUntil) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    const current = topSortState.get(hit.table.id);
    const direction = current && current.columnIndex === hit.columnIndex && current.direction === 'asc' ? 'desc' : 'asc';
    topSortState.set(hit.table.id, { columnIndex: hit.columnIndex, direction });
    applyTopSort(hit.table);
    lastSortEventKey = eventKey;
    lastSortEventUntil = now + 450;
  }

  function installBoardHeaderSort() {
    ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(eventName => {
      window.addEventListener(eventName, handleTopHeaderSort, true);
      document.addEventListener(eventName, handleTopHeaderSort, true);
    });
    markTopSortHeaders();
    const observer = new MutationObserver(queueReapplyTopSorts);
    if (document.body) observer.observe(document.body, { childList: true, subtree: true });
    window.setInterval(queueReapplyTopSorts, 1000);
  }

  const AMOUNT_RATIO_COLUMN_INDEX = 11;
  const amountRatioPrevByCode = new Map();
  let amountRatioLoading = false;
  let amountRatioApplyQueued = false;

  function parseNumber(value) {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(String(value).replace(/,/g, '').replace(/%/g, '').replace(/x/g, '').trim());
    return Number.isFinite(number) ? number : null;
  }

  function stockCodeFromRow(row) {
    let code = String(row?.dataset?.stockCode || row?.dataset?.stockcode || '').trim();
    code = code.toUpperCase().replace(/^A(?=\d{6}$)/, '').replace(/_AL$/, '').replace(/_NX$/, '');
    return /^\d{6}$/.test(code) ? code : '';
  }

  function top100ModelForRatio() {
    const selector = document.getElementById('candidate-model-selector');
    return selector?.value || '';
  }

  function rankModeForRatio() {
    try {
      return localStorage.getItem('stockboard.rankMode.v1') || 'auto';
    } catch (_error) {
      return 'auto';
    }
  }

  function amountRatioUrl() {
    const params = new URLSearchParams();
    const model = top100ModelForRatio();
    const rankMode = rankModeForRatio();
    if (model) params.set('candidate_model', model);
    if (rankMode) params.set('rank_mode', rankMode);
    params.set('ts', String(Date.now()));
    return `/api/top100?${params.toString()}`;
  }

  function firstRowValue(row, keys) {
    for (const key of keys) {
      const value = row && row[key];
      if (value !== null && value !== undefined && value !== '') return value;
    }
    return null;
  }

  async function loadAmountRatioBase() {
    if (amountRatioLoading) return;
    amountRatioLoading = true;
    try {
      const response = await fetch(amountRatioUrl(), { cache: 'no-store' });
      if (!response.ok) throw new Error(`amount ratio top100 failed: ${response.status}`);
      const rows = await response.json();
      if (!Array.isArray(rows)) return;
      rows.forEach(row => {
        const code = String(firstRowValue(row, ['stock_code', 'code']) || '').replace(/^A(?=\d{6}$)/, '').replace(/_AL$/, '').replace(/_NX$/, '');
        const prev = parseNumber(firstRowValue(row, [
          'prev_trade_value_eok',
          'previous_trade_value_eok',
          'yesterday_trade_value_eok',
          'prevTradeValueEok',
          'previousTradeValueEok'
        ]));
        if (/^\d{6}$/.test(code) && prev && prev > 0) amountRatioPrevByCode.set(code, prev);
      });
      queueApplyAmountRatio();
    } catch (error) {
      console.warn(error);
    } finally {
      amountRatioLoading = false;
    }
  }

  function setAmountRatioHeaders() {
    TOP_SORT_TABLE_IDS.concat(['trading-board']).forEach(id => {
      const table = document.getElementById(id);
      if (!table) return;
      const header = table.querySelector(`th:nth-child(${AMOUNT_RATIO_COLUMN_INDEX + 1})`);
      if (!header) return;
      const label = header.querySelector('.column-label');
      if (label && label.firstChild) label.firstChild.nodeValue = '대금비';
      else if (label) label.textContent = '대금비';
      else header.textContent = '대금비';
      header.dataset.amountRatioHeader = '1';
    });
  }

  function amountRatioTextClass(ratio) {
    if (!Number.isFinite(ratio) || ratio <= 0) return 'amount-ratio-number amount-ratio-missing';
    return ratio >= 1 ? 'amount-ratio-number amount-ratio-strong' : 'amount-ratio-number amount-ratio-weak';
  }

  function amountRatioCellHtml(ratio) {
    if (!Number.isFinite(ratio) || ratio <= 0) return '<span class="amount-ratio-number amount-ratio-missing">-</span>';
    const display = ratio >= 10 ? '10x+' : `${ratio.toFixed(ratio >= 3 ? 1 : 2)}x`;
    return `<span class="${amountRatioTextClass(ratio)}">${display}</span>`;
  }

  function applyAmountRatioToRow(row) {
    const code = stockCodeFromRow(row);
    if (!code) return;
    const prev = amountRatioPrevByCode.get(code);
    const current = parseNumber(row.children[6]?.textContent);
    const cell = row.children[AMOUNT_RATIO_COLUMN_INDEX];
    if (!cell) return;
    const ratio = prev && current !== null ? current / prev : null;
    const html = amountRatioCellHtml(ratio);
    if (cell.dataset.amountRatioHtml !== html) {
      cell.innerHTML = html;
      cell.dataset.amountRatioHtml = html;
    }
    cell.className = 'amount-ratio-cell number-cell';
    cell.style.setProperty('text-align', 'right', 'important');
    const value = cell.querySelector('.amount-ratio-number');
    if (value) {
      if (ratio && Number.isFinite(ratio) && ratio >= 1) value.style.setProperty('color', 'var(--red)', 'important');
      else if (ratio && Number.isFinite(ratio)) value.style.setProperty('color', 'var(--blue)', 'important');
      else value.style.setProperty('color', '#4b5563', 'important');
    }
    const tooltip = [
      '대금비 = 당일 거래대금 / 전일 거래대금',
      `당일 거래대금: ${current === null ? '-' : current.toLocaleString('en-US')}억`,
      `전일 거래대금: ${prev ? prev.toLocaleString('en-US', { maximumFractionDigits: 2 }) + '억' : '-'}`,
      `대금비: ${ratio && Number.isFinite(ratio) ? ratio.toFixed(2) + 'x' : '-'}`
    ].join('\n');
    cell.dataset.tooltip = tooltip;
    cell.setAttribute('aria-label', tooltip);
  }

  function applyAmountRatio() {
    setAmountRatioHeaders();
    TOP_SORT_TABLE_IDS.concat(['selected-board', 'trading-board']).forEach(id => {
      const table = document.getElementById(id);
      if (!table) return;
      table.querySelectorAll('tr[data-stock-code], tr[data-stockcode]').forEach(applyAmountRatioToRow);
    });
  }

  function queueApplyAmountRatio() {
    if (amountRatioApplyQueued) return;
    amountRatioApplyQueued = true;
    window.requestAnimationFrame(() => {
      amountRatioApplyQueued = false;
      applyAmountRatio();
    });
  }

  function installAmountRatioColumn() {
    loadAmountRatioBase();
    window.setInterval(loadAmountRatioBase, 60000);
    window.setInterval(queueApplyAmountRatio, 1000);
    const observer = new MutationObserver(queueApplyAmountRatio);
    if (document.body) observer.observe(document.body, { childList: true, subtree: true });
    queueApplyAmountRatio();
  }

  function installTop5ArrowNavigation() {
    const NAV_TABLE_IDS = ['candidate-board', 'top20-board', 'top50-board', 'trading-board'];
    const STOCK_CODE_PATTERN = /^\d{6}$/;

    function normalizeCode(value) {
      let text = String(value || '').trim().toUpperCase();
      if (text.startsWith('A') && text.length === 7) text = text.slice(1);
      text = text.replace('_AL', '').replace('_NX', '');
      return STOCK_CODE_PATTERN.test(text) ? text : null;
    }

    function codeFromNode(node) {
      let current = node;
      while (current && current !== document.body) {
        const data = current.dataset || {};
        const code = normalizeCode(data.stockCode || data.stockcode || data.code || data.stock_code || data.stockNameCode);
        if (code) return code;
        current = current.parentElement;
      }
      return null;
    }

    function visibleRows() {
      const rows = [];
      NAV_TABLE_IDS.forEach(id => {
        const table = document.getElementById(id);
        if (!table) return;
        Array.from(table.querySelectorAll('tr')).forEach(row => {
          if (row.querySelector('th') || row.offsetParent === null) return;
          const code = codeFromNode(row);
          if (code) rows.push({ row, code, tableId: id });
        });
      });
      return rows;
    }

    function isEditableTarget(target) {
      return Boolean(target && target.closest && target.closest('input, textarea, select, [contenteditable="true"], [contenteditable=""]'));
    }

    document.addEventListener('keydown', event => {
      if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
      if (isEditableTarget(event.target)) return;
      const selection = window.StockBoardSelection;
      const activeCode = normalizeCode(selection && selection.activeStockCode);
      if (!selection || !activeCode || typeof selection.activateStock !== 'function') return;
      const rows = visibleRows();
      const index = rows.findIndex(item => item.code === activeCode);
      const next = rows[index + (event.key === 'ArrowUp' ? -1 : 1)];
      if (!next) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      selection.activateStock(next.code, { scroll: true });
    }, true);
  }

  function installUiHotfixes() {
    installInjectedStyles();
    installServerDisconnectGuard();
    installMarketSupplyGraphFirst();
    installBoardHeaderSort();
    installAmountRatioColumn();
    installTop5ArrowNavigation();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', installUiHotfixes, { once: true });
  } else {
    installUiHotfixes();
  }

  window.StockBoardTooltip = Object.assign(window.StockBoardTooltip || {}, {
    ensureTooltipElement,
    setTooltipContent,
    positionTooltipElement,
    showTooltipElement,
    hideTooltipElement
  });
})();

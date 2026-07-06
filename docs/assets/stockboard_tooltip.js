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

  function installServerDisconnectGuard() {
    const style = document.createElement('style');
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
      #top20-board .stock-name,
      #top50-board .stock-name {
        text-align: left !important;
      }
      #top20-board td:nth-child(5),
      #top20-board td:nth-child(6),
      #top20-board td:nth-child(7),
      #top50-board td:nth-child(5),
      #top50-board td:nth-child(6),
      #top50-board td:nth-child(7) {
        text-align: right !important;
        font-variant-numeric: tabular-nums;
      }
      .enhanced-board th.stockboard-sortable-header {
        cursor: pointer;
        user-select: none;
      }
      .enhanced-board th.stockboard-sortable-header[data-sort-dir="asc"]::after {
        content: " ▲";
        color: #1d4ed8;
      }
      .enhanced-board th.stockboard-sortable-header[data-sort-dir="desc"]::after {
        content: " ▼";
        color: #b91c1c;
      }
      .amount-ratio-cell {
        padding: 2px 4px !important;
      }
      .amount-ratio-box {
        position: relative;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 100%;
        min-width: 0;
        height: calc(var(--metric-height, 26px) - 2px);
        overflow: hidden;
        border: 1px solid #c5cdd4;
        border-radius: 2px;
        background: #edf1f4;
        font-variant-numeric: tabular-nums;
      }
      .amount-ratio-fill {
        position: absolute;
        inset: 0 auto 0 0;
        width: var(--amount-ratio-width, 0%);
        background: var(--amount-ratio-color, #cbd5e1);
        opacity: .72;
      }
      .amount-ratio-value {
        position: relative;
        z-index: 1;
        width: 100%;
        text-align: center;
        color: #17202a;
        font-weight: 800;
        text-shadow: 0 0 2px #fff, 0 0 2px #fff;
      }
      .amount-ratio-missing {
        color: #7f8c8d;
      }
    `;
    document.head.appendChild(style);

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
    if (row.firstElementChild !== graphBlock) {
      row.insertBefore(graphBlock, supplyTable);
    }
  }

  const SORT_TABLE_IDS = ['candidate-board', 'top20-board', 'top50-board', 'trading-board'];
  const sortState = new Map();

  function markSortHeaders() {
    SORT_TABLE_IDS.forEach((id) => {
      const table = document.getElementById(id);
      if (!table) return;
      Array.from(table.querySelectorAll('th')).forEach((th) => {
        th.classList.add('stockboard-sortable-header');
      });
    });
  }

  function normalizeSortText(text) {
    return String(text || '').replace(/[↑↓▲▼]/g, '').replace(/,/g, '').replace(/%/g, '').trim();
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

  function applySort(table, columnIndex, direction) {
    const body = table.tBodies && table.tBodies[0] ? table.tBodies[0] : table;
    const rows = Array.from(body.querySelectorAll('tr')).filter((row) => !row.querySelector('th') && row.children.length > 1);
    if (rows.length <= 1) return;
    rows.sort((left, right) => {
      const l = sortCellValue(left, columnIndex);
      const r = sortCellValue(right, columnIndex);
      let result = 0;
      if (l.type === 'number' && r.type === 'number') result = l.value - r.value;
      else result = String(l.value).localeCompare(String(r.value), 'ko-KR', { numeric: true });
      return direction === 'asc' ? result : -result;
    });
    rows.forEach((row) => body.appendChild(row));
    Array.from(table.querySelectorAll('th')).forEach((th, index) => {
      th.classList.add('stockboard-sortable-header');
      if (index === columnIndex) {
        th.dataset.sortDir = direction;
      } else {
        delete th.dataset.sortDir;
      }
    });
  }

  function installBoardHeaderSort() {
    document.addEventListener('click', (event) => {
      const th = event.target?.closest?.('th');
      if (!th || event.target?.closest?.('.column-resizer')) return;
      const table = th.closest('table');
      if (!table || !SORT_TABLE_IDS.includes(table.id)) return;
      const columnIndex = Array.from(th.parentElement.children || []).indexOf(th);
      if (columnIndex < 0) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      const current = sortState.get(table.id);
      const direction = current && current.columnIndex === columnIndex && current.direction === 'asc' ? 'desc' : 'asc';
      sortState.set(table.id, { columnIndex, direction });
      applySort(table, columnIndex, direction);
    }, true);

    markSortHeaders();
    const observer = new MutationObserver(markSortHeaders);
    if (document.body) observer.observe(document.body, { childList: true, subtree: true });
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
      rows.forEach((row) => {
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
    SORT_TABLE_IDS.forEach((id) => {
      const table = document.getElementById(id);
      if (!table) return;
      const header = table.querySelector(`th:nth-child(${AMOUNT_RATIO_COLUMN_INDEX + 1})`);
      if (!header) return;
      const label = header.querySelector('.column-label');
      if (label && label.firstChild) {
        label.firstChild.nodeValue = '대금비';
      } else if (label) {
        label.textContent = '대금비';
      } else {
        header.textContent = '대금비';
      }
      header.dataset.amountRatioHeader = '1';
    });
  }

  function ratioTone(ratio) {
    if (ratio >= 1.2) return 'var(--red)';
    if (ratio <= 0.8) return 'var(--blue)';
    return '#94a3b8';
  }

  function amountRatioCellHtml(ratio) {
    if (!Number.isFinite(ratio) || ratio <= 0) {
      return '<span class="amount-ratio-missing">-</span>';
    }
    const display = ratio >= 10 ? '10x+' : `${ratio.toFixed(ratio >= 3 ? 1 : 2)}x`;
    const width = Math.max(3, Math.min(100, ratio / 3 * 100));
    return `<div class="amount-ratio-box" style="--amount-ratio-width:${width.toFixed(1)}%;--amount-ratio-color:${ratioTone(ratio)}"><span class="amount-ratio-fill"></span><span class="amount-ratio-value">${display}</span></div>`;
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
    cell.className = 'amount-ratio-cell visual-cell';
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
    SORT_TABLE_IDS.concat(['selected-board']).forEach((id) => {
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
    if (document.body) observer.observe(document.body, { childList: true, subtree: true, characterData: true });
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
      NAV_TABLE_IDS.forEach((id) => {
        const table = document.getElementById(id);
        if (!table) return;
        Array.from(table.querySelectorAll('tr')).forEach((row) => {
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

    document.addEventListener('keydown', (event) => {
      if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
      if (isEditableTarget(event.target)) return;
      const selection = window.StockBoardSelection;
      const activeCode = normalizeCode(selection && selection.activeStockCode);
      if (!selection || !activeCode || typeof selection.activateStock !== 'function') return;
      const rows = visibleRows();
      const index = rows.findIndex((item) => item.code === activeCode);
      const next = rows[index + (event.key === 'ArrowUp' ? -1 : 1)];
      if (!next) return;
      event.preventDefault();
      event.stopImmediatePropagation();
      selection.activateStock(next.code, { scroll: true });
    }, true);
  }

  function installUiHotfixes() {
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

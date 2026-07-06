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

  const MODEL_PRIMARY_KEY = 'stockboard.candidateModelId.v1';
  const MODEL_LEGACY_KEY = 'stockboard.candidateModel.v1';
  const MODEL_FORCE_KEY = 'stockboard.candidateModel.force.v2';
  const MODEL_ITEMS = [
    ['NET_BUY_STRENGTH_V02', '순매수 강도 v0.2'],
    ['NET_BUY_STRENGTH_V01', '순매수 강도 v0.1'],
    ['TVRANK_A_V03_TEMP', '현재 하드코딩 기준'],
    ['OPENING_MONEY_FLOW_V01', '돈쏠림 시작형'],
    ['OPENING_ACCUMULATION_V01', '조용한 매집형'],
    ['OPENING_BURST_V01', '폭발 확인형'],
    ['PROGRAM_FLOW_V01', '프로그램 동행형'],
    ['RELATIVE_STRENGTH_OPENING_V01', '시장대비 강도형']
  ];
  const MODEL_IDS = new Set(MODEL_ITEMS.map(([id]) => id));
  const MODEL_LABEL_TO_ID = new Map(MODEL_ITEMS.map(([id, label]) => [label.replace(/\s+/g, ''), id]));
  const LEGACY_FIX_IDS = new Set(['NET_BUY_STRENGTH_V01', 'TVRANK_A_V03_TEMP']);
  const nativeStorageGetItem = window.Storage && window.Storage.prototype.getItem;
  const nativeStorageSetItem = window.Storage && window.Storage.prototype.setItem;

  function normalizeModelId(value) {
    const text = String(value || '').trim();
    if (MODEL_IDS.has(text)) return text;
    return MODEL_LABEL_TO_ID.get(text.replace(/\s+/g, '')) || '';
  }

  function rawStorageGet(key) {
    try {
      return nativeStorageGetItem ? nativeStorageGetItem.call(localStorage, key) : localStorage.getItem(key);
    } catch (_error) {
      return '';
    }
  }

  function rawStorageSet(key, value) {
    try {
      if (nativeStorageSetItem) nativeStorageSetItem.call(localStorage, key, value);
      else localStorage.setItem(key, value);
    } catch (_error) {
      // Storage can be unavailable in restricted environments.
    }
  }

  function preferredStoredModelId() {
    const forced = normalizeModelId(rawStorageGet(MODEL_FORCE_KEY));
    if (forced) return forced;
    const primary = normalizeModelId(rawStorageGet(MODEL_PRIMARY_KEY));
    const legacy = normalizeModelId(rawStorageGet(MODEL_LEGACY_KEY));
    if (primary === 'NET_BUY_STRENGTH_V02' && legacy && legacy !== primary && LEGACY_FIX_IDS.has(legacy)) {
      return legacy;
    }
    return primary || legacy || '';
  }

  function writeStoredModel(modelId) {
    const normalized = normalizeModelId(modelId);
    if (!normalized) return '';
    rawStorageSet(MODEL_FORCE_KEY, normalized);
    rawStorageSet(MODEL_PRIMARY_KEY, normalized);
    rawStorageSet(MODEL_LEGACY_KEY, normalized);
    return normalized;
  }

  function patchCandidateModelStorage() {
    if (!nativeStorageGetItem || window.__stockboardCandidateModelStoragePatched) return;
    window.__stockboardCandidateModelStoragePatched = true;
    window.Storage.prototype.getItem = function patchedGetItem(key) {
      if (this === localStorage && (key === MODEL_PRIMARY_KEY || key === MODEL_LEGACY_KEY)) {
        return preferredStoredModelId() || nativeStorageGetItem.call(this, key);
      }
      return nativeStorageGetItem.call(this, key);
    };
  }

  function modelSelector() {
    return document.getElementById('candidate-model-selector');
  }

  function optionForModel(selector, modelId) {
    return Array.from(selector?.options || []).find(option => option.value === modelId) || null;
  }

  function ensureModelOptions(selector) {
    if (!selector) return;
    MODEL_ITEMS.forEach(([id, label]) => {
      let option = optionForModel(selector, id);
      if (!option) {
        option = document.createElement('option');
        option.value = id;
        selector.appendChild(option);
      }
      option.textContent = label;
      option.disabled = false;
    });
  }

  function currentModelId(selector) {
    return normalizeModelId(selector?.value)
      || normalizeModelId(selector?.selectedOptions?.[0]?.textContent);
  }

  function reconcileModelSelection(options = {}) {
    const selector = modelSelector();
    if (!selector) return '';
    ensureModelOptions(selector);
    const stored = preferredStoredModelId();
    const current = currentModelId(selector);
    const modelId = stored || current;
    if (!modelId || !optionForModel(selector, modelId)) return '';
    const changed = selector.value !== modelId;
    selector.value = modelId;
    writeStoredModel(modelId);
    if (changed && options.dispatchChange) {
      selector.dispatchEvent(new Event('change', { bubbles: true }));
    }
    return modelId;
  }

  function installModelPersistenceGuard() {
    patchCandidateModelStorage();
    const selector = modelSelector();
    if (!selector) return;
    ensureModelOptions(selector);
    reconcileModelSelection({ dispatchChange: false });
    if (selector.dataset.modelGuardInstalled !== '1') {
      selector.dataset.modelGuardInstalled = '1';
      ['pointerdown', 'mousedown', 'click', 'input', 'change', 'blur', 'keyup'].forEach(eventName => {
        selector.addEventListener(eventName, () => {
          const modelId = currentModelId(selector);
          if (modelId) writeStoredModel(modelId);
        }, true);
      });
      window.addEventListener('pagehide', () => {
        const modelId = currentModelId(selector);
        if (modelId) writeStoredModel(modelId);
      }, true);
    }
    [0, 25, 100, 300, 800, 1600, 3000].forEach(delay => {
      window.setTimeout(() => reconcileModelSelection({ dispatchChange: delay >= 300 }), delay);
    });
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
      body.stockboard-server-down .lane-speed-badge { color: #d71920 !important; font-weight: 900 !important; }
      body.stockboard-server-down .lane-speed-badge { border-color: #dc8b8b !important; background: #fff0f0 !important; }
      #top20-board .stock-name,#top50-board .stock-name { text-align: left !important; }
      #top20-board td:nth-child(5),#top20-board td:nth-child(6),#top20-board td:nth-child(7),
      #top50-board td:nth-child(5),#top50-board td:nth-child(6),#top50-board td:nth-child(7) {
        text-align: right !important; font-variant-numeric: tabular-nums;
      }
      .enhanced-board th.stockboard-sortable-header { cursor: pointer; user-select: none; }
      .enhanced-board th.stockboard-sortable-header[data-sort-dir="asc"]::after { content: " ▲"; color: #1d4ed8; }
      .enhanced-board th.stockboard-sortable-header[data-sort-dir="desc"]::after { content: " ▼"; color: #b91c1c; }
    `;
    document.head.appendChild(style);

    const idsToFreeze = ['refresh-delay','hot-lane-speed-badge','hot-actual-speed','candidate-lane-speed-badge','top20-lane-speed-badge','top20-actual-speed','top50-lane-speed-badge','top50-actual-speed','top100-lane-speed-badge','top100-actual-speed'];
    const byId = id => document.getElementById(id);
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
      idsToFreeze.forEach(id => {
        const element = byId(id);
        if (!element) return;
        remember(element);
        if (id.endsWith('actual-speed')) element.textContent = '실제 - / 서버 끊김';
        else if (id !== 'refresh-delay') {
          element.textContent = '서버 끊김';
          element.classList.remove('lane-speed-fast','lane-speed-medium','lane-speed-slow','lane-speed-protect','lane-speed-waiting');
          element.classList.add('lane-speed-slow');
        }
        element.setAttribute('title', '서버 끊김 / 표시 속도는 실시간 속도가 아님');
      });
    }
    function restoreServerOk() {
      if (!document.body.classList.contains('stockboard-server-down')) return;
      document.body.classList.remove('stockboard-server-down');
      idsToFreeze.concat(['combined-status-lamp']).forEach(id => {
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
        const response = await fetch(`/api/health?ts=${Date.now()}`, { cache: 'no-store', signal: controller.signal });
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
    if (graphBlock && supplyTable && row.firstElementChild !== graphBlock) row.insertBefore(graphBlock, supplyTable);
  }

  const SORT_TABLE_IDS = ['candidate-board', 'top20-board', 'top50-board', 'trading-board'];
  const sortState = new Map();
  let lastSortEventKey = '';
  let lastSortEventUntil = 0;

  function sortTables() {
    return SORT_TABLE_IDS.map(id => document.getElementById(id)).filter(Boolean);
  }
  function normalizeCellText(text) {
    return String(text || '').replace(/[↑↓▲▼]/g, '').replace(/,/g, '').replace(/%/g, '').trim();
  }
  function cellSortValue(row, columnIndex) {
    const cell = row.children[columnIndex];
    const text = normalizeCellText(cell ? cell.textContent : '');
    const numericText = text.replace(/[^0-9+\-.]/g, '');
    if (numericText && /^[-+]?\d+(?:\.\d+)?$/.test(numericText)) {
      const number = Number(numericText);
      if (Number.isFinite(number)) return { type: 'number', value: number };
    }
    return { type: 'text', value: text.toLowerCase() };
  }
  function dataRows(table) {
    const body = table.tBodies && table.tBodies[0] ? table.tBodies[0] : table;
    return Array.from(body.querySelectorAll('tr')).filter(row => !row.querySelector('th') && row.children.length > 1);
  }
  function applyTableSort(table) {
    const state = sortState.get(table.id);
    if (!state) return;
    const rows = dataRows(table);
    if (rows.length <= 1) return;
    rows.sort((left, right) => {
      const l = cellSortValue(left, state.columnIndex);
      const r = cellSortValue(right, state.columnIndex);
      let result = 0;
      if (l.type === 'number' && r.type === 'number') result = l.value - r.value;
      else result = String(l.value).localeCompare(String(r.value), 'ko-KR', { numeric: true });
      return state.direction === 'asc' ? result : -result;
    });
    const body = table.tBodies && table.tBodies[0] ? table.tBodies[0] : table;
    rows.forEach(row => body.appendChild(row));
    Array.from(table.querySelectorAll('th')).forEach((th, index) => {
      th.classList.add('stockboard-sortable-header');
      if (index === state.columnIndex) {
        th.dataset.sortDir = state.direction;
        th.setAttribute('aria-sort', state.direction === 'asc' ? 'ascending' : 'descending');
      } else {
        delete th.dataset.sortDir;
        th.removeAttribute('aria-sort');
      }
    });
  }
  function headerFromEvent(event) {
    const target = event.target;
    const th = target && target.closest ? target.closest('th') : null;
    if (!th || target.closest?.('.column-resizer')) return null;
    const table = th.closest('table');
    if (!table || !SORT_TABLE_IDS.includes(table.id)) return null;
    return { table, th };
  }
  function handleSortEvent(event) {
    const hit = headerFromEvent(event);
    if (!hit) return;
    const { table, th } = hit;
    const columnIndex = Array.from(th.parentElement.children || []).indexOf(th);
    if (columnIndex < 0) return;
    const eventKey = `${table.id}:${columnIndex}`;
    const now = performance.now();
    if (event.type !== 'pointerdown' && event.type !== 'mousedown' && eventKey === lastSortEventKey && now < lastSortEventUntil) {
      event.preventDefault();
      event.stopImmediatePropagation();
      return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
    const current = sortState.get(table.id);
    const direction = current && current.columnIndex === columnIndex && current.direction === 'asc' ? 'desc' : 'asc';
    sortState.set(table.id, { columnIndex, direction });
    applyTableSort(table);
    lastSortEventKey = eventKey;
    lastSortEventUntil = now + 450;
  }
  function markSortHeaders() {
    sortTables().forEach(table => {
      Array.from(table.querySelectorAll('th')).forEach(th => th.classList.add('stockboard-sortable-header'));
      applyTableSort(table);
    });
  }
  function installBoardHeaderSort() {
    ['pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'].forEach(eventName => {
      document.addEventListener(eventName, handleSortEvent, true);
    });
    const observer = new MutationObserver(markSortHeaders);
    if (document.body) observer.observe(document.body, { childList: true, subtree: true });
    markSortHeaders();
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
    installServerDisconnectGuard();
    installMarketSupplyGraphFirst();
    installBoardHeaderSort();
    installTop5ArrowNavigation();
    installModelPersistenceGuard();
  }

  installModelPersistenceGuard();
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

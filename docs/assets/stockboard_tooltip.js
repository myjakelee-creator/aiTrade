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

  const CANDIDATE_MODEL_PRIMARY_KEY = 'stockboard.candidateModelId.v1';
  const CANDIDATE_MODEL_LEGACY_KEY = 'stockboard.candidateModel.v1';
  const CANDIDATE_MODELS = [
    ['NET_BUY_STRENGTH_V02', '순매수 강도 v0.2'],
    ['NET_BUY_STRENGTH_V01', '순매수 강도 v0.1'],
    ['TVRANK_A_V03_TEMP', '현재 하드코딩 기준'],
    ['OPENING_MONEY_FLOW_V01', '돈쏠림 시작형'],
    ['OPENING_ACCUMULATION_V01', '조용한 매집형'],
    ['OPENING_BURST_V01', '폭발 확인형'],
    ['PROGRAM_FLOW_V01', '프로그램 동행형'],
    ['RELATIVE_STRENGTH_OPENING_V01', '시장대비 강도형']
  ];
  const CANDIDATE_MODEL_IDS = new Set(CANDIDATE_MODELS.map(([id]) => id));
  const CANDIDATE_MODEL_LABEL_TO_ID = new Map(
    CANDIDATE_MODELS.map(([id, label]) => [label.replace(/\s+/g, ''), id])
  );

  function candidateModelSelector() {
    return document.getElementById('candidate-model-selector');
  }

  function normalizeCandidateModelId(value) {
    const text = String(value || '').trim();
    if (CANDIDATE_MODEL_IDS.has(text)) return text;
    return CANDIDATE_MODEL_LABEL_TO_ID.get(text.replace(/\s+/g, '')) || '';
  }

  function readStoredCandidateModelId() {
    try {
      return normalizeCandidateModelId(
        localStorage.getItem(CANDIDATE_MODEL_PRIMARY_KEY)
        || localStorage.getItem(CANDIDATE_MODEL_LEGACY_KEY)
        || ''
      );
    } catch (_error) {
      return '';
    }
  }

  function writeStoredCandidateModelId(modelId) {
    const normalized = normalizeCandidateModelId(modelId);
    if (!normalized) return '';
    try {
      localStorage.setItem(CANDIDATE_MODEL_PRIMARY_KEY, normalized);
      localStorage.setItem(CANDIDATE_MODEL_LEGACY_KEY, normalized);
    } catch (_error) {
      // localStorage can be unavailable in restricted environments.
    }
    return normalized;
  }

  function optionForCandidateModel(selector, modelId) {
    return Array.from(selector?.options || []).find(option => option.value === modelId) || null;
  }

  function ensureCandidateModelOptions(selector) {
    if (!selector) return;
    CANDIDATE_MODELS.forEach(([id, label]) => {
      let option = optionForCandidateModel(selector, id);
      if (!option) {
        option = document.createElement('option');
        option.value = id;
        selector.appendChild(option);
      }
      option.textContent = label;
      option.disabled = false;
    });
  }

  function currentCandidateModelId(selector) {
    return normalizeCandidateModelId(selector?.value)
      || normalizeCandidateModelId(selector?.selectedOptions?.[0]?.textContent);
  }

  function reconcileCandidateModelSelection(options = {}) {
    const selector = candidateModelSelector();
    if (!selector) return '';
    ensureCandidateModelOptions(selector);
    const stored = readStoredCandidateModelId();
    const current = currentCandidateModelId(selector);
    const modelId = stored || current;
    if (!modelId || !optionForCandidateModel(selector, modelId)) return '';
    const changed = selector.value !== modelId;
    selector.value = modelId;
    writeStoredCandidateModelId(modelId);
    if (changed && options.dispatchChange) {
      selector.dispatchEvent(new Event('change', { bubbles: true }));
    }
    return modelId;
  }

  function installCandidateModelPersistenceGuard() {
    const selector = candidateModelSelector();
    if (!selector) return;
    ensureCandidateModelOptions(selector);
    reconcileCandidateModelSelection({ dispatchChange: false });
    if (selector.dataset.candidateModelGuardInstalled !== '1') {
      selector.dataset.candidateModelGuardInstalled = '1';
      selector.addEventListener('change', () => {
        const modelId = currentCandidateModelId(selector) || selector.value;
        if (!modelId || !optionForCandidateModel(selector, modelId)) return;
        selector.value = modelId;
        writeStoredCandidateModelId(modelId);
      }, true);
    }
    [0, 50, 250, 1000, 2000].forEach((delay) => {
      window.setTimeout(() => reconcileCandidateModelSelection({ dispatchChange: delay >= 250 }), delay);
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

  const BOARD_SORT_TABLE_IDS = ['candidate-board', 'top20-board', 'top50-board', 'trading-board'];

  function boardSortTables() {
    return BOARD_SORT_TABLE_IDS.map((id) => document.getElementById(id)).filter(Boolean);
  }

  function installBoardHeaderSort() {
    const sortState = new Map();
    let applyingSort = false;
    let applyQueued = false;
    let suppressClickUntil = 0;
    let suppressClickKey = '';

    function normalizeCellText(text) {
      return String(text || '')
        .replace(/[↑↓▲▼]/g, '')
        .replace(/,/g, '')
        .replace(/%/g, '')
        .trim();
    }

    function cellValue(row, columnIndex) {
      const cell = row.children[columnIndex];
      const text = normalizeCellText(cell ? cell.textContent : '');
      const numberMatch = text.match(/[-+]?\d+(?:\.\d+)?/);
      if (numberMatch && text.replace(numberMatch[0], '').trim().length <= 2) {
        const number = Number(numberMatch[0]);
        if (Number.isFinite(number)) return { type: 'number', value: number };
      }
      return { type: 'text', value: text.toLowerCase() };
    }

    function rowsForSort(table) {
      const body = table.tBodies && table.tBodies[0] ? table.tBodies[0] : table;
      return Array.from(body.querySelectorAll('tr')).filter((row) => !row.querySelector('th'));
    }

    function applySort(table) {
      const state = sortState.get(table.id);
      if (!state) return;
      const rows = rowsForSort(table);
      if (rows.length <= 1) return;
      applyingSort = true;
      rows.sort((a, b) => {
        const av = cellValue(a, state.columnIndex);
        const bv = cellValue(b, state.columnIndex);
        let result;
        if (av.type === 'number' && bv.type === 'number') {
          result = av.value - bv.value;
        } else {
          result = String(av.value).localeCompare(String(bv.value), 'ko-KR', { numeric: true });
        }
        return state.direction === 'asc' ? result : -result;
      });
      const body = table.tBodies && table.tBodies[0] ? table.tBodies[0] : table;
      rows.forEach((row) => body.appendChild(row));
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
      applyingSort = false;
    }

    function queueApplySorts() {
      if (applyQueued || applyingSort) return;
      applyQueued = true;
      window.requestAnimationFrame(() => {
        applyQueued = false;
        boardSortTables().forEach(applySort);
      });
    }

    function markHeadersAndListeners() {
      boardSortTables().forEach((table) => {
        if (table.dataset.stockboardHeaderSortInstalled !== '1') {
          table.dataset.stockboardHeaderSortInstalled = '1';
          table.addEventListener('pointerup', handleHeaderSortEvent, true);
          table.addEventListener('click', handleHeaderSortEvent, true);
        }
        Array.from(table.querySelectorAll('th')).forEach((th) => {
          th.classList.add('stockboard-sortable-header');
        });
      });
    }

    function handleHeaderSortEvent(event) {
      const target = event.target;
      const th = target && target.closest ? target.closest('th') : null;
      if (!th || target.closest?.('.column-resizer')) return;
      const table = th.closest('table');
      if (!table || !BOARD_SORT_TABLE_IDS.includes(table.id)) return;
      const headerCells = Array.from(th.parentElement.children || []);
      const columnIndex = headerCells.indexOf(th);
      if (columnIndex < 0) return;
      const eventKey = `${table.id}:${columnIndex}`;
      const now = performance.now();
      if (event.type === 'click' && suppressClickKey === eventKey && now < suppressClickUntil) {
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      event.preventDefault();
      event.stopImmediatePropagation();
      const current = sortState.get(table.id);
      const direction = current && current.columnIndex === columnIndex && current.direction === 'asc' ? 'desc' : 'asc';
      sortState.set(table.id, { columnIndex, direction });
      applySort(table);
      if (event.type === 'pointerup') {
        suppressClickKey = eventKey;
        suppressClickUntil = now + 350;
      }
    }

    document.addEventListener('pointerup', handleHeaderSortEvent, true);
    document.addEventListener('click', handleHeaderSortEvent, true);
    const observer = new MutationObserver(() => {
      markHeadersAndListeners();
      queueApplySorts();
    });
    if (document.body) observer.observe(document.body, { childList: true, subtree: true });
    markHeadersAndListeners();
    queueApplySorts();
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
          if (row.querySelector('th')) return;
          if (row.offsetParent === null) return;
          const code = codeFromNode(row);
          if (code) rows.push({ row, code, tableId: id });
        });
      });
      return rows;
    }

    function isEditableTarget(target) {
      return Boolean(target && target.closest && target.closest('input, textarea, select, [contenteditable="true"], [contenteditable=""]'));
    }

    function move(direction, event) {
      const selection = window.StockBoardSelection;
      const activeCode = normalizeCode(selection && selection.activeStockCode);
      if (!selection || !activeCode || typeof selection.activateStock !== 'function') return false;
      const rows = visibleRows();
      const index = rows.findIndex((item) => item.code === activeCode);
      if (index < 0) return false;
      const next = rows[index + direction];
      if (!next) return false;
      event.preventDefault();
      event.stopImmediatePropagation();
      selection.activateStock(next.code, { scroll: true });
      return true;
    }

    document.addEventListener('keydown', (event) => {
      if (event.key !== 'ArrowUp' && event.key !== 'ArrowDown') return;
      if (isEditableTarget(event.target)) return;
      move(event.key === 'ArrowUp' ? -1 : 1, event);
    }, true);
  }

  function installUiHotfixes() {
    installServerDisconnectGuard();
    installMarketSupplyGraphFirst();
    installBoardHeaderSort();
    installTop5ArrowNavigation();
    installCandidateModelPersistenceGuard();
  }

  installCandidateModelPersistenceGuard();

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

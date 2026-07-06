(function () {
  "use strict";

  ensureCandidateModelOptions();
  installRuntimeDisplayFixes();

  window.StockBoardControls = Object.assign(window.StockBoardControls || {}, {
    readStoredValue,
    writeStoredValue,
    readStoredBoolean,
    writeStoredBoolean,
    applyButtonPressed,
    toggleElementHidden,
    safeAddEventListener,
    setElementText
  });

  function ensureCandidateModelOptions() {
    const selector = document.getElementById('candidate-model-selector');
    if (!selector) return;

    const primaryModelId = 'NET_BUY_STRENGTH_V02';
    const primaryLabel = '순매수 강도 v0.2';
    const previousModelId = 'NET_BUY_STRENGTH_V01';
    const previousLabel = '순매수 강도 v0.1';
    const legacyModelId = 'TVRANK_A_V03_TEMP';
    const legacyLabel = '현재 하드코딩 기준';

    let primaryOption = Array.from(selector.options || [])
      .find(option => option.value === primaryModelId);
    if (!primaryOption) {
      primaryOption = document.createElement('option');
      primaryOption.value = primaryModelId;
    }
    primaryOption.textContent = primaryLabel;
    primaryOption.dataset.primary = 'true';
    selector.insertBefore(primaryOption, selector.firstChild || null);

    let previousOption = Array.from(selector.options || [])
      .find(option => option.value === previousModelId);
    if (!previousOption) {
      previousOption = document.createElement('option');
      previousOption.value = previousModelId;
      selector.insertBefore(previousOption, primaryOption.nextSibling || null);
    }
    previousOption.textContent = previousLabel;
    previousOption.dataset.previous = 'true';

    let legacyOption = Array.from(selector.options || [])
      .find(option => option.value === legacyModelId);
    if (!legacyOption) {
      legacyOption = document.createElement('option');
      legacyOption.value = legacyModelId;
      selector.appendChild(legacyOption);
    }
    legacyOption.textContent = legacyLabel;
    legacyOption.dataset.legacy = 'true';

    try {
      const primaryKey = 'stockboard.candidateModelId.v1';
      const legacyKey = 'stockboard.candidateModel.v1';
      const saved = localStorage.getItem(primaryKey) || localStorage.getItem(legacyKey);
      if (saved === null) {
        localStorage.setItem(primaryKey, primaryModelId);
        localStorage.setItem(legacyKey, primaryModelId);
      } else if ([primaryModelId, previousModelId, legacyModelId].includes(saved)) {
        localStorage.setItem(primaryKey, saved);
        localStorage.setItem(legacyKey, saved);
      }
    } catch (error) {
      // Ignore storage failures; inline board code still falls back safely.
    }
  }

  function installRuntimeDisplayFixes() {
    injectRuntimeDisplayFixStyles();
    const apply = () => {
      applyAmountRatioColorRule();
    };
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', () => {
        apply();
        observeRuntimeDisplayFixes(apply);
      }, { once: true });
    } else {
      apply();
      observeRuntimeDisplayFixes(apply);
    }
  }

  function injectRuntimeDisplayFixStyles() {
    if (document.getElementById('stockboard-controls-runtime-fix-style')) return;
    const style = document.createElement('style');
    style.id = 'stockboard-controls-runtime-fix-style';
    style.textContent = `
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
      #top50-board .amount-ratio-cell {
        text-align: right !important;
        font-variant-numeric: tabular-nums;
      }
      #top20-board td:nth-child(4),
      #top50-board td:nth-child(4) {
        text-align: left !important;
      }
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
      .amount-ratio-number.amount-ratio-strong {
        color: var(--red) !important;
      }
      .amount-ratio-number.amount-ratio-weak {
        color: var(--blue) !important;
      }
      .amount-ratio-number.amount-ratio-missing {
        color: #4b5563 !important;
      }
    `;
    document.head.appendChild(style);
  }

  function observeRuntimeDisplayFixes(apply) {
    if (window.__stockboardRuntimeDisplayFixObserverInstalled) return;
    window.__stockboardRuntimeDisplayFixObserverInstalled = true;
    const observer = new MutationObserver(() => {
      if (window.__stockboardRuntimeDisplayFixQueued) return;
      window.__stockboardRuntimeDisplayFixQueued = true;
      window.requestAnimationFrame(() => {
        window.__stockboardRuntimeDisplayFixQueued = false;
        apply();
      });
    });
    if (document.body) {
      observer.observe(document.body, { childList: true, subtree: true, characterData: true });
    }
    window.setInterval(apply, 1000);
  }

  function parseAmountRatioText(text) {
    const normalized = String(text || '').replace(/,/g, '').trim();
    if (!normalized || normalized === '-') return null;
    const match = normalized.match(/[-+]?\d+(?:\.\d+)?/);
    if (!match) return null;
    const value = Number(match[0]);
    return Number.isFinite(value) ? value : null;
  }

  function applyAmountRatioColorRule() {
    document.querySelectorAll('.amount-ratio-number').forEach(element => {
      const ratio = parseAmountRatioText(element.textContent);
      element.classList.remove('amount-ratio-strong', 'amount-ratio-weak', 'amount-ratio-neutral', 'amount-ratio-missing');
      if (ratio === null) {
        element.classList.add('amount-ratio-missing');
      } else if (ratio >= 1) {
        element.classList.add('amount-ratio-strong');
      } else {
        element.classList.add('amount-ratio-weak');
      }
    });
  }

  function readStoredValue(storageKey, fallback) {
    try {
      const value = localStorage.getItem(storageKey);
      return value === null ? fallback : value;
    } catch (error) {
      return fallback;
    }
  }

  function writeStoredValue(storageKey, value) {
    try {
      localStorage.setItem(storageKey, value);
      if (storageKey === 'stockboard.candidateModelId.v1') {
        localStorage.setItem('stockboard.candidateModel.v1', value);
      }
      return true;
    } catch (error) {
      return false;
    }
  }

  function readStoredBoolean(storageKey, fallback) {
    const fallbackValue = Boolean(fallback);
    try {
      const value = localStorage.getItem(storageKey);
      if (value === null) return fallbackValue;
      return value === '1' || value === 'true';
    } catch (error) {
      return fallbackValue;
    }
  }

  function writeStoredBoolean(storageKey, value) {
    try {
      localStorage.setItem(storageKey, value ? '1' : '0');
      return true;
    } catch (error) {
      return false;
    }
  }

  function applyButtonPressed(button, pressed) {
    if (!button) return;
    button.classList.toggle('active', Boolean(pressed));
    button.setAttribute('aria-pressed', pressed ? 'true' : 'false');
  }

  function toggleElementHidden(element, hidden) {
    if (!element) return;
    element.hidden = Boolean(hidden);
  }

  function safeAddEventListener(target, type, handler, options) {
    if (!target || typeof target.addEventListener !== 'function') return null;
    target.addEventListener(type, handler, options);
    return () => target.removeEventListener(type, handler, options);
  }

  function setElementText(element, text) {
    if (!element) return;
    element.textContent = text;
  }
})();

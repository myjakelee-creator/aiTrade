(function () {
  "use strict";

  ensureCandidateModelOptions();

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

    const primaryModelId = 'NET_BUY_STRENGTH_V01';
    const primaryLabel = '순매수 강도 v0.1';
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
      const storageKey = 'stockboard.candidateModelId.v1';
      const saved = localStorage.getItem(storageKey);
      if (saved === null || saved === legacyModelId) {
        localStorage.setItem(storageKey, primaryModelId);
      }
    } catch (error) {
      // Ignore storage failures; inline board code still falls back safely.
    }
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
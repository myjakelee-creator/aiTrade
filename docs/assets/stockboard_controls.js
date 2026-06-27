(function () {
  "use strict";

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

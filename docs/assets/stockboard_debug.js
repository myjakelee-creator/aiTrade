(function () {
  "use strict";

  window.StockBoardDebug = Object.assign(window.StockBoardDebug || {}, {
    loadDebugPanelVisible,
    saveDebugPanelVisible,
    buildDirectApiDebugUrl,
    pickDirectApiValue,
    applyDebugPanelVisibility
  });

  function loadDebugPanelVisible(storageKey) {
    try {
      return localStorage.getItem(storageKey) === '1';
    } catch (error) {
      return false;
    }
  }

  function saveDebugPanelVisible(storageKey, visible) {
    try {
      localStorage.setItem(storageKey, visible ? '1' : '0');
    } catch (error) {
      console.warn('Debug panel visibility could not be saved.', error);
    }
  }

  function buildDirectApiDebugUrl(codes) {
    const safeCodes = Array.isArray(codes) ? codes.filter(Boolean) : [];
    return `/api/realtime?codes=${safeCodes.join(',')}`;
  }

  function pickDirectApiValue(payload, keys) {
    if (!payload || typeof payload !== 'object') return null;
    for (const key of keys || []) {
      const value = payload[key];
      if (value !== null && value !== undefined && value !== '') return value;
    }
    return null;
  }

  function applyDebugPanelVisibility(panel, button, visible) {
    if (panel) {
      panel.hidden = !visible;
    }
    if (button) {
      button.classList.toggle('active', visible);
      button.setAttribute('aria-pressed', visible ? 'true' : 'false');
    }
  }
})();

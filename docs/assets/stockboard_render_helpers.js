(function () {
  "use strict";

  window.StockBoardRenderHelpers = Object.assign(window.StockBoardRenderHelpers || {}, {
    hasValue,
    firstValue,
    classNames
  });

  function hasValue(value) {
    return value !== null && value !== undefined && value !== '';
  }

  function firstValue(row, keys) {
    if (!row || typeof row !== 'object') return null;
    for (const key of keys || []) {
      const value = row[key];
      if (hasValue(value)) return value;
    }
    return null;
  }

  function classNames(...values) {
    return values
      .flatMap(value => Array.isArray(value) ? value : [value])
      .map(value => String(value || '').trim())
      .filter(Boolean)
      .join(' ');
  }
})();

(function () {
  "use strict";

  function hasCloseMetricsSnapshot(row) {
    return row
      && row.strength_source === 'opt10046'
      && row.orderbook_source === 'opt10004';
  }

  function needsCloseMetrics(row, code, requestedCodes, pendingCodes, completedCodes) {
    if (!row || !code) return false;
    if (hasCloseMetricsSnapshot(row)) return false;
    if (completedCodes?.has?.(code)) return false;
    if (requestedCodes?.has?.(code)) return false;
    if (pendingCodes?.has?.(code)) return false;
    return true;
  }

  function canRequestCloseMetrics(now, lastRequestedAt, inFlight, throttleMs) {
    if (inFlight) return false;
    return now - lastRequestedAt >= throttleMs;
  }

  function buildCloseMetricsRequestUrl(codes, options = {}) {
    const query = new URLSearchParams({
      codes: (codes || []).join(','),
      priority: options.priority || 'lazy',
      force: String(options.force ?? '0')
    });
    return `/api/close_metrics_request?${query}`;
  }

  window.StockBoardCloseMetrics = Object.assign(window.StockBoardCloseMetrics || {}, {
    hasCloseMetricsSnapshot,
    needsCloseMetrics,
    canRequestCloseMetrics,
    buildCloseMetricsRequestUrl
  });
})();

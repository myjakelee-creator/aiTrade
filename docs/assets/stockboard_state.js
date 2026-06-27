(function () {
  "use strict";

  const STORAGE_KEYS = Object.freeze({
    tradingBoardColumnWidths: 'stockboard.tradingBoard.columnWidths.v1',
    tradingBoardSortState: 'stockboard.tradingBoard.sortState.v1',
    displayDensity: 'stockboard.displayDensity.v1',
    displayMode: 'stockboardDisplayMode',
    topbarColumnWidths: 'stockboard.topbar.columnWidths.v4',
    usMarketColumnWidths: 'stockboard.usMarket.columnWidths.v2',
    marketSupplyColumnWidths: 'stockboard.marketSupply.columnWidths.v2',
    candleMode: 'stockboard.candleMode.v1',
    debugPanelVisible: 'stockboard.debugPanelVisible'
  });

  const DISPLAY_MODES = Object.freeze({
    fast: 'fast',
    graphic: 'graphic'
  });

  const CLOSE_METRICS = Object.freeze({
    batchSize: 20,
    throttleMs: 1000,
    scrollDeltaTriggerPx: 150,
    refreshDelayMs: 10000
  });

  window.StockBoardState = Object.assign(window.StockBoardState || {}, {
    STORAGE_KEYS,
    DISPLAY_MODES,
    CLOSE_METRICS
  });
})();

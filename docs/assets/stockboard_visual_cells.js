(function () {
  "use strict";

  const Format = window.StockBoardFormat || {};
  const numericValue = Format.numericValue;
  const formatVolume = Format.formatVolume;
  const displayValue = Format.displayValue;

  function strengthVisualPosition(value) {
    const normalized = Math.max(-1, Math.min(1, (value - 100) / 100));
    const visual = Math.sign(normalized) * Math.pow(Math.abs(normalized), 0.6);
    return (1 + visual) * 50;
  }

  function balanceView(value, options = {}) {
    const bidVolume = numericValue(options.bidVolume);
    const askVolume = numericValue(options.askVolume);
    const optionBidPct = numericValue(options.bidPct);
    const optionAskPct = numericValue(options.askPct);
    const source = options.orderbookSource ?? options.orderbook_source ?? null;
    const snapshotAt = options.orderbookSnapshotAt ?? options.orderbook_snapshot_at ?? null;
    const staleSec = options.orderbookStaleSec ?? options.orderbook_stale_sec ?? null;
    const statusValue = options.orderbookStatus ?? options.orderbook_status ?? null;
    if (
      bidVolume === null
      && askVolume === null
      && (optionBidPct === null || optionAskPct === null)
    ) return null;
    let buyPct = null;
    let sellPct = null;
    if (optionBidPct !== null && optionAskPct !== null) {
      buyPct = Math.round(optionBidPct);
      sellPct = Math.round(optionAskPct);
    } else if (bidVolume !== null && askVolume !== null && bidVolume + askVolume > 0) {
      buyPct = Math.round((bidVolume / (bidVolume + askVolume)) * 100);
      sellPct = 100 - buyPct;
    }
    if (buyPct === null || sellPct === null) return null;
    const display = `${buyPct}% / ${sellPct}%`;
    const tooltipLines = [`잔량비: ${display}`];
    if (bidVolume !== null) tooltipLines.push(`총매수잔량: ${formatVolume(bidVolume)}`);
    if (askVolume !== null) tooltipLines.push(`총매도잔량: ${formatVolume(askVolume)}`);
    tooltipLines.push('계산: 매수잔량 / 전체잔량, 매도잔량 / 전체잔량');
    tooltipLines.push(`source: ${displayValue(source)}`);
    tooltipLines.push(`snapshot_at: ${displayValue(snapshotAt)}`);
    tooltipLines.push(`stale_sec: ${displayValue(staleSec)}`);
    tooltipLines.push(`status: ${displayValue(statusValue)}`);
    return {
      value: display,
      buyPct,
      sellPct,
      blueWidth: sellPct,
      redWidth: buyPct,
      tooltip: tooltipLines.join('\n')
    };
  }

  function minuteStrengthView(value) {
    const number = numericValue(value);
    if (number === null) return null;
    const redWidth = strengthVisualPosition(number);
    const difference = Math.round(number - 100);
    const status = number < 100 ? '매도 우세' : number > 100 ? '매수 우세' : '균형';
    const differenceText = difference > 0 ? `+${difference}` : String(difference);
    const tooltipLines = [`순간강도: ${number.toFixed(2)}`, status, `100 기준 ${differenceText}`];
    if (number >= 200) tooltipLines.push('바 표시: 200 기준 매수 포화');
    if (number <= 0) tooltipLines.push('바 표시: 0 기준 매도 포화');
    return {
      value: number,
      blueWidth: 100 - redWidth,
      redWidth,
      tooltip: tooltipLines.join('\n')
    };
  }

  function sessionStrengthView(value, options = {}) {
    const number = numericValue(value);
    if (number === null) return null;
    const buyVolume = numericValue(options.buyVolume);
    const sellVolume = numericValue(options.sellVolume);
    const redWidth = strengthVisualPosition(number);
    const difference = Math.round(number - 100);
    const status = number < 100 ? '매도 우세' : number > 100 ? '매수 우세' : '균형';
    const differenceText = difference > 0 ? `+${difference}` : String(difference);
    const tooltipLines = [
      `5분강도: ${number.toFixed(2)}`,
      `최근5분 매수체결량: ${buyVolume !== null ? formatVolume(buyVolume) : '-'}`,
      `최근5분 매도체결량: ${sellVolume !== null ? formatVolume(sellVolume) : '-'}`,
      buyVolume !== null && sellVolume !== null
        ? '계산: 최근5분 매수체결량 / 최근5분 매도체결량 * 100'
        : '계산: -',
      status,
      `100 기준 ${differenceText}`,
      '기준: 브라우저 수신 기준 최근 5분 delta',
      '주의: opt10046 체결강도5분 아님'
    ];
    return {
      value: number,
      blueWidth: 100 - redWidth,
      redWidth,
      tooltip: tooltipLines.join('\n')
    };
  }

  window.StockBoardVisualCells = Object.assign(window.StockBoardVisualCells || {}, {
    balanceView,
    minuteStrengthView,
    sessionStrengthView
  });
})();

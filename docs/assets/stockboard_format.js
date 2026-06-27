(function () {
  "use strict";

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, char => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[char]);
  }

  function displayValue(value, suffix = '') {
    if (value === null || value === undefined || value === '') return '-';
    const text = String(value);
    return suffix && !text.endsWith(suffix) ? `${text}${suffix}` : text;
  }

  function formatInteger(value) {
    if (value === null || value === undefined || value === '') return '-';
    const number = Number(String(value).replace(/,/g, ''));
    return Number.isFinite(number) ? Math.trunc(number).toLocaleString('en-US') : displayValue(value);
  }

  function numericValue(value) {
    if (value === null || value === undefined || value === '') return null;
    const number = Number(String(value).replace(/[%+,]/g, '').trim());
    return Number.isFinite(number) ? number : null;
  }

  function formatTruncatedInteger(value) {
    const number = numericValue(value);
    return number === null ? '-' : String(Math.trunc(number));
  }

  function formatVolume(value) {
    const number = numericValue(value);
    return number === null ? '-' : Math.trunc(number).toLocaleString('en-US');
  }

  function formatPercent(value) {
    if (value === null || value === undefined || value === '') return '-';
    const number = Number(String(value).replace(/[%+,]/g, '').trim());
    if (!Number.isFinite(number)) return displayValue(value, '%');
    return `${number > 0 ? '+' : ''}${number.toFixed(2)}%`;
  }

  function formatOhlcTooltipNumber(value, maximumFractionDigits = 0) {
    if (value === null || value === undefined || value === '') return '-';
    const number = Number(String(value).replace(/,/g, ''));
    if (!Number.isFinite(number)) return '-';
    return number.toLocaleString('en-US', {
      minimumFractionDigits: 0,
      maximumFractionDigits
    });
  }

  function formatOhlcMove(value, prevClose) {
    const price = Number(value);
    const base = Number(prevClose);
    if (!Number.isFinite(price) || !Number.isFinite(base) || base <= 0) return '';
    const rate = (price - base) / base * 100;
    return ` (${rate > 0 ? '+' : ''}${rate.toFixed(2)}%)`;
  }

  function formatOhlcTooltipPrice(value, prevClose, maximumFractionDigits = 0) {
    const display = formatOhlcTooltipNumber(value, maximumFractionDigits);
    if (display === '-') return display;
    return `${display}${formatOhlcMove(value, prevClose)}`;
  }

  window.StockBoardFormat = Object.assign(window.StockBoardFormat || {}, {
    escapeHtml,
    displayValue,
    formatInteger,
    numericValue,
    formatTruncatedInteger,
    formatVolume,
    formatPercent,
    formatOhlcTooltipNumber,
    formatOhlcMove,
    formatOhlcTooltipPrice
  });
})();

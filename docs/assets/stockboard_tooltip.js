(function () {
  "use strict";

  const AFTER_CLOSE_STRENGTH_PREFIX = '1분강도 대체값: 5분강도';

  function ensureTooltipElement(className) {
    const element = document.createElement('div');
    element.className = className;
    element.setAttribute('role', 'tooltip');
    document.body.appendChild(element);
    return element;
  }

  function afterCloseStrengthTooltip(content) {
    const text = String(content || '');
    if (!text.startsWith(AFTER_CLOSE_STRENGTH_PREFIX)) return text;
    const lines = text.split('\n').map(line => line.trim()).filter(Boolean);
    const strengthLine = lines.find(line => line.startsWith('5분강도:')) || '5분강도: -';
    return [
      strengthLine.replace('5분강도:', '5분강도 참고값:'),
      '표시: 장마감 1분강도 칸에는 5분강도 참고값 표시',
      '계산: 순매수 강도 v0.2 장마감 점수에서는 1분강도 계산 제외',
      '분모: 장중 700점 / 장마감 600점',
      '상태: display_only'
    ].join('\n');
  }

  function setTooltipContent(element, content) {
    if (!element) return;
    element.textContent = afterCloseStrengthTooltip(content);
  }

  function decorateAfterCloseStrengthCell(source) {
    if (!source || !source.matches?.(`[data-tooltip^="${AFTER_CLOSE_STRENGTH_PREFIX}"]`)) return;
    source.dataset.afterCloseStrengthDisplayOnly = '1';
    source.setAttribute('aria-label', afterCloseStrengthTooltip(source.dataset.tooltip || ''));

    const fastText = source.querySelector?.('.fast-metric-text');
    if (fastText) {
      const rawText = fastText.dataset.afterCloseRawText || fastText.textContent.trim();
      if (!fastText.dataset.afterCloseRawText) fastText.dataset.afterCloseRawText = rawText;
      const displayText = rawText && rawText !== '-' ? `5분 ${rawText}` : '5분 -';
      if (fastText.textContent !== displayText) fastText.textContent = displayText;
      fastText.dataset.afterCloseStrengthDisplayOnly = '1';
      return;
    }

    if (!source.querySelector?.('.after-close-strength-badge')) {
      const badge = document.createElement('span');
      badge.className = 'after-close-strength-badge';
      badge.textContent = '5분참고';
      badge.setAttribute('aria-hidden', 'true');
      badge.style.cssText = [
        'position:absolute',
        'right:3px',
        'bottom:1px',
        'z-index:3',
        'padding:0 3px',
        'border-radius:2px',
        'background:rgba(255,247,223,.92)',
        'color:#8a5a00',
        'font-size:10px',
        'font-weight:800',
        'line-height:1.15',
        'pointer-events:none'
      ].join(';');
      source.style.position = source.style.position || 'relative';
      source.appendChild(badge);
    }
  }

  function decorateAfterCloseStrengthCells(root = document) {
    if (!root?.querySelectorAll) return;
    root
      .querySelectorAll(`[data-tooltip^="${AFTER_CLOSE_STRENGTH_PREFIX}"]`)
      .forEach(decorateAfterCloseStrengthCell);
  }

  function installAfterCloseStrengthDecorator() {
    const run = () => decorateAfterCloseStrengthCells(document);
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', run, { once: true });
    } else {
      run();
    }
    const observer = new MutationObserver(mutations => {
      for (const mutation of mutations) {
        if (mutation.type === 'attributes' && mutation.attributeName === 'data-tooltip') {
          decorateAfterCloseStrengthCell(mutation.target);
        }
        mutation.addedNodes?.forEach(node => {
          if (node.nodeType === Node.ELEMENT_NODE) {
            if (node.matches?.(`[data-tooltip^="${AFTER_CLOSE_STRENGTH_PREFIX}"]`)) {
              decorateAfterCloseStrengthCell(node);
            }
            decorateAfterCloseStrengthCells(node);
          }
        });
      }
    });
    observer.observe(document.documentElement, {
      subtree: true,
      childList: true,
      attributes: true,
      attributeFilter: ['data-tooltip']
    });
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

  installAfterCloseStrengthDecorator();

  window.StockBoardTooltip = Object.assign(window.StockBoardTooltip || {}, {
    ensureTooltipElement,
    setTooltipContent,
    positionTooltipElement,
    showTooltipElement,
    hideTooltipElement,
    decorateAfterCloseStrengthCells
  });
})();

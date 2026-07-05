(function () {
  "use strict";

  function ensureTooltipElement(className) {
    const element = document.createElement('div');
    element.className = className;
    element.setAttribute('role', 'tooltip');
    document.body.appendChild(element);
    return element;
  }

  function setTooltipContent(element, content) {
    if (!element) return;
    element.textContent = content || '';
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

  window.StockBoardTooltip = Object.assign(window.StockBoardTooltip || {}, {
    ensureTooltipElement,
    setTooltipContent,
    positionTooltipElement,
    showTooltipElement,
    hideTooltipElement
  });
})();

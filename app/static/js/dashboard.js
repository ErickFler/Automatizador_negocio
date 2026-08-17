(() => {
  "use strict";

  const source = document.getElementById("dashboard-chart-data");
  if (!source) return;

  const periodSelector = document.getElementById("period");
  const customDateFields = document.querySelectorAll(".custom-date-field");
  function updateCustomDateVisibility() {
    const hidden = periodSelector && periodSelector.value !== "custom";
    customDateFields.forEach((field) => { field.hidden = hidden; });
  }
  if (periodSelector) periodSelector.addEventListener("change", updateCustomDateVisibility);
  updateCustomDateVisibility();

  let dashboard;
  try {
    dashboard = JSON.parse(source.textContent);
  } catch (_error) {
    return;
  }

  const money = dashboard.is_money;
  const numberFormat = new Intl.NumberFormat("es-MX", {
    style: money ? "currency" : "decimal",
    currency: money ? "MXN" : undefined,
    maximumFractionDigits: money ? 2 : 4,
  });

  function prepareCanvas(canvas) {
    const ratio = window.devicePixelRatio || 1;
    const width = Math.max(canvas.clientWidth, 320);
    const height = Math.max(canvas.clientHeight, 300);
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    const context = canvas.getContext("2d");
    context.scale(ratio, ratio);
    context.font = "14px system-ui, sans-serif";
    return { context, width, height };
  }

  function drawEmpty(context, width, height) {
    context.fillStyle = "#627269";
    context.textAlign = "center";
    context.fillText("No hay datos para mostrar", width / 2, height / 2);
  }

  function drawAxes(context, width, height, padding, maxValue) {
    context.strokeStyle = "#dce7e1";
    context.fillStyle = "#627269";
    context.lineWidth = 1;
    context.textAlign = "right";
    for (let step = 0; step <= 4; step += 1) {
      const y = padding.top + ((height - padding.top - padding.bottom) * step) / 4;
      const value = maxValue * (1 - step / 4);
      context.beginPath();
      context.moveTo(padding.left, y);
      context.lineTo(width - padding.right, y);
      context.stroke();
      context.fillText(numberFormat.format(value), padding.left - 9, y + 5);
    }
  }

  function drawLineChart(canvas, dataset) {
    const values = dataset.values.map(Number);
    const { context, width, height } = prepareCanvas(canvas);
    if (!values.length) return drawEmpty(context, width, height);
    const padding = { top: 24, right: 24, bottom: 52, left: 78 };
    const maxValue = Math.max(...values, 0) || 1;
    drawAxes(context, width, height, padding, maxValue);
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;
    const gap = values.length > 1 ? plotWidth / (values.length - 1) : 0;
    context.strokeStyle = "#176742";
    context.fillStyle = "#176742";
    context.lineWidth = 3;
    context.beginPath();
    values.forEach((value, index) => {
      const x = padding.left + (values.length > 1 ? gap * index : plotWidth / 2);
      const y = padding.top + plotHeight * (1 - value / maxValue);
      if (index === 0) context.moveTo(x, y);
      else context.lineTo(x, y);
    });
    context.stroke();
    context.textAlign = "center";
    values.forEach((value, index) => {
      const x = padding.left + (values.length > 1 ? gap * index : plotWidth / 2);
      const y = padding.top + plotHeight * (1 - value / maxValue);
      context.beginPath();
      context.arc(x, y, 5, 0, Math.PI * 2);
      context.fill();
      const label = dataset.labels[index];
      if (values.length <= 16 || index % Math.ceil(values.length / 12) === 0) {
        context.save();
        context.translate(x, height - padding.bottom + 22);
        context.rotate(values.length > 12 ? -0.55 : 0);
        context.fillStyle = "#40534a";
        context.fillText(label, 0, 0);
        context.restore();
      }
    });
  }

  function drawBarChart(canvas, dataset) {
    const values = dataset.values.map(Number);
    const { context, width, height } = prepareCanvas(canvas);
    if (!values.length) return drawEmpty(context, width, height);
    const padding = { top: 24, right: 24, bottom: 64, left: 78 };
    const maxValue = Math.max(...values, 0) || 1;
    drawAxes(context, width, height, padding, maxValue);
    const plotWidth = width - padding.left - padding.right;
    const plotHeight = height - padding.top - padding.bottom;
    const slot = plotWidth / values.length;
    const barWidth = Math.min(slot * 0.58, 70);
    values.forEach((value, index) => {
      const heightValue = plotHeight * (value / maxValue);
      const x = padding.left + slot * index + (slot - barWidth) / 2;
      const y = padding.top + plotHeight - heightValue;
      context.fillStyle = index % 2 ? "#3c8061" : "#176742";
      context.fillRect(x, y, barWidth, heightValue);
      context.fillStyle = "#40534a";
      context.textAlign = "center";
      context.fillText(dataset.labels[index], x + barWidth / 2, height - 28);
    });
  }

  function render() {
    const temporal = document.getElementById("temporal-chart");
    const branches = document.getElementById("branch-chart");
    if (temporal) drawLineChart(temporal, dashboard.temporal);
    if (branches) drawBarChart(branches, dashboard.branches);
  }

  let resizeTimer;
  window.addEventListener("resize", () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(render, 120);
  });
  render();
})();

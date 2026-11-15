const modeButtons = document.querySelectorAll("[data-upload-mode]");
const modePanels = document.querySelectorAll("[data-upload-panel]");

modeButtons.forEach((button) => {
  button.addEventListener("click", () => {
    const selectedMode = button.dataset.uploadMode;
    modeButtons.forEach((item) => {
      const active = item === button;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    modePanels.forEach((panel) => {
      panel.hidden = panel.dataset.uploadPanel !== selectedMode;
    });
  });
});

function isoDate(value) {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function displayDate(value) {
  return new Intl.DateTimeFormat("es-MX", {day: "2-digit", month: "2-digit"}).format(value);
}

function localDate(value) {
  return value ? new Date(`${value}T12:00:00`) : null;
}

function mondayOf(value) {
  const result = new Date(value);
  const day = result.getDay() || 7;
  result.setDate(result.getDate() - day + 1);
  return result;
}

async function fetchCoverage(startDate, days) {
  const response = await fetch(
    `/imports/coverage?start_date=${encodeURIComponent(startDate)}&days=${days}`,
    {headers: {Accept: "application/json"}},
  );
  if (!response.ok) throw new Error("coverage-unavailable");
  return response.json();
}

function configureReplacement(container, existing) {
  const state = container.querySelector("[data-existing-state]");
  const control = container.querySelector("[data-replace-control]");
  const checkbox = container.querySelector("[data-replace-checkbox]");
  const value = container.querySelector("[data-replace-value]");
  container.dataset.hasExisting = String(existing);
  if (state) state.hidden = !existing;
  const selected = Boolean(container.querySelector('input[type="file"]')?.files?.length);
  if (control) control.hidden = !existing || !selected;
  if (checkbox) {
    checkbox.checked = false;
    checkbox.onchange = () => {
      if (value) value.value = checkbox.checked ? "true" : "false";
      container.classList.toggle("will-replace", checkbox.checked);
    };
  }
  if (value) value.value = "false";
  container.classList.remove("will-replace");
}

function showProgress(form, button, message) {
  const progress = form.querySelector("[data-upload-progress]");
  if (progress) progress.hidden = false;
  modeButtons.forEach((item) => { item.disabled = true; });
  button.disabled = true;
  button.textContent = message;
}

const dailyForm = document.querySelector("#daily-upload-form");
const dailyButton = document.querySelector("#submit-daily-upload");
const dailyDate = document.querySelector("#sales-date");
const dailyFileInputs = Array.from(document.querySelectorAll("[data-daily-file]"));

function updateDailyFiles() {
  let selectedCount = 0;
  dailyFileInputs.forEach((input) => {
    const card = input.closest("[data-daily-upload-card]");
    const state = card?.querySelector("[data-file-state]");
    const selected = Boolean(input.files?.length);
    const existing = card?.dataset.hasExisting === "true";
    if (selected) selectedCount += 1;
    card?.classList.toggle("has-file", selected);
    const replaceControl = card?.querySelector("[data-replace-control]");
    if (replaceControl) {
      replaceControl.hidden = card?.dataset.hasExisting !== "true" || !selected;
    }
    if (state) state.textContent = selected ? "Listo" : existing ? "Ya cargado" : "Falta archivo";
  });
  if (dailyButton) {
    dailyButton.disabled = selectedCount === 0 || !dailyDate?.value;
    dailyButton.textContent = selectedCount
      ? `Procesar día (${selectedCount} ${selectedCount === 1 ? "archivo" : "archivos"})`
      : "Procesar día";
  }
}

async function updateDailyCoverage() {
  const cards = Array.from(document.querySelectorAll("[data-daily-upload-card]"));
  cards.forEach((card) => configureReplacement(card, false));
  if (!dailyDate?.value) return;
  try {
    const data = await fetchCoverage(dailyDate.value, 1);
    const existingIds = new Set(data.slots.map((slot) => String(slot.branch_id)));
    cards.forEach((card) => {
      const branchId = card.querySelector("[data-branch-input]")?.value;
      configureReplacement(card, existingIds.has(String(branchId)));
    });
    updateDailyFiles();
  } catch (_error) {
    // El servidor mantiene la protección aunque el aviso previo no esté disponible.
  }
}

dailyFileInputs.forEach((input) => input.addEventListener("change", updateDailyFiles));
dailyDate?.addEventListener("change", () => {
  updateDailyFiles();
  updateDailyCoverage();
});
updateDailyFiles();

if (dailyForm && dailyButton) {
  dailyForm.addEventListener("submit", () => {
    dailyFileInputs.forEach((input) => {
      if (!input.files?.length) {
        const card = input.closest("[data-daily-upload-card]");
        input.disabled = true;
        card?.querySelectorAll("[data-branch-input], [data-replace-value]")
          .forEach((item) => { item.disabled = true; });
      }
    });
    showProgress(dailyForm, dailyButton, "Procesando el día…");
  });
}

const weeklyForm = document.querySelector("#weekly-upload-form");
const weekDate = document.querySelector("#week-date");
const weeklyButton = document.querySelector("#submit-weekly-upload");
const weeklySummary = document.querySelector("#weekly-file-summary");
const weeklySlots = Array.from(document.querySelectorAll("[data-weekly-slot]"));
const weeklyFileInputs = weeklySlots.map((slot) => slot.querySelector("[data-weekly-file]"));

function updateWeeklyGrid() {
  let selectedCount = 0;
  let existingCount = 0;
  const counts = Array(7).fill(0);
  weeklySlots.forEach((slot) => {
    const input = slot.querySelector("[data-weekly-file]");
    const filename = slot.querySelector("[data-slot-filename]");
    const action = slot.querySelector("[data-slot-action]");
    const state = slot.querySelector("[data-slot-state]");
    const selected = Boolean(input?.files?.length);
    const existing = slot.dataset.hasExisting === "true";
    const dayIndex = Number(slot.dataset.dayIndex);
    if (existing) existingCount += 1;
    if (selected) {
      selectedCount += 1;
    }
    if (selected || existing) counts[dayIndex] += 1;
    slot.classList.toggle("has-file", selected);
    slot.classList.toggle("has-existing", existing && !selected);
    const replaceControl = slot.querySelector("[data-replace-control]");
    if (replaceControl) {
      replaceControl.hidden = slot.dataset.hasExisting !== "true" || !selected;
    }
    if (filename) filename.textContent = selected ? input.files[0].name : "Ningún archivo";
    if (action) action.textContent = selected ? "Cambiar archivo" : "Elegir archivo";
    if (state) state.textContent = selected ? "Listo" : existing ? "Cargado" : "Falta";
    const existingState = slot.querySelector("[data-existing-state]");
    if (existingState) existingState.hidden = !existing || !selected;
  });

  counts.forEach((count, index) => {
    const row = document.querySelector(`[data-week-row="${index}"]`);
    const dayCount = row?.querySelector("[data-day-count]");
    row?.classList.toggle("is-complete", count === 4);
    if (dayCount) dayCount.textContent = `${count} de 4 cargados`;
  });
  const completeDays = counts.filter((count) => count === 4).length;
  if (weeklySummary) {
    weeklySummary.textContent = selectedCount
      ? `${selectedCount} ${selectedCount === 1 ? "archivo nuevo seleccionado" : "archivos nuevos seleccionados"}. ${existingCount} cargas ya existentes.`
      : weekDate?.value
        ? `${existingCount} cargas ya existentes. ${completeDays} ${completeDays === 1 ? "día completo" : "días completos"}.`
        : "Seleccione una fecha para preparar la semana.";
  }
  if (weeklyButton) {
    weeklyButton.disabled = selectedCount === 0 || !weekDate?.value;
    weeklyButton.textContent = selectedCount
      ? `Procesar ${selectedCount} ${selectedCount === 1 ? "archivo" : "archivos"}`
      : "Procesar semana";
  }
}

async function prepareWeek() {
  weeklySlots.forEach((slot) => configureReplacement(slot, false));
  const chosen = localDate(weekDate?.value);
  if (!chosen) {
    updateWeeklyGrid();
    return;
  }
  const monday = mondayOf(chosen);
  const dates = [];
  for (let index = 0; index < 7; index += 1) {
    const current = new Date(monday);
    current.setDate(monday.getDate() + index);
    dates.push(current);
    const row = document.querySelector(`[data-week-row="${index}"]`);
    const label = row?.querySelector("[data-week-day-label]");
    if (label) label.textContent = displayDate(current);
  }
  weeklySlots.forEach((slot) => {
    const slotDate = slot.querySelector("[data-slot-date]");
    if (slotDate) slotDate.value = isoDate(dates[Number(slot.dataset.dayIndex)]);
  });
  try {
    const data = await fetchCoverage(isoDate(monday), 7);
    const existing = new Set(data.slots.map((slot) => `${slot.sales_date}:${slot.branch_id}`));
    weeklySlots.forEach((slot) => {
      const slotDate = slot.querySelector("[data-slot-date]")?.value;
      configureReplacement(slot, existing.has(`${slotDate}:${slot.dataset.branchId}`));
    });
  } catch (_error) {
    if (weeklySummary) {
      weeklySummary.textContent = "Semana preparada. La protección contra duplicados se comprobará al procesar.";
    }
  }
  updateWeeklyGrid();
}

weeklyFileInputs.forEach((input) => input?.addEventListener("change", updateWeeklyGrid));
weekDate?.addEventListener("change", prepareWeek);
updateWeeklyGrid();

if (weeklyForm && weeklyButton) {
  weeklyForm.addEventListener("submit", () => {
    weeklySlots.forEach((slot) => {
      const input = slot.querySelector("[data-weekly-file]");
      if (!input?.files?.length) {
        slot.querySelectorAll(
          "[data-weekly-file], [data-slot-date], [data-slot-branch], [data-replace-value]",
        ).forEach((item) => { item.disabled = true; });
      }
    });
    const count = weeklyFileInputs.filter((input) => input?.files?.length).length;
    showProgress(weeklyForm, weeklyButton, `Procesando ${count} archivos…`);
  });
}

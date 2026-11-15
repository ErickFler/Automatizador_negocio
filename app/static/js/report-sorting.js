(() => {
  "use strict";

  const field = document.getElementById("sort-field");
  const direction = document.getElementById("sort-direction");
  if (!field || !direction) return;

  function updateDirectionLabels() {
    const alphabetical = ["name", "category"].includes(field.value);
    const descending = direction.querySelector('option[value="desc"]');
    const ascending = direction.querySelector('option[value="asc"]');
    if (descending) descending.textContent = alphabetical ? "Z–A" : "Mayor a menor";
    if (ascending) ascending.textContent = alphabetical ? "A–Z" : "Menor a mayor";
  }

  field.addEventListener("change", updateDirectionLabels);
  updateDirectionLabels();
})();

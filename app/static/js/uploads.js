const uploadList = document.querySelector("#upload-list");
const template = document.querySelector("#upload-template");
const addButton = document.querySelector("#add-upload");
const form = document.querySelector("#upload-form");
const submitButton = document.querySelector("#submit-upload");

function refreshCards() {
  const cards = [...uploadList.querySelectorAll("[data-upload-card]")];
  cards.forEach((card, index) => {
    card.querySelector(".upload-number").textContent = String(index + 1);
    card.querySelector(".remove-upload").hidden = cards.length === 1;
  });
  addButton.disabled = cards.length >= Number(uploadList.dataset.maxFiles);
}

addButton.addEventListener("click", () => {
  const clone = template.content.cloneNode(true);
  uploadList.appendChild(clone);
  refreshCards();
});

uploadList.addEventListener("click", (event) => {
  const removeButton = event.target.closest(".remove-upload");
  if (!removeButton) return;
  removeButton.closest("[data-upload-card]").remove();
  refreshCards();
});

form.addEventListener("submit", () => {
  submitButton.disabled = true;
  submitButton.textContent = "Procesando…";
});

refreshCards();

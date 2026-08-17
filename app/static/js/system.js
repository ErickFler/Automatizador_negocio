const shutdownForm = document.querySelector("[data-shutdown-form]");

if (shutdownForm) {
  shutdownForm.addEventListener("submit", (event) => {
    const confirmed = window.confirm(
      "¿Seguro que deseas apagar el sistema?",
    );
    if (!confirmed) event.preventDefault();
  });
}

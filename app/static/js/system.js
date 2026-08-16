const shutdownForm = document.querySelector("[data-shutdown-form]");

if (shutdownForm) {
  shutdownForm.addEventListener("submit", (event) => {
    const confirmed = window.confirm(
      "¿Deseas apagar el Sistema de Concentrado de Ventas?",
    );
    if (!confirmed) event.preventDefault();
  });
}


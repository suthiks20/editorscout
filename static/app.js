document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-char-count]").forEach((field) => {
    const counter = document.querySelector(`[data-count-for="${field.id}"]`);
    const updateCount = () => {
      if (counter) counter.textContent = String(field.value.length);
    };
    field.addEventListener("input", updateCount);
    updateCount();
  });

  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const field = document.getElementById(button.dataset.copyTarget);
      if (!field) return;
      try {
        await navigator.clipboard.writeText(field.value);
        button.textContent = "Copied";
      } catch {
        field.focus();
        field.select();
        const copied = document.execCommand("copy");
        button.textContent = copied ? "Copied" : "Select and copy";
      }
      window.setTimeout(() => {
        button.textContent = "Copy draft";
      }, 1800);
    });
  });
});

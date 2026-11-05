document.addEventListener("DOMContentLoaded", () => {
  const orgType = document.getElementById("org-type");
  const orgNameField = document.getElementById("org-name-field");
  const orgName = document.getElementById("org-name");
  const orgNameLabel = document.getElementById("org-name-label-text");
  const filterStatus = document.getElementById("filter-status");
  const batchYearField = document.getElementById("batch-year-field");
  const searchForm = document.querySelector(".search-form");
  const personInputField = document.getElementById("person-input-field");
  const modeInputs = document.querySelectorAll('input[name="mode"]');
  const roleInput = document.getElementById("role-input");
  const cityInput = document.getElementById("city-input");

  const updateOrganisationFields = () => {
    if (orgType && orgNameField && orgName && orgNameLabel) {
      const type = orgType.value;
      const visible = Boolean(type);
      orgNameField.hidden = !visible;
      orgName.disabled = !visible;
      if (visible) {
        const name = type === "other" ? "Organisation" : type[0].toUpperCase() + type.slice(1);
        orgNameLabel.textContent = `${name} name`;
        orgName.placeholder = `${name} name`;
      }
      if (type === "college") {
        orgName.setAttribute("list", "college-options");
      } else {
        orgName.removeAttribute("list");
      }
    }
    if (batchYearField) {
      const type = orgType?.value;
      const showYear = (type === "college" || type === "school")
        && filterStatus?.value === "current";
      batchYearField.hidden = !showYear;
      const yearInput = batchYearField.querySelector("input");
      if (yearInput) yearInput.disabled = !showYear;
    }
  };

  orgType?.addEventListener("change", updateOrganisationFields);
  filterStatus?.addEventListener("change", updateOrganisationFields);
  const updateSearchMode = () => {
    const mode = searchForm?.querySelector('input[name="mode"]:checked')?.value || "browse";
    if (personInputField) {
      personInputField.hidden = mode !== "person";
      const input = personInputField.querySelector("input");
      if (input) input.disabled = mode !== "person";
    }
    if (roleInput) {
      roleInput.required = mode === "browse";
    }
    if (cityInput) cityInput.required = mode === "browse";
    const excludeField = document.getElementById("exclude-field");
    const excludeInput = excludeField?.querySelector("input");
    if (excludeInput && mode === "person" && excludeInput.value === "recruiter, hiring, HR, agency") {
      excludeInput.value = "";
    }
  };
  modeInputs.forEach((input) => input.addEventListener("change", updateSearchMode));
  updateOrganisationFields();
  updateSearchMode();

  document.querySelectorAll("[data-confirm]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!window.confirm(form.dataset.confirm)) event.preventDefault();
    });
  });

  document.querySelectorAll("[data-confirm-draft]").forEach((form) => {
    const confirmation = form.querySelector('input[name="confirm_repeat"]');
    const button = form.querySelector("button[type=submit]");
    if (!confirmation || !button) return;
    button.disabled = !confirmation.checked;
    confirmation.addEventListener("change", () => {
      button.disabled = !confirmation.checked;
    });
  });

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

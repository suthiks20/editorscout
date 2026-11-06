document.addEventListener("DOMContentLoaded", () => {
  const orgType = document.getElementById("org-type");
  const orgNameField = document.getElementById("org-name-field");
  const orgName = document.getElementById("org-name");
  const orgNameLabel = document.getElementById("org-name-label-text");
  const filterStatus = document.getElementById("filter-status");
  const batchYearField = document.getElementById("batch-year-field");
  const searchForm = document.querySelector(".search-form");
  const personInputField = document.getElementById("person-input-field");
  const platformFields = document.getElementById("platform-fields");
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
    if (platformFields) platformFields.hidden = mode !== "person";
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
  const updateCreditCount = () => {
    const count = document.querySelectorAll(
      '#platform-fields input[name="platforms"]:checked'
    ).length;
    const countLabel = document.getElementById("platform-credit-estimate");
    if (countLabel) {
      countLabel.textContent = count
        ? `${count} searches will use ${count} Serper credits`
        : "Select at least one platform to search";
    }
  };
  document.querySelectorAll('#platform-fields input[name="platforms"]').forEach((input) => {
    input.addEventListener("change", updateCreditCount);
  });
  updateOrganisationFields();
  updateSearchMode();
  updateCreditCount();

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
      const limit = Number(field.dataset.countLimit);
      const copyAnyway = document.querySelector(`[data-copy-anyway="${field.id}"]`);
      const copyButton = document.querySelector(`[data-copy-target="${field.id}"]`);
      const overLimitNote = document.querySelector(`[data-over-limit-for="${field.id}"]`);
      if (copyButton && limit) {
        copyButton.disabled = field.value.length > limit && !copyAnyway?.checked;
      }
      if (overLimitNote && limit) overLimitNote.hidden = field.value.length <= limit;
    };
    field.addEventListener("input", updateCount);
    updateCount();
  });

  document.querySelectorAll("[data-copy-anyway]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const field = document.getElementById(checkbox.dataset.copyAnyway);
      const button = document.querySelector(`[data-copy-target="${checkbox.dataset.copyAnyway}"]`);
      if (field && button) button.disabled = field.value.length > 299 && !checkbox.checked;
    });
  });

  const commonKind = document.getElementById("common-kind");
  const commonMessage = document.getElementById("common-message");
  const commonLimit = document.getElementById("common-limit");
  const unknownPlaceholders = document.getElementById("unknown-placeholders");
  let activeCommonKey = commonKind?.value === "follow_up" ? "followUp" : "connection";
  const knownPlaceholders = new Set([
    "first_name", "name", "organisation", "role", "specific_note", "my_name", "link",
  ]);
  const updateCommonMessage = () => {
    if (!commonKind || !commonMessage) return;
    const key = commonKind.value === "follow_up" ? "followUp" : "connection";
    if (key !== activeCommonKey) {
      commonMessage.dataset[activeCommonKey] = commonMessage.value;
      activeCommonKey = key;
      commonMessage.value = commonMessage.dataset[key] || "";
    } else {
      commonMessage.dataset[key] = commonMessage.value;
    }
    const isConnection = commonKind.value === "connection";
    commonMessage.dataset.countLimit = isConnection ? "299" : "3000";
    if (commonLimit) commonLimit.textContent = `${isConnection ? "299" : "3000"}${isConnection ? "" : " soft"} characters`;
    const includeContacted = document.getElementById("include-contacted-option");
    if (includeContacted) includeContacted.hidden = !isConnection;
    const count = document.querySelector('[data-count-for="common-message"]');
    if (count) count.textContent = String(commonMessage.value.length);
    const unknown = [...commonMessage.value.matchAll(/\{([^{}]+)\}/g)]
      .map((match) => match[1])
      .filter((name) => !knownPlaceholders.has(name));
    if (unknownPlaceholders) {
      unknownPlaceholders.textContent = unknown.length
        ? `Unknown placeholders: ${[...new Set(unknown)].map((name) => `{${name}}`).join(", ")}`
        : "";
      unknownPlaceholders.classList.toggle("error-text", unknown.length > 0);
    }
  };
  commonKind?.addEventListener("change", updateCommonMessage);
  commonMessage?.addEventListener("input", updateCommonMessage);
  document.querySelectorAll("[data-insert-placeholder]").forEach((button) => {
    button.addEventListener("click", () => {
      if (!commonMessage) return;
      const start = commonMessage.selectionStart;
      const end = commonMessage.selectionEnd;
      const placeholder = button.dataset.insertPlaceholder;
      commonMessage.setRangeText(placeholder, start, end, "end");
      commonMessage.focus();
      commonMessage.dispatchEvent(new Event("input", { bubbles: true }));
    });
  });
  updateCommonMessage();

  document.querySelectorAll("[data-select-people]").forEach((button) => {
    button.addEventListener("click", () => {
      const checked = button.dataset.selectPeople === "all";
      document.querySelectorAll(".shortlist-person-checkbox").forEach((checkbox) => {
        checkbox.checked = checked;
      });
    });
  });

  const queueKey = "editorscout-shortlist-queue";
  const queueMode = document.getElementById("queue-mode");
  const queueControls = document.getElementById("queue-controls");
  const queueCards = [...document.querySelectorAll("[data-queue-card]")];
  const queuePosition = document.getElementById("queue-position");
  let queueIndex = Number(localStorage.getItem(queueKey) || 0);
  const updateQueue = () => {
    if (!queueMode || !queueCards.length) return;
    queueIndex = Math.max(0, Math.min(queueIndex, queueCards.length - 1));
    const enabled = queueMode.checked;
    if (queueControls) queueControls.hidden = !enabled;
    queueCards.forEach((card, index) => {
      card.hidden = enabled && index !== queueIndex;
    });
    if (queuePosition) queuePosition.textContent = `Person ${queueIndex + 1} of ${queueCards.length}`;
    localStorage.setItem(queueKey, String(queueIndex));
    localStorage.setItem(`${queueKey}-enabled`, enabled ? "1" : "0");
  };
  if (queueMode) {
    queueMode.checked = localStorage.getItem(`${queueKey}-enabled`) === "1";
    queueMode.addEventListener("change", updateQueue);
  }
  document.getElementById("queue-next")?.addEventListener("click", () => {
    queueIndex = (queueIndex + 1) % queueCards.length;
    updateQueue();
    window.scrollTo({ top: queueCards[queueIndex].offsetTop, behavior: "smooth" });
  });
  updateQueue();

  document.querySelectorAll("[data-send-from]").forEach((form) => {
    form.addEventListener("submit", () => {
      const field = document.getElementById(form.dataset.sendFrom);
      const hidden = form.querySelector('input[name="sent_text"]');
      if (field && hidden) hidden.value = field.value;
    });
  });

  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const field = document.getElementById(button.dataset.copyTarget);
      if (!field) return;
      const originalLabel = button.dataset.originalLabel || button.textContent;
      button.dataset.originalLabel = originalLabel;
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
        button.textContent = originalLabel;
      }, 1800);
    });
  });
});

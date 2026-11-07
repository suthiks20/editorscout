document.addEventListener("DOMContentLoaded", () => {
  const themeSelect = document.querySelector(".ui-theme-select");
  if (themeSelect) {
    let savedTheme = "dark";
    try {
      savedTheme = window.localStorage.getItem("editorscout-theme") || "dark";
    } catch {
      savedTheme = "dark";
    }
    if (!["auto", "light", "dark"].includes(savedTheme)) savedTheme = "dark";
    themeSelect.value = savedTheme;
    const applyTheme = (theme) => {
      if (theme === "auto") document.documentElement.removeAttribute("data-theme");
      else document.documentElement.dataset.theme = theme;
      const themeColor = document.querySelector('meta[name="theme-color"]');
      const autoDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
      if (themeColor) {
        themeColor.content = theme === "dark" || (theme === "auto" && autoDark)
          ? "#07090f"
          : "#f5f7fb";
      }
    };
    applyTheme(savedTheme);
    themeSelect.addEventListener("change", () => {
      const theme = themeSelect.value;
      applyTheme(theme);
      try {
        window.localStorage.setItem("editorscout-theme", theme);
      } catch {
        // The selected theme remains active for this page if storage is unavailable.
      }
    });
  }

  document.querySelectorAll(".ui-avatar").forEach((avatar) => {
    const name = avatar.closest("td")?.querySelector(".profile-name")?.textContent
      || avatar.closest(".platform-result-card")?.querySelector("strong")?.textContent
      || avatar.textContent;
    let hash = 0;
    for (const character of name.trim().toLowerCase()) {
      hash = (hash * 31 + character.charCodeAt(0)) | 0;
    }
    avatar.classList.add(`ui-avatar-tone-${Math.abs(hash) % 6}`);
  });

  document.querySelectorAll(".ui-toast").forEach((toast) => {
    const dismiss = () => {
      toast.remove();
    };
    toast.querySelector(".ui-toast-dismiss")?.addEventListener("click", dismiss);
    if (toast.getAttribute("role") !== "alert") {
      window.setTimeout(dismiss, 6000);
    }
  });

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
  const widenSearchField = document.getElementById("widen-search-field");
  const searchCreditEstimate = document.getElementById("search-credit-estimate");
  const locationRoot = document.getElementById("location-controls");
  const countrySelect = document.getElementById("loc-country");
  const stateSelect = document.getElementById("loc-state");
  const stateCustom = document.getElementById("loc-state-custom");
  const citySelect = document.getElementById("loc-city");
  const cityCustom = document.getElementById("loc-city-custom");
  const stateHelp = document.getElementById("loc-state-help");
  const cityHelp = document.getElementById("loc-city-help");
  const noLocationData = document.getElementById("loc-no-data");
  const locationCache = new Map();

  const setCustomInput = (input, visible, value = "") => {
    input.hidden = !visible;
    input.disabled = !visible;
    if (!visible) input.value = "";
    if (visible && value) input.value = value;
  };
  const resetSelect = (select, placeholder) => {
    select.replaceChildren(new Option(placeholder, ""));
  };
  const loadCityOptions = (location, selectedCity = "", selectedCustom = "") => {
    resetSelect(citySelect, "Any city");
    const region = location.states.find((item) => item.name === stateSelect.value);
    if (!region) {
      citySelect.disabled = true;
      setCustomInput(cityCustom, false);
      cityHelp.textContent = "Choose a state or region first.";
      return;
    }
    region.cities.forEach((name) => citySelect.add(new Option(name, name)));
    citySelect.add(new Option("Other city (type it)", "__other__"));
    citySelect.disabled = false;
    citySelect.hidden = false;
    const hasCity = region.cities.includes(selectedCity);
    citySelect.value = selectedCustom || (selectedCity && !hasCity) ? "__other__" : selectedCity;
    const customChosen = citySelect.value === "__other__";
    setCustomInput(
      cityCustom,
      customChosen,
      selectedCustom || (customChosen ? selectedCity : "")
    );
    cityHelp.textContent = "Choose a city, or select Other city to type one.";
  };
  const loadLocationOptions = async (countryCode, selectedState = "", selectedCity = "", selectedStateCustom = "", selectedCityCustom = "") => {
    stateSelect.onchange = null;
    citySelect.onchange = null;
    resetSelect(stateSelect, "Any state / region");
    resetSelect(citySelect, "Any city");
    setCustomInput(stateCustom, false);
    setCustomInput(cityCustom, false);
    noLocationData.hidden = true;
    if (!countryCode) {
      stateSelect.disabled = true;
      citySelect.disabled = true;
      stateHelp.textContent = "Choose a country first.";
      cityHelp.textContent = "Choose a state or region first.";
      return;
    }

    noLocationData.textContent = "No city list for this country yet. Type the city or region yourself.";
    let location = locationCache.get(countryCode);
    if (!locationCache.has(countryCode)) {
      try {
        const response = await fetch(`/static/data/locations/${encodeURIComponent(countryCode)}.json`);
        if (response.status === 404) {
          location = null;
          locationCache.set(countryCode, null);
        } else if (!response.ok) {
          throw new Error(`Location list request failed (${response.status}).`);
        } else {
          location = await response.json();
          if (!location || !Array.isArray(location.states)) {
            throw new Error("Location list has an invalid format.");
          }
          locationCache.set(countryCode, location);
        }
      } catch (error) {
        noLocationData.textContent = `${error.message} You can still type the location.`;
        location = null;
      }
    }
    if (countrySelect.value !== countryCode) return;
    if (!location) {
      stateSelect.disabled = true;
      stateSelect.hidden = true;
      citySelect.disabled = true;
      citySelect.hidden = true;
      setCustomInput(stateCustom, true, selectedStateCustom || selectedState);
      setCustomInput(cityCustom, true, selectedCityCustom || selectedCity);
      noLocationData.hidden = false;
      stateHelp.textContent = "Type a state or region if helpful.";
      cityHelp.textContent = "Type a city if helpful.";
      return;
    }

    stateSelect.hidden = false;
    citySelect.hidden = false;
    location.states.forEach((item) => stateSelect.add(new Option(item.name, item.name)));
    stateSelect.add(new Option("Other state / region (type it)", "__other__"));
    stateSelect.disabled = false;
    const stateExists = location.states.some((item) => item.name === selectedState);
    if (selectedStateCustom || (selectedState && !stateExists)) {
      stateSelect.value = "__other__";
      setCustomInput(stateCustom, true, selectedStateCustom || selectedState);
      citySelect.disabled = true;
      citySelect.hidden = true;
      setCustomInput(cityCustom, true, selectedCityCustom || selectedCity);
      cityHelp.textContent = "Type a city for this region.";
    } else {
      stateSelect.value = stateExists ? selectedState : "";
      stateHelp.textContent = "Choose a state or region.";
      loadCityOptions(location, selectedCity, selectedCityCustom);
    }

    stateSelect.onchange = () => {
      setCustomInput(stateCustom, stateSelect.value === "__other__");
      if (stateSelect.value === "__other__") {
        citySelect.disabled = true;
        citySelect.hidden = true;
        cityCustom.value = "";
        setCustomInput(cityCustom, true);
        cityHelp.textContent = "Type a city for this region.";
      } else {
        citySelect.hidden = false;
        setCustomInput(cityCustom, false);
        loadCityOptions(location);
      }
    };
    citySelect.onchange = () => {
      const customChosen = citySelect.value === "__other__";
      setCustomInput(cityCustom, customChosen);
      if (customChosen) cityHelp.textContent = "Type the city name.";
    };
  };
  if (locationRoot && countrySelect) {
    const initialState = locationRoot.dataset.initialState || "";
    const initialCity = locationRoot.dataset.initialCity || "";
    loadLocationOptions(
      countrySelect.value,
      initialState,
      initialCity,
      locationRoot.dataset.initialStateCustom || "",
      locationRoot.dataset.initialCityCustom || ""
    );
    countrySelect.addEventListener("change", () => {
      loadLocationOptions(countrySelect.value);
    });
  }

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
    if (widenSearchField) {
      widenSearchField.hidden = mode !== "browse";
      const input = widenSearchField.querySelector("input");
      if (input) input.disabled = mode !== "browse";
    }
    if (searchCreditEstimate) searchCreditEstimate.hidden = mode !== "browse";
    if (roleInput) {
      roleInput.required = mode === "browse";
    }
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
      if (!form.dataset.confirm) return;
      if (!window.confirm(form.dataset.confirm)) {
        event.preventDefault();
        return;
      }
      const confirmation = form.querySelector('input[name="confirm_all"]');
      if (confirmation) confirmation.value = "1";
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
      const overLimitNote = document.querySelector(`[data-over-limit-for="${field.id}"]`);
      document.querySelectorAll(`[data-copy-target="${field.id}"]`).forEach((button) => {
        const card = button.closest("[data-queue-card]");
        const expanded = card?.querySelector(".shortlist-expanded-panel:not([hidden])");
        const allowed = field.value.length <= limit || Boolean(copyAnyway?.checked && (!button.hasAttribute("data-copy-compact") || expanded));
        if (limit) button.disabled = button.hasAttribute("data-copy-compact")
          ? !field.value || !allowed
          : field.value.length > limit && !copyAnyway?.checked;
      });
      if (overLimitNote && limit) overLimitNote.hidden = field.value.length <= limit;
    };
    field.addEventListener("input", updateCount);
    updateCount();
  });

  document.querySelectorAll("[data-copy-anyway]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      const field = document.getElementById(checkbox.dataset.copyAnyway);
      if (field) field.dispatchEvent(new Event("input", { bubbles: true }));
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

  const queueKey = "editorscout-shortlist-queue";
  const queueMode = document.getElementById("queue-mode");
  const queueControls = document.getElementById("queue-controls");
  const originalQueueCards = [...document.querySelectorAll("[data-queue-card]")];
  let queueCards = [...originalQueueCards];
  const queuePosition = document.getElementById("queue-position");
  const shortlistSearch = document.getElementById("shortlist-search");
  const creatorsFirst = document.getElementById("creators-first");
  const roleFilterButtons = [...document.querySelectorAll("[data-role-filter]")];
  let selectedFilter = "all";
  let selectedRole = "all";
  let queueIndex = 0;
  const getSessionValue = (key) => {
    try {
      return window.sessionStorage.getItem(key);
    } catch {
      return null;
    }
  };
  const setSessionValue = (key, value) => {
    try {
      window.sessionStorage.setItem(key, value);
    } catch {
      // Storage may be disabled by the browser; the page remains usable.
    }
  };
  const removeSessionValue = (key) => {
    try {
      window.sessionStorage.removeItem(key);
    } catch {
      // Storage may be disabled by the browser; the page remains usable.
    }
  };
  const isMatchingFilter = (card, filter) => {
    const hasDraft = card.dataset.hasDraft === "true";
    const isShortlisted = card.dataset.status === "shortlisted";
    const roleMatches = selectedRole === "all" || (
      selectedRole === "Other"
        ? !["Content Creator", "Video Editor", "Videographer"].includes(card.dataset.role)
        : card.dataset.role === selectedRole
    );
    if (!roleMatches) return false;
    if (filter === "needs-draft") return !hasDraft;
    if (filter === "ready") return hasDraft && isShortlisted;
    if (filter === "sent") return ["sent", "connected", "replied", "agreed"].includes(card.dataset.status);
    return true;
  };
  const getFilteredCards = () => {
    const query = shortlistSearch?.value.trim().toLocaleLowerCase() || "";
    return queueCards.filter((card) => {
      const textMatches = !query || card.dataset.name.includes(query)
        || card.dataset.organisation.includes(query)
        || card.dataset.role.toLocaleLowerCase().includes(query)
        || card.dataset.headline.includes(query);
      return textMatches && isMatchingFilter(card, selectedFilter);
    });
  };
  const activeCard = () => queueCards[queueIndex] || queueCards[0];
  const closeCard = (card, forget = false) => {
    const panel = card.querySelector(".shortlist-expanded-panel");
    const toggle = card.querySelector("[data-person-toggle]");
    if (panel) panel.hidden = true;
    if (toggle) {
      toggle.setAttribute("aria-expanded", "false");
      toggle.setAttribute("aria-label", `Expand ${card.querySelector(".shortlist-row-name")?.textContent || "person"}`);
    }
    if (forget && getSessionValue(`${queueKey}-person`) === card.dataset.personId) {
      removeSessionValue(`${queueKey}-person`);
    }
  };
  const openCard = (card, scroll = true) => {
    queueCards.forEach((other) => {
      if (other !== card) closeCard(other);
    });
    const panel = card.querySelector(".shortlist-expanded-panel");
    const toggle = card.querySelector("[data-person-toggle]");
    if (panel) panel.hidden = false;
    if (toggle) {
      toggle.setAttribute("aria-expanded", "true");
      toggle.setAttribute("aria-label", `Collapse ${card.querySelector(".shortlist-row-name")?.textContent || "person"}`);
    }
    queueIndex = queueCards.indexOf(card);
    setSessionValue(`${queueKey}-person`, card.dataset.personId);
    updateVisibility();
    if (scroll) card.scrollIntoView({ behavior: "smooth", block: "nearest" });
  };
  const closeAllCards = (forget = true) => {
    queueCards.forEach((card) => closeCard(card, false));
    if (forget) removeSessionValue(`${queueKey}-person`);
  };
  const updateVisibility = () => {
    if (!queueCards.length) return;
    const filtered = getFilteredCards();
    const firstFiltered = filtered[0];
    if (filtered.length && !filtered.includes(activeCard())) {
      if (activeCard()) closeCard(activeCard(), true);
      queueIndex = queueCards.indexOf(firstFiltered);
    }
    queueCards.forEach((card) => {
      const matches = filtered.includes(card);
      card.hidden = !matches || Boolean(queueMode?.checked && card !== activeCard());
    });
    if (queueMode?.checked && activeCard()) {
      openCardWithoutScroll(activeCard());
    }
    if (queueControls) queueControls.hidden = !queueMode?.checked;
    if (queuePosition && activeCard()) {
      const index = filtered.indexOf(activeCard());
      queuePosition.textContent = filtered.length ? `Person ${Math.max(0, index) + 1} of ${filtered.length}` : "No people match this filter";
    }
  };
  const openCardWithoutScroll = (card) => {
    queueCards.forEach((other) => {
      if (other !== card) closeCard(other);
    });
    const panel = card.querySelector(".shortlist-expanded-panel");
    const toggle = card.querySelector("[data-person-toggle]");
    if (panel) panel.hidden = false;
    if (toggle) toggle.setAttribute("aria-expanded", "true");
  };
  const updateFilterCounts = () => {
    const counts = {
      all: queueCards.length,
      "needs-draft": queueCards.filter((card) => card.dataset.hasDraft !== "true").length,
      ready: queueCards.filter((card) => isMatchingFilter(card, "ready")).length,
      sent: queueCards.filter((card) => isMatchingFilter(card, "sent")).length,
    };
    document.querySelectorAll("[data-filter-count]").forEach((element) => {
      element.textContent = String(counts[element.dataset.filterCount] || 0);
    });
    const roleCounts = { all: queueCards.length, Other: 0 };
    roleFilterButtons.forEach((button) => {
      const role = button.dataset.roleFilter;
      if (role !== "all" && role !== "Other") roleCounts[role] = 0;
    });
    queueCards.forEach((card) => {
      const role = ["Content Creator", "Video Editor", "Videographer"].includes(card.dataset.role)
        ? card.dataset.role
        : "Other";
      roleCounts[role] = (roleCounts[role] || 0) + 1;
    });
    document.querySelectorAll("[data-role-count]").forEach((element) => {
      element.textContent = String(roleCounts[element.dataset.roleCount] || 0);
    });
  };
  const refreshShortlist = () => {
    updateFilterCounts();
    updateVisibility();
  };
  document.querySelectorAll("[data-shortlist-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      selectedFilter = button.dataset.shortlistFilter;
      document.querySelectorAll("[data-shortlist-filter]").forEach((filterButton) => {
        filterButton.setAttribute("aria-pressed", String(filterButton === button));
      });
      refreshShortlist();
    });
  });
  roleFilterButtons.forEach((button) => {
    button.addEventListener("click", () => {
      selectedRole = button.dataset.roleFilter;
      roleFilterButtons.forEach((roleButton) => {
        roleButton.setAttribute("aria-pressed", String(roleButton === button));
      });
      refreshShortlist();
    });
  });
  shortlistSearch?.addEventListener("input", refreshShortlist);
  creatorsFirst?.addEventListener("change", () => {
    const activeId = activeCard()?.dataset.personId;
    const roleOrder = [
      "Content Creator", "Video Editor", "Videographer", "Motion Designer",
      "Photographer", "Social Media", "Designer", "Other",
    ];
    const list = creatorsFirst.checked ? [...originalQueueCards].sort((a, b) => {
      const roleDiff = roleOrder.indexOf(a.dataset.role) - roleOrder.indexOf(b.dataset.role);
      return roleDiff || originalQueueCards.indexOf(a) - originalQueueCards.indexOf(b);
    }) : [...originalQueueCards];
    list.forEach((card) => document.getElementById("shortlist-cards").appendChild(card));
    queueCards = list;
    queueIndex = Math.max(0, queueCards.findIndex((card) => card.dataset.personId === activeId));
    refreshShortlist();
  });
  queueCards.forEach((card) => {
    card.querySelector("[data-person-toggle]")?.addEventListener("click", () => {
      const panel = card.querySelector(".shortlist-expanded-panel");
      if (panel?.hidden) openCard(card);
      else {
        closeCard(card, true);
        updateVisibility();
      }
    });
    card.querySelectorAll("[data-person-tab]").forEach((tab) => {
      const activateTab = () => {
        const name = tab.dataset.personTab;
        card.querySelectorAll("[data-person-tab]").forEach((otherTab) => {
          const selected = otherTab === tab;
          otherTab.setAttribute("aria-selected", String(selected));
          otherTab.tabIndex = selected ? 0 : -1;
        });
        card.querySelectorAll("[data-person-tabpanel]").forEach((panel) => {
          panel.hidden = panel.dataset.personTabpanel !== name;
        });
        tab.focus();
      };
      tab.addEventListener("click", activateTab);
      tab.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
        event.preventDefault();
        const tabs = [...card.querySelectorAll("[data-person-tab]")];
        const step = event.key === "ArrowRight" ? 1 : -1;
        tabs[(tabs.indexOf(tab) + step + tabs.length) % tabs.length].click();
      });
    });
    card.querySelector("[data-quick-send]")?.addEventListener("click", (event) => {
      const form = document.getElementById(card.querySelector("[data-quick-send]").getAttribute("form"));
      const confirmation = form?.querySelector('input[name="confirm_duplicate"][required]');
      if (confirmation && !confirmation.checked) {
        event.preventDefault();
        openCard(card);
        card.querySelector('[data-person-tab="draft"]')?.click();
        confirmation.focus();
      }
    });
  });
  let toastTimer;
  const sendToast = document.getElementById("sl-send-toast");
  const showSendToast = (message) => {
    if (!sendToast) return;
    sendToast.textContent = message;
    sendToast.hidden = false;
    window.clearTimeout(toastTimer);
    toastTimer = window.setTimeout(() => {
      sendToast.hidden = true;
    }, 5000);
  };
  document.querySelectorAll("[data-send-open]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (button.disabled) return;
      const field = document.getElementById(button.dataset.sendDraft);
      const card = button.closest("[data-queue-card]");
      if (!field || !card) return;
      const copyAnyway = card.querySelector(`[data-copy-anyway="${field.id}"]`);
      if (field.value.length > 299 && !copyAnyway?.checked) {
        openCard(card);
        card.querySelector('[data-person-tab="draft"]')?.click();
        card.querySelector(`[data-over-limit-for="${field.id}"]`)?.removeAttribute("hidden");
        showSendToast("This connection note exceeds 299 characters. Shorten it or select Copy anyway.");
        return;
      }

      const clipboardWrite = navigator.clipboard?.writeText(field.value);
      window.open(button.dataset.sendTarget, "_blank", "noopener,noreferrer");
      try {
        if (!clipboardWrite) throw new Error("Clipboard API unavailable");
        await clipboardWrite;
        showSendToast("Copied. Paste it in the DM, then press Mark sent.");
      } catch {
        openCard(card);
        card.querySelector('[data-person-tab="draft"]')?.click();
        field.focus();
        field.select();
        showSendToast("Clipboard access was denied. The message is selected; copy it manually, paste in the DM, then press Mark sent.");
      }
    });
  });
  const shortlistForm = document.getElementById("shortlist-results-form");
  if (shortlistForm) {
    const selectable = [...shortlistForm.querySelectorAll(
      '.results-table input[name="profile"]:not(:disabled)'
    )];
    const headerToggle = shortlistForm.querySelector("[data-select-page-results]");
    const submit = shortlistForm.querySelector("[data-selection-submit]");
    const countLabels = document.querySelectorAll("[data-selected-count]");
    const scopeOptions = document.querySelectorAll('input[name="selection_scope"]');
    const scopeFieldset = document.querySelector(".pool-scope");
    const total = selectable.length;
    const poolTotal = Number(scopeFieldset?.dataset.poolCount || 0);
    const poolUrl = shortlistForm.dataset.poolUrl;
    const setScope = () => {
      const poolScope = document.querySelector('input[name="selection_scope"]:checked')?.value === "pool";
      if (poolScope) {
        selectable.forEach((checkbox) => {
          checkbox.checked = true;
          checkbox.disabled = true;
        });
        if (headerToggle) {
          headerToggle.checked = total > 0;
          headerToggle.disabled = true;
          headerToggle.indeterminate = false;
        }
        shortlistForm.action = poolUrl;
        shortlistForm.dataset.confirm =
          `Add all ${poolTotal} discovered profiles to your shortlist? Open each profile to check it before you contact anyone.`;
        countLabels.forEach((label) => {
          label.textContent = `${poolTotal} discovered profiles selected`;
        });
        if (submit) {
          submit.disabled = poolTotal === 0;
          submit.textContent = `Add all ${poolTotal} to shortlist`;
        }
        return;
      }
      selectable.forEach((checkbox) => {
        checkbox.disabled = false;
      });
      if (headerToggle) headerToggle.disabled = false;
      shortlistForm.action = shortlistForm.dataset.pageUrl;
      updateSelection();
    };
    const updateSelection = () => {
      const selected = selectable.filter((checkbox) => checkbox.checked).length;
      const allSelected = total > 0 && selected === total;
      const someSelected = selected > 0 && !allSelected;
      [headerToggle].forEach((checkbox) => {
        if (!checkbox || checkbox.disabled) return;
        checkbox.checked = allSelected;
        checkbox.indeterminate = someSelected;
      });
      countLabels.forEach((label) => {
        label.textContent = `${selected} of ${total} selected`;
      });
      if (submit) {
        submit.disabled = selected === 0;
        submit.textContent = allSelected
          ? `Add all ${total} to shortlist`
          : "Add selected to shortlist";
      }
      if (allSelected) {
        shortlistForm.dataset.confirm =
          `Add all ${total} profiles to your shortlist? Open each profile to check it before you contact anyone.`;
      } else {
        shortlistForm.dataset.confirm = "";
      }
      const confirmation = shortlistForm.querySelector('input[name="confirm_all"]');
      if (confirmation) confirmation.value = "0";
    };
    const setAll = (checked) => {
      selectable.forEach((checkbox) => {
        checkbox.checked = checked;
      });
      updateSelection();
    };
    headerToggle?.addEventListener("change", () => setAll(headerToggle.checked));
    scopeOptions.forEach((radio) => {
      radio.addEventListener("change", setScope);
    });
    selectable.forEach((checkbox) => {
      checkbox.addEventListener("change", updateSelection);
    });
    shortlistForm.dataset.pageUrl = shortlistForm.action;
    updateSelection();
  }
  document.querySelectorAll("[data-select-people]").forEach((button) => {
    button.addEventListener("click", () => {
      const checked = button.dataset.selectPeople === "all";
      queueCards.filter((card) => !card.hidden).forEach((card) => {
        const checkbox = card.querySelector(".shortlist-person-checkbox");
        if (checkbox) checkbox.checked = checked;
      });
    });
  });
  document.getElementById("collapse-all")?.addEventListener("click", () => closeAllCards());
  const navigateQueue = (direction, needsSendingOnly = false) => {
    const filtered = getFilteredCards();
    if (!filtered.length) return;
    const currentIndex = filtered.indexOf(activeCard());
    for (let offset = 1; offset < filtered.length; offset += 1) {
      const candidate = filtered[(currentIndex + direction * offset + filtered.length * 2) % filtered.length];
      if (!needsSendingOnly || candidate.dataset.needsSending === "true") {
        openCard(candidate);
        return;
      }
    }
    if (queuePosition) queuePosition.textContent = "No one else needs sending";
  };
  if (queueMode) {
    queueMode.checked = getSessionValue(`${queueKey}-enabled`) === "1";
    queueMode.addEventListener("change", () => {
      setSessionValue(`${queueKey}-enabled`, queueMode.checked ? "1" : "0");
      updateVisibility();
      if (queueMode.checked && activeCard()) openCard(activeCard());
    });
  }
  document.getElementById("queue-previous")?.addEventListener("click", () => navigateQueue(-1));
  document.getElementById("queue-next")?.addEventListener("click", () => navigateQueue(1, true));
  refreshShortlist();
  const savedPersonId = getSessionValue(`${queueKey}-person`);
  const savedCard = queueCards.find((card) => card.dataset.personId === savedPersonId);
  if (savedCard) {
    requestAnimationFrame(() => openCard(savedCard));
  } else if (queueMode?.checked && activeCard()) {
    openCardWithoutScroll(activeCard());
  }

  document.addEventListener("keydown", (event) => {
    if (event.altKey || event.ctrlKey || event.metaKey) return;
    if (event.target.matches("input, textarea, select, [contenteditable=true], button, a")) return;
    const current = activeCard();
    if (!current) return;
    if (event.key.toLowerCase() === "j") {
      event.preventDefault();
      navigateQueue(1);
    } else if (event.key.toLowerCase() === "k") {
      event.preventDefault();
      navigateQueue(-1);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const panel = current.querySelector(".shortlist-expanded-panel");
      if (panel?.hidden) openCard(current);
      else closeCard(current, true);
    } else if (event.key.toLowerCase() === "c") {
      event.preventDefault();
      const copyButton = current.querySelector(".compact-copy:not(:disabled)");
      copyButton?.click();
    } else if (event.key.toLowerCase() === "o") {
      event.preventDefault();
      current.querySelector("[data-send-open][data-send-compact]:not(:disabled)")?.click();
    } else if (event.key.toLowerCase() === "s") {
      event.preventDefault();
      current.querySelector("[data-quick-send]:not(:disabled)")?.click();
    }
  });

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

  const showWorking = (button) => {
    if (!button || button.disabled || button.classList.contains("is-working")) return;
    button.classList.add("is-working");
    button.disabled = true;
    const spinner = document.createElement("span");
    spinner.className = "ui-spinner";
    spinner.setAttribute("aria-hidden", "true");
    button.replaceChildren(spinner, document.createTextNode("Working..."));
  };
  document.querySelectorAll(".search-form, .pool-collect-form, #common-message-form").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (event.defaultPrevented) return;
      const submitter = event.submitter;
      if (!submitter) return;
      const isSearch = form.matches(".search-form, .pool-collect-form");
      const isGenerate = form.id === "common-message-form"
        && submitter.name === "action"
        && submitter.value === "generate";
      if (isSearch || isGenerate) showWorking(submitter);
    });
  });
  document.querySelectorAll(".ui-export-link").forEach((link) => {
    link.addEventListener("click", () => {
      if (link.classList.contains("is-working")) return;
      link.classList.add("is-working");
      link.setAttribute("aria-disabled", "true");
      const spinner = document.createElement("span");
      spinner.className = "ui-spinner";
      spinner.setAttribute("aria-hidden", "true");
      link.replaceChildren(spinner, document.createTextNode("Working..."));
    });
  });
});

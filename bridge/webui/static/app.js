// DSSA web UI: live run terminal (4.46) and catalog filter. No inline scripts (CSP).
"use strict";
(function () {
  // Catalog (integration pass 1): filter rows as you type. Without
  // JavaScript the same search runs on the server through the form.
  const list = document.getElementById("app-list");
  const search = document.getElementById("app-search");
  const category = document.getElementById("app-category");
  const planForm = document.getElementById("plan-form");
  const filters = document.getElementById("filters");

  // Selection (decision A): every ticked application goes into one plan.
  function picks() {
    return Array.from(document.querySelectorAll(".pick"))
      .filter((p) => p.type === "hidden" || p.checked);
  }

  function syncSelection() {
    if (!planForm) return;
    const chosen = picks();
    const ids = chosen.map((p) => p.dataset.id);
    document.getElementById("sel-count").textContent = String(chosen.length);
    const names = document.getElementById("sel-list");
    names.replaceChildren(...chosen.map((p) => {
      const item = document.createElement("li");
      item.textContent = p.dataset.name + " ";
      // Remove from the selection, also when the filter hides its row.
      const drop = document.createElement("button");
      drop.type = "button";
      drop.className = "link";
      drop.textContent = "Remove";
      drop.setAttribute("aria-label", "Remove " + p.dataset.name + " from the selection");
      drop.addEventListener("click", function () {
        if (p.type === "hidden") p.remove(); else p.checked = false;
        syncSelection();
        document.getElementById("sel-count").focus();
      });
      item.appendChild(drop);
      return item;
    }));
    document.getElementById("sel-empty").hidden = chosen.length > 0;
    const hint = document.getElementById("chosen-hint");
    if (hint) {
      hint.textContent = ids.includes(hint.dataset.id) ? "In your selection."
        : "Tick its box in the list to add it to your selection.";
    }
    document.getElementById("sel-clear").hidden = chosen.length === 0;
    // One plan has one action: offer the ones that fit the selection.
    const states = new Set(chosen.map((p) => p.dataset.installed).filter((v) => v !== ""));
    const mixed = states.size > 1;
    document.getElementById("sel-mixed").hidden = !mixed;
    const radios = Array.from(planForm.querySelectorAll('input[name="operation"]'));
    for (const radio of radios) {
      radio.disabled = mixed || (states.size === 1 && !states.has(radio.dataset.needs));
    }
    const current = radios.find((r) => r.checked);
    if (!current || current.disabled) {
      const first = radios.find((r) => !r.disabled);
      if (first) first.checked = true;
    }
    document.getElementById("draft-choice").hidden = !chosen.some((p) => p.dataset.draft === "1");
    document.getElementById("plan-button").disabled = chosen.length === 0 || mixed;
    // Keep the selection when a row is opened or the filter form is sent.
    if (list) {
      for (const row of list.querySelectorAll("a.app-row")) {
        const url = new URL(row.href);
        url.searchParams.delete("sel");
        for (const id of ids) url.searchParams.append("sel", id);
        row.href = url.pathname + url.search;
      }
    }
    if (filters) {
      for (const old of filters.querySelectorAll(".keep-sel")) old.remove();
      for (const id of ids) {
        const keep = document.createElement("input");
        keep.type = "hidden";
        keep.name = "sel";
        keep.value = id;
        keep.className = "keep-sel";
        filters.appendChild(keep);
      }
    }
  }

  if (planForm) {
    for (const box of document.querySelectorAll("input.pick[type=checkbox]")) {
      box.addEventListener("change", syncSelection);
    }
    syncSelection();
  }

  if (list && search && category) {
    function filter() {
      const needle = search.value.trim().toLowerCase();
      for (const row of list.querySelectorAll("[data-search]")) {
        const match = (!category.value || row.dataset.category === category.value)
          && (!needle || row.dataset.search.includes(needle));
        row.parentElement.hidden = !match;
        // Keep the filter when a row is chosen.
        const url = new URL(row.href);
        url.searchParams.delete("q");
        url.searchParams.delete("category");
        if (search.value.trim()) url.searchParams.set("q", search.value.trim());
        if (category.value) url.searchParams.set("category", category.value);
        row.href = url.pathname + url.search;
      }
    }
    search.addEventListener("input", filter);
    category.addEventListener("change", filter);
  }
  // After choosing a row, keyboard focus moves to its description.
  const chosenApp = document.getElementById("selected-app");
  if (chosenApp) chosenApp.focus();
})();

(function () {
  const terminal = document.getElementById("terminal");
  if (!terminal) return;
  const status = document.getElementById("status");
  const form = document.getElementById("input");
  const job = terminal.dataset.job;
  let offset = 0;
  // Terminal control sequences (colours, cursor moves) are not shown.
  const ansi = /\x1b\[[0-9;?]*[ -\/]*[@-~]|\x1b[()][0-9A-Za-z]|\r(?!\n)/g;

  async function poll() {
    try {
      const reply = await fetch(`/jobs/${job}/poll?offset=${offset}`, {credentials: "same-origin"});
      const view = await reply.json();
      if (view.text) {
        terminal.textContent += view.text.replace(ansi, "");
        terminal.scrollTop = terminal.scrollHeight;
      }
      offset = view.offset;
      if (view.running) {
        status.textContent = "Running…";
        setTimeout(poll, 700);
      } else {
        status.textContent = `Finished, exit code ${view.exit_code}.`;
        form.hidden = true;
      }
    } catch (error) {
      status.textContent = "Lost contact with the web UI; retrying…";
      setTimeout(poll, 2000);
    }
  }

  form.addEventListener("submit", async function (event) {
    event.preventDefault();
    const body = new URLSearchParams(new FormData(form));
    form.elements.line.value = "";
    await fetch(form.action, {method: "POST", body, credentials: "same-origin"});
  });
  poll();
})();

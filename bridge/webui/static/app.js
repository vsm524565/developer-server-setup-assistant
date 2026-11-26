// DSSA web UI: live run terminal (4.46) and catalog filter. No inline scripts (CSP).
"use strict";
(function () {
  // Catalog (integration pass 1): filter rows as you type. Without
  // JavaScript the same search runs on the server through the form.
  const list = document.getElementById("app-list");
  const search = document.getElementById("app-search");
  const category = document.getElementById("app-category");
  if (!list || !search || !category) return;
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
  // After choosing a row, keyboard focus moves to its actions.
  const chosen = document.getElementById("selected-app");
  if (chosen) chosen.focus();
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

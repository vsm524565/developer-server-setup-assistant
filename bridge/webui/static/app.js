// DSSA web UI: live run terminal (4.46). No inline scripts (CSP).
"use strict";
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

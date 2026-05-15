import { api, ApiError } from "../api.js";
import { showToast } from "../components/toast.js";

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function _addPatternRow(slot) {
  const row = document.createElement("div");
  row.className = "row pii-pattern";
  row.innerHTML = `
    <input class="pii-pattern__label" placeholder="LABEL" style="flex: 0 0 140px;">
    <input class="pii-pattern__regex" placeholder="regex (\\bAPP-\\d+\\b)" style="flex: 1;">
    <button type="button" class="btn btn--ghost btn--sm" data-slot="rm">✕</button>`;
  row.querySelector('[data-slot="rm"]').addEventListener("click", () => row.remove());
  slot.appendChild(row);
}

function _readPatterns(slot) {
  const out = [];
  slot.querySelectorAll(".pii-pattern").forEach((row) => {
    const label = row.querySelector(".pii-pattern__label").value.trim();
    const pattern = row.querySelector(".pii-pattern__regex").value;
    if (label && pattern) out.push({ label, pattern });
  });
  return out;
}

function _renderMatches(slot, matches) {
  if (!matches.length) {
    slot.innerHTML = "<div class='t-muted'>Совпадений нет.</div>";
    return;
  }
  slot.innerHTML = `
    <table class="table">
      <thead>
        <tr><th>Метка</th><th>Источник</th><th>Значение</th><th>Позиция</th></tr>
      </thead>
      <tbody>
        ${matches
          .map(
            (m) => `
          <tr>
            <td><code>${_esc(m.label)}</code></td>
            <td><span class="chip ${m.source === "extra" ? "chip--ok" : ""}">${_esc(m.source)}</span></td>
            <td class="t-mono">${_esc(m.value)}</td>
            <td class="t-mono t-muted">${m.start}–${m.end}</td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>`;
}

export async function renderPII(container) {
  const html = await (await fetch("/ui/static/pages/pii.html")).text();
  container.innerHTML = html;

  const patternsSlot = container.querySelector('[data-slot="patterns"]');
  const inputEl = container.querySelector('[data-slot="input"]');
  const maskedEl = container.querySelector('[data-slot="masked"]');
  const matchesEl = container.querySelector('[data-slot="matches"]');

  // Дефолтная строка-пример
  inputEl.value =
    "Андеррайтер Иван Петров просил перезвонить на +7 (905) 123-45-67. " +
    "Скан паспорта 4521 123456 пришёл на ivan@example.com. Заявка APP-12345678.";
  _addPatternRow(patternsSlot);
  patternsSlot.querySelector(".pii-pattern__label").value = "APPLICATION_ID";
  patternsSlot.querySelector(".pii-pattern__regex").value = "\\bAPP-\\d{6,}\\b";

  container.querySelector('[data-slot="add-pattern"]').addEventListener("click", () => {
    _addPatternRow(patternsSlot);
  });
  container.querySelector('[data-slot="clear"]').addEventListener("click", () => {
    inputEl.value = "";
    maskedEl.textContent = "";
    matchesEl.innerHTML = "";
  });
  container.querySelector('[data-slot="run"]').addEventListener("click", async () => {
    const text = inputEl.value;
    if (!text.trim()) {
      showToast("Введите текст", "info");
      return;
    }
    maskedEl.textContent = "...";
    matchesEl.innerHTML = '<span class="loader-l1"></span>';
    try {
      const d = await api.pii_test({ text, extra_patterns: _readPatterns(patternsSlot) });
      maskedEl.textContent = d.masked_text;
      _renderMatches(matchesEl, d.matches || []);
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.code}: ${e.message}` : e.message;
      maskedEl.textContent = "";
      matchesEl.innerHTML = `<div class='t-muted'>${_esc(msg)}</div>`;
      showToast("Ошибка обработки", "error");
    }
  });
}

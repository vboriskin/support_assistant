/* Prompt playground: версии + sandbox-сравнение. */

import { api, ApiError } from "../api.js";
import { showToast } from "../components/toast.js";

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function _renderMarkup(text) {
  return _esc(text).replace(/\[(\d+)\]/g, "<strong>[$1]</strong>").replace(/\n/g, "<br>");
}

function _versionsTable(data) {
  const versions = data.versions || [];
  if (!versions.length) {
    return `<div class="t-secondary">Сохранённых версий пока нет. Baseline берётся из <code>core/prompts/system_assistant.txt</code>.</div>`;
  }
  return `
    <table class="table">
      <thead>
        <tr><th>Создана</th><th>Автор</th><th>Заметка</th><th>Статус</th><th></th></tr>
      </thead>
      <tbody>
        ${versions
          .map(
            (v) => `
          <tr data-id="${_esc(v.id)}">
            <td class="t-mono t-muted">${(v.created_at || "").slice(0, 19).replace("T", " ")}</td>
            <td>${_esc(v.created_by || "—")}</td>
            <td class="t-secondary">${_esc((v.note || "").slice(0, 80))}</td>
            <td>${v.is_active ? `<span class="chip chip--ok">активна</span>` : `<span class="chip">черновик</span>`}</td>
            <td class="row" style="gap: var(--space-2);">
              <button class="btn btn--ghost btn--sm" data-act="load">→ B</button>
              ${v.is_active ? "" : `<button class="btn btn--ghost btn--sm" data-act="activate">Активировать</button>
                                    <button class="btn btn--ghost btn--sm" data-act="delete">✕</button>`}
            </td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>`;
}

async function _loadCases(select) {
  // Подтягиваем eval-кейсы из reports/cases через файловую структуру — а у нас
  // нет /api/evals/cases listing. Поэтому даём минимум: список из последних
  // прогонов, чтобы можно было выбрать query.
  try {
    const runs = await api.listEvalRuns();
    const last = runs.find((r) => r.status === "succeeded");
    if (!last) return;
    const run = await api.getEvalRun(last.run_id);
    const opts = (run.results || [])
      .slice(0, 50)
      .map((c) => `<option value="${_esc(c.case_id)}" data-query="${_esc(c.query)}">${_esc(c.case_id)} — ${_esc(c.query.slice(0, 60))}</option>`)
      .join("");
    if (opts) select.innerHTML += opts;
  } catch {
    // тихо
  }
}

function _renderResult(slot, data, label) {
  if (!data) {
    slot.innerHTML = "<div class='empty-state'>Нет результата</div>";
    return;
  }
  const srcs = (data.sources || [])
    .map(
      (s, i) => `
      <li>
        <strong>[${i + 1}]</strong> ${_esc(s.title || s.source_id)}
        <span class="t-muted">· score ${(s.score || 0).toFixed(3)}</span>
      </li>`
    )
    .join("");
  slot.innerHTML = `
    <h4>${_esc(label)} <span class="t-muted t-body-small">· ${data.model || ""} · ${data.latency_ms} мс</span></h4>
    <div class="message__text">${_renderMarkup(data.answer_text || "")}</div>
    ${srcs ? `<ol class="t-secondary t-body-small" style="margin-top: var(--space-3); padding-left: var(--space-4);">${srcs}</ol>` : ""}`;
}

export async function renderPrompts(container) {
  const html = await (await fetch("/ui/static/pages/prompts.html")).text();
  container.innerHTML = html;

  const versionsSlot = container.querySelector('[data-slot="versions"]');
  const promptA = container.querySelector('[data-slot="prompt-a"]');
  const promptB = container.querySelector('[data-slot="prompt-b"]');
  const caseSelect = container.querySelector('[data-slot="case-select"]');
  const queryInput = container.querySelector('[data-slot="query"]');
  const resultA = container.querySelector('[data-slot="result-a"]');
  const resultB = container.querySelector('[data-slot="result-b"]');

  async function reload() {
    versionsSlot.innerHTML = '<span class="loader-l2"></span>';
    try {
      const data = await api.promptsList();
      versionsSlot.innerHTML = _versionsTable(data);
      // baseline / активная — в A
      const active = (data.versions || []).find((v) => v.is_active);
      promptA.value = active ? active.content : data.baseline_content || "";
      if (!promptB.value) promptB.value = promptA.value;
      versionsSlot.querySelectorAll("tbody tr").forEach((tr) => {
        const id = tr.dataset.id;
        tr.querySelectorAll("[data-act]").forEach((btn) => {
          btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            const v = (data.versions || []).find((x) => x.id === id);
            if (btn.dataset.act === "load") {
              promptB.value = v.content;
              showToast("Загружено в B", "info");
            } else if (btn.dataset.act === "activate") {
              try {
                await api.promptActivate(id);
                showToast("Активировано", "success");
                await reload();
              } catch (err) {
                showToast(`Ошибка: ${err.message}`, "error");
              }
            } else if (btn.dataset.act === "delete") {
              if (!confirm("Удалить версию?")) return;
              try {
                await api.promptDelete(id);
                await reload();
              } catch (err) {
                showToast(`Ошибка: ${err.message}`, "error");
              }
            }
          });
        });
      });
    } catch (e) {
      versionsSlot.innerHTML = `<div class='empty-state'>Не удалось: ${_esc(e.message)}</div>`;
    }
  }

  await reload();
  await _loadCases(caseSelect);

  caseSelect.addEventListener("change", () => {
    const opt = caseSelect.selectedOptions[0];
    if (opt && opt.dataset.query) queryInput.value = opt.dataset.query;
  });

  container.querySelector('[data-slot="run"]').addEventListener("click", async () => {
    const caseId = caseSelect.value || null;
    const query = queryInput.value.trim();
    if (!caseId && !query) {
      showToast("Введите запрос или выберите eval-кейс", "info");
      return;
    }
    resultA.innerHTML = '<span class="loader-l2"></span>';
    resultB.innerHTML = '<span class="loader-l2"></span>';
    try {
      const [a, b] = await Promise.all([
        api.promptPreview({ system_prompt: promptA.value, query, case_id: caseId }),
        api.promptPreview({ system_prompt: promptB.value, query, case_id: caseId }),
      ]);
      _renderResult(resultA, a, "A");
      _renderResult(resultB, b, "B");
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.code}: ${e.message}` : e.message;
      resultA.innerHTML = `<div class='empty-state'>${_esc(msg)}</div>`;
      resultB.innerHTML = "";
    }
  });

  container.querySelector('[data-slot="save-b"]').addEventListener("click", async () => {
    if (!promptB.value.trim()) {
      showToast("B пустой", "info");
      return;
    }
    const note = prompt("Краткая заметка к версии (опционально):", "");
    const activate = confirm("Сразу сделать активной?");
    try {
      await api.promptCreate({ name: "system_assistant", content: promptB.value, note, activate });
      showToast("Сохранено", "success");
      await reload();
    } catch (e) {
      showToast(`Ошибка: ${e.message}`, "error");
    }
  });
}

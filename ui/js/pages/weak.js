/* Витрина слабых ответов: 👎 / нет источников / отказ. */

import { api, ApiError } from "../api.js";
import { showToast } from "../components/toast.js";

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

const REASON_LABEL = {
  feedback_negative: { label: "👎", cls: "chip--bad" },
  no_sources: { label: "нет источников", cls: "chip" },
  declined: { label: "отказ", cls: "chip" },
};

function _reasonChips(reasons) {
  return reasons
    .map((r) => {
      const { label, cls } = REASON_LABEL[r] || { label: r, cls: "chip" };
      return `<span class="chip ${cls}">${_esc(label)}</span>`;
    })
    .join(" ");
}

function _row(item) {
  return `
    <div class="weak-row" data-conv-id="${_esc(item.conversation_id)}" data-msg-id="${_esc(item.message_id)}">
      <div class="weak-row__head">
        <div class="t-secondary t-body-small">
          ${(item.created_at || "").slice(0, 19).replace("T", " ")}
          ${item.ticket_id ? ` · тикет <code>${_esc(item.ticket_id.slice(0, 8))}</code>` : ""}
          · источников: ${item.used_sources_count}
        </div>
        <div class="weak-row__reasons">${_reasonChips(item.reasons)}</div>
      </div>
      <div class="weak-row__query"><strong>Запрос:</strong> ${_esc(item.user_query || "—")}</div>
      <div class="weak-row__answer t-secondary"><strong>Ответ:</strong> ${_esc(item.answer_snippet || "—")}${
        (item.answer_snippet || "").length >= 240 ? "…" : ""
      }</div>
      <div class="weak-row__actions">
        <button type="button" class="btn btn--ghost btn--sm" data-act="add-eval">+ В eval-набор</button>
        <a class="btn btn--ghost btn--sm" href="#/history" data-act="open-history">Открыть в истории</a>
      </div>
    </div>`;
}

async function _addToEval(query) {
  try {
    await api.createEvalCase({
      query: query || "(пустой запрос)",
      edge_case_type: "typical",
      category: null,
      expected_sources: [],
      must_mention: [],
      must_not_mention: [],
      expected_answer_summary: "",
    });
    showToast("Кейс добавлен в eval-набор", "success");
  } catch (e) {
    const msg = e instanceof ApiError ? `${e.code}: ${e.message}` : e.message;
    showToast(`Ошибка: ${msg}`, "error");
  }
}

async function _load(container, params) {
  const wrap = container.querySelector('[data-slot="list-wrap"]');
  wrap.innerHTML = '<span class="loader-l2"></span>';
  try {
    const items = await api.listWeak(params);
    if (!items.length) {
      wrap.innerHTML = "<div class='empty-state'>Слабых ответов в выбранном окне не найдено — отлично.</div>";
      return;
    }
    wrap.innerHTML = `
      <div class="t-secondary t-body-small" style="margin-bottom: var(--space-3);">
        Найдено: ${items.length}
      </div>
      ${items.map(_row).join("")}`;
    wrap.querySelectorAll(".weak-row").forEach((row) => {
      const addBtn = row.querySelector('[data-act="add-eval"]');
      addBtn.addEventListener("click", () => {
        const q = row.querySelector(".weak-row__query")?.textContent || "";
        // отрезаем «Запрос: »
        _addToEval(q.replace(/^Запрос:\s*/, "").trim());
      });
    });
  } catch (e) {
    wrap.innerHTML = `<div class="empty-state">Не удалось загрузить: ${_esc(e.message)}</div>`;
  }
}

export async function renderWeak(container) {
  const html = await (await fetch("/ui/static/pages/weak.html")).text();
  container.innerHTML = html;
  const form = container.querySelector('[data-slot="filters"]');
  const params = () => Object.fromEntries(new FormData(form).entries());
  await _load(container, params());
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    _load(container, params());
  });
}

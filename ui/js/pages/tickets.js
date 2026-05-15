import { api, ApiError } from "../api.js";
import { showToast } from "../components/toast.js";

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function _row(t) {
  return `
    <tr data-id="${_esc(t.id)}">
      <td><code>${_esc(t.external_id)}</code></td>
      <td>${_esc(t.subject)}</td>
      <td><span class="status-chip" data-status="${_esc(t.status)}">${_esc(t.status)}</span></td>
      <td>${_esc(t.module || "—")}</td>
      <td class="t-mono t-muted">${(t.created_at || "").slice(0, 10)}</td>
    </tr>`;
}

function _renderAnalysis(slot, data) {
  const cat = data.categorization?.categorization || {};
  const ans = data.answer || {};
  const draft = data.suggested_response_to_user || "";
  slot.innerHTML = `
    <h4>Результат анализа</h4>
    <div class="grid-2" style="margin-top: var(--space-2);">
      <div class="kv">
        <div><span class="t-secondary">Модуль:</span> <strong>${_esc(cat.module || "—")}</strong></div>
        <div><span class="t-secondary">Категория:</span> ${_esc(cat.category || "—")}</div>
        <div><span class="t-secondary">Тип:</span> ${_esc(cat.type || "—")}</div>
        <div><span class="t-secondary">Срочность:</span> ${_esc(cat.urgency || "—")}</div>
        <div><span class="t-secondary">Уверенность:</span> ${cat.confidence != null ? (cat.confidence * 100).toFixed(0) + "%" : "—"}</div>
      </div>
      <div class="kv">
        <div><span class="t-secondary">Группа:</span> ${_esc(cat.suggested_assignee_group || "—")}</div>
        <div><span class="t-secondary">Application ID:</span> ${_esc(cat.extracted_application_id || "—")}</div>
        ${cat.reasoning ? `<div class="t-secondary" style="margin-top: var(--space-2);">${_esc(cat.reasoning)}</div>` : ""}
      </div>
    </div>
    <h4 style="margin-top: var(--space-3);">Ответ ассистента</h4>
    <div class="message__text">${_esc(ans.text || "").replace(/\n/g, "<br>")}</div>
    ${draft ? `<h4 style="margin-top: var(--space-3);">Драфт ответа клиенту</h4>
      <pre class="draft-block">${_esc(draft)}</pre>` : ""}`;
}

async function _renderDetail(wrap, id) {
  const existing = wrap.querySelector('[data-slot="detail"]');
  if (existing) existing.remove();
  const card = document.createElement("div");
  card.className = "card";
  card.dataset.slot = "detail";
  card.style.marginTop = "var(--space-4)";
  card.innerHTML = '<span class="loader-l2"></span>';
  wrap.appendChild(card);
  let t;
  try {
    t = await api.getTicket(id);
  } catch (e) {
    card.innerHTML = `<div class="t-muted">Не удалось загрузить тикет</div>`;
    return;
  }
  card.innerHTML = `
    <header class="detail__header">
      <div>
        <h3>${_esc(t.subject)}</h3>
        <div class="t-secondary">${_esc(t.external_id)} · ${_esc(t.module || "—")} · ${(t.created_at || "").slice(0, 10)}</div>
      </div>
      <div class="detail__actions">
        <button type="button" class="btn btn--ghost btn--sm" data-act="analyze">Анализ</button>
        <button type="button" class="btn btn--ghost btn--sm" data-act="reindex">Переиндексировать</button>
      </div>
    </header>
    <p style="margin-top: var(--space-3); white-space: pre-wrap;">${_esc(t.description || "")}</p>
    ${t.summary ? `<h4 style="margin-top: var(--space-3);">Выжимка</h4>
      <p>${_esc(t.summary.summary_one_line)}</p>
      <p class="t-secondary"><strong>Симптом:</strong> ${_esc(t.summary.symptom)}</p>
      ${t.summary.solution_steps?.length ? `<ol style="margin-left: var(--space-4); margin-top: var(--space-2);">${t.summary.solution_steps.map((s) => `<li>${_esc(s)}</li>`).join("")}</ol>` : ""}` : ""}
    <div data-slot="analysis"></div>`;

  card.querySelector('[data-act="reindex"]').addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "Идёт переиндексация…";
    try {
      await api.reindexTicket(t.id);
      showToast("Тикет переиндексирован", "success");
      btn.textContent = "Переиндексировать";
    } catch (err) {
      const msg = err instanceof ApiError ? err.message || err.code : "ошибка";
      showToast(`Не удалось переиндексировать: ${msg}`, "error");
      btn.textContent = "Переиндексировать";
    } finally {
      btn.disabled = false;
    }
  });

  card.querySelector('[data-act="analyze"]').addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    const analysis = card.querySelector('[data-slot="analysis"]');
    analysis.innerHTML = '<span class="loader-l1"></span>';
    btn.disabled = true;
    try {
      const data = await api.analyze({
        subject: t.subject,
        description: t.description || t.subject,
        channel: t.channel || null,
        author_role: t.author_role || null,
      });
      _renderAnalysis(analysis, data);
    } catch (err) {
      analysis.innerHTML = `<div class="t-muted">Не удалось выполнить анализ</div>`;
      showToast("Анализ не удался", "error");
    } finally {
      btn.disabled = false;
    }
  });
}

async function _load(container, params) {
  const wrap = container.querySelector('[data-slot="table-wrap"]');
  wrap.innerHTML = '<span class="loader-l2"></span>';
  const data = await api.listTickets(params);
  const items = data.items || [];
  if (!items.length) {
    wrap.innerHTML = "<div class='empty-state'>По заданным фильтрам тикеты не найдены.</div>";
    return;
  }
  wrap.innerHTML = `
    <table class="table">
      <thead>
        <tr><th>ID</th><th>Тема</th><th>Статус</th><th>Модуль</th><th>Создан</th></tr>
      </thead>
      <tbody>${items.map(_row).join("")}</tbody>
    </table>`;
  wrap.querySelectorAll("tbody tr").forEach((tr) => {
    tr.addEventListener("click", () => _renderDetail(wrap, tr.dataset.id));
  });
}

export async function renderTickets(container) {
  const html = await (await fetch("/ui/static/pages/tickets.html")).text();
  container.innerHTML = html;

  const form = container.querySelector('[data-slot="filters"]');
  await _load(container, {});

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    _load(container, Object.fromEntries(fd.entries()));
  });
}

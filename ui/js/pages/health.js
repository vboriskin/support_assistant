import { api } from "../api.js";

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function _statusBadge(s) {
  if (s === "ok") return `<span class="chip chip--ok">OK</span>`;
  if (s === "error") return `<span class="chip chip--bad">FAIL</span>`;
  return `<span class="chip">${_esc(s || "—")}</span>`;
}

async function _renderChecks(slot) {
  slot.innerHTML = '<span class="loader-l2"></span>';
  try {
    const d = await api.healthDetails();
    slot.innerHTML = `
      <div class="row" style="justify-content: space-between; align-items: center; margin-bottom: var(--space-3);">
        <h3 style="margin: 0;">Статус: ${_statusBadge(d.status)}</h3>
      </div>
      <table class="table">
        <thead>
          <tr><th>Компонент</th><th>Статус</th><th>Латентность</th><th>Ошибка</th></tr>
        </thead>
        <tbody>
          ${d.checks
            .map(
              (c) => `
            <tr>
              <td>${_esc(c.name)}</td>
              <td>${_statusBadge(c.status)}</td>
              <td class="t-mono t-muted">${c.latency_ms != null ? c.latency_ms + " мс" : "—"}</td>
              <td class="t-muted">${_esc(c.error || "")}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  } catch (e) {
    slot.innerHTML = `<div class='empty-state'>Не удалось получить health: ${_esc(e.message)}</div>`;
  }
}

async function _renderCoverage(slot) {
  slot.innerHTML = '<span class="loader-l2"></span>';
  try {
    const d = await api.coverage();
    const cov = d.summaries_coverage_pct ?? 0;
    slot.innerHTML = `
      <div class="grid-4">
        <div class="kpi"><div class="kpi__value">${d.tickets_total}</div><div class="kpi__label">Тикетов</div></div>
        <div class="kpi"><div class="kpi__value">${d.summaries_total}</div><div class="kpi__label">Суммаризаций</div></div>
        <div class="kpi"><div class="kpi__value">${cov}%</div><div class="kpi__label">Покрытие summary</div></div>
        <div class="kpi"><div class="kpi__value">${d.kb_total}</div><div class="kpi__label">KB-статей${d.kb_deprecated ? ` <span class="t-muted">(deprecated ${d.kb_deprecated})</span>` : ""}</div></div>
      </div>
      <table class="table" style="margin-top: var(--space-4);">
        <thead>
          <tr><th>Модуль</th><th>Тикеты</th><th>Summary</th><th>KB</th></tr>
        </thead>
        <tbody>
          ${(d.modules || [])
            .map(
              (m) => `
            <tr>
              <td>${_esc(m.module)}</td>
              <td class="t-mono">${m.tickets}</td>
              <td class="t-mono">${m.summaries}</td>
              <td class="t-mono">${m.kb_articles}</td>
            </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
  } catch (e) {
    slot.innerHTML = `<div class='empty-state'>Не удалось получить coverage: ${_esc(e.message)}</div>`;
  }
}

export async function renderHealth(container) {
  const html = await (await fetch("/ui/static/pages/health.html")).text();
  container.innerHTML = html;
  const checks = container.querySelector('[data-slot="checks"]');
  const coverage = container.querySelector('[data-slot="coverage"]');
  if (!checks || !coverage) return; // навигация уже сменилась
  await Promise.all([_renderChecks(checks), _renderCoverage(coverage)]);
  const refresh = container.querySelector('[data-slot="refresh"]');
  if (!refresh) return; // контейнер уже перерендерился — выходим тихо
  refresh.addEventListener("click", () => {
    _renderChecks(checks);
    _renderCoverage(coverage);
  });
}

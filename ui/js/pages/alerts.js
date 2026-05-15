import { api } from "../api.js";
import { showToast } from "../components/toast.js";

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function _renderStatus(container, data) {
  const sig = data.signals || {};
  const s = data.settings || {};
  container.querySelector('[data-slot="signals"]').innerHTML = `
    <div class="grid-2">
      <div class="kpi"><div class="kpi__value">${sig.p95_latency_ms || 0} мс</div><div class="kpi__label">p95 LLM</div></div>
      <div class="kpi"><div class="kpi__value">${((sig.no_sources_ratio || 0) * 100).toFixed(1)}%</div><div class="kpi__label">no-sources ratio</div></div>
      <div class="kpi"><div class="kpi__value">${sig.error_count || 0}</div><div class="kpi__label">Ошибок LLM</div></div>
      <div class="kpi"><div class="kpi__value">${sig.llm_calls || 0}</div><div class="kpi__label">Вызовов</div></div>
    </div>`;
  const v = data.violations || [];
  container.querySelector('[data-slot="violations"]').innerHTML = v.length
    ? `<ul class="t-error">${v.map((x) => `<li>${_esc(x)}</li>`).join("")}</ul>`
    : `<div class="chip chip--ok">всё в норме</div>`;
  container.querySelector('[data-slot="settings"]').innerHTML = `
    <ul style="padding-left: var(--space-4);">
      <li><strong>Включены:</strong> ${s.enabled ? "да" : "нет"}</li>
      <li><strong>Webhook задан:</strong> ${s.webhook_url_set ? "да" : "нет"} <span class="t-muted">(переменная окружения ALERTS_WEBHOOK_URL)</span></li>
      <li><strong>Порог p95 latency:</strong> ${s.p95_latency_threshold_ms} мс</li>
      <li><strong>Порог no-sources:</strong> ${(s.no_sources_ratio_threshold * 100).toFixed(0)}%</li>
      <li><strong>Порог ошибок:</strong> ${s.error_count_threshold}</li>
      <li><strong>Интервал проверки:</strong> ${s.check_interval_sec} с</li>
    </ul>
    <div class="t-muted t-body-small" style="margin-top: var(--space-2);">
      Настройки задаются через переменные ALERTS_* в .env.
    </div>`;
}

async function _refresh(container) {
  try {
    const data = await api.alertsStatus();
    _renderStatus(container, data);
  } catch (e) {
    container.querySelector('[data-slot="signals"]').innerHTML = `<div class='empty-state'>Не удалось: ${_esc(e.message)}</div>`;
  }
}

export async function renderAlerts(container) {
  const html = await (await fetch("/ui/static/pages/alerts.html")).text();
  container.innerHTML = html;
  await _refresh(container);
  container.querySelector('[data-slot="trigger"]').addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const r = await api.alertsTrigger();
      if (r.webhook_sent) showToast("Webhook отправлен", "success");
      else if (!r.violations.length) showToast("Нарушений нет", "info");
      else showToast("Нарушения есть, но webhook не настроен", "info");
      await _refresh(container);
    } catch (err) {
      showToast(`Ошибка: ${err.message}`, "error");
    } finally {
      btn.disabled = false;
    }
  });
}

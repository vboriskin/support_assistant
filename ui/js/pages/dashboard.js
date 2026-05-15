import { api } from "../api.js";

function _bar(label, count, max) {
  const pct = max > 0 ? Math.round((count / max) * 100) : 0;
  return `
    <div class="bars__row">
      <span class="bars__label">${label}</span>
      <span class="bars__track"><span class="bars__fill" style="width:${pct}%"></span></span>
      <span class="bars__count">${count}</span>
    </div>`;
}

function _kpi(label, value, hint = "") {
  return `
    <div class="kpi-card">
      <div class="kpi-card__label">${label}</div>
      <div class="kpi-card__value">${value}</div>
      ${hint ? `<div class="kpi-card__hint">${hint}</div>` : ""}
    </div>`;
}

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function _renderTimeseries(slot, points) {
  if (!points || !points.length) {
    slot.innerHTML = "<div class='empty-state'>Нет данных за период</div>";
    return;
  }
  const W = 720, H = 140, PAD_L = 40, PAD_R = 12, PAD_T = 8, PAD_B = 22;
  const max = Math.max(1, ...points.map((p) => p.count));
  const stepX = (W - PAD_L - PAD_R) / Math.max(1, points.length - 1);
  const innerH = H - PAD_T - PAD_B;
  const path = points
    .map((p, i) => {
      const x = PAD_L + i * stepX;
      const y = PAD_T + innerH - (p.count / max) * innerH;
      return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
    })
    .join(" ");
  const points_dots = points
    .map((p, i) => {
      const x = PAD_L + i * stepX;
      const y = PAD_T + innerH - (p.count / max) * innerH;
      return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2" class="ts-dot"/>`;
    })
    .join("");
  // X-axis labels — каждые N точек
  const labelStep = Math.max(1, Math.floor(points.length / 6));
  const xLabels = points
    .map((p, i) => {
      if (i % labelStep !== 0 && i !== points.length - 1) return "";
      const x = PAD_L + i * stepX;
      const label = p.date.slice(5); // MM-DD
      return `<text x="${x.toFixed(1)}" y="${H - 6}" class="ts-tick" text-anchor="middle">${label}</text>`;
    })
    .join("");
  slot.innerHTML = `
    <svg class="timeseries" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"
         role="img" aria-label="Динамика тикетов">
      <line x1="${PAD_L}" y1="${PAD_T}"           x2="${PAD_L}" y2="${H - PAD_B}" class="ts-axis"/>
      <line x1="${PAD_L}" y1="${H - PAD_B}"        x2="${W - PAD_R}" y2="${H - PAD_B}" class="ts-axis"/>
      <text x="${PAD_L - 4}" y="${PAD_T + 8}"     class="ts-tick" text-anchor="end">${max}</text>
      <text x="${PAD_L - 4}" y="${H - PAD_B}"      class="ts-tick" text-anchor="end">0</text>
      <path d="${path}" class="ts-line" fill="none"/>
      ${points_dots}
      ${xLabels}
    </svg>`;
}

function _renderAnomalies(slot, anomalies) {
  if (!anomalies || !anomalies.length) {
    slot.innerHTML = "<span class='t-muted'>Ничего необычного.</span>";
    return;
  }
  slot.innerHTML = `
    <ul class="dotted" style="margin:0;">
      ${anomalies
        .map(
          (a) => `
            <li><strong>${_esc(a.module)}</strong> —
              ${a.current} в этом периоде против ${a.previous} в прошлом
              <span class="t-secondary">(+${a.delta_pct}%)</span></li>`,
        )
        .join("")}
    </ul>`;
}

async function _load(container, period) {
  const kpiSlot = container.querySelector('[data-slot="kpi"]');
  const tsSlot = container.querySelector('[data-slot="timeseries"]');
  const modSlot = container.querySelector('[data-slot="by-module"]');
  const stSlot = container.querySelector('[data-slot="by-status"]');
  const anomalySlot = container.querySelector('[data-slot="anomalies"]');
  const lastSlot = container.querySelector('[data-slot="last-ingest"]');

  kpiSlot.innerHTML = '<span class="loader-l2"></span>';
  tsSlot.innerHTML = '<span class="loader-l1"></span>';

  const data = await api.dashboard(period);

  kpiSlot.innerHTML = [
    _kpi("Тикетов всего", data.tickets_total ?? 0, `${data.tickets_in_period ?? 0} за период`),
    _kpi("В индексе", data.tickets_indexed_total ?? 0),
    _kpi(
      "LLM-вызовов",
      data.llm_calls_total ?? 0,
      `${data.llm_calls_in_period ?? 0} за период`,
    ),
    _kpi(
      "LLM latency",
      `${data.avg_llm_latency_ms ?? 0} ms`,
      `p95: ${data.p95_llm_latency_ms ?? 0} ms`,
    ),
  ].join("");

  _renderTimeseries(tsSlot, data.timeseries);

  const byMod = data.tickets_by_module || [];
  const modMax = Math.max(1, ...byMod.map((x) => x.count));
  modSlot.innerHTML = byMod.length
    ? byMod.map((x) => _bar(_esc(x.module), x.count, modMax)).join("")
    : "<div class='empty-state'>Данных пока нет</div>";

  const byStatus = data.tickets_by_status || [];
  const statusMax = Math.max(1, ...byStatus.map((x) => x.count));
  stSlot.innerHTML = byStatus.length
    ? byStatus.map((x) => _bar(_esc(x.status), x.count, statusMax)).join("")
    : "<div class='empty-state'>Данных пока нет</div>";

  _renderAnomalies(anomalySlot, data.anomalies);

  const li = data.last_ingest;
  lastSlot.innerHTML = li
    ? `<div>Job <code>${_esc(li.id.slice(0, 8))}</code>:
        <span class="status-chip" data-status="${_esc(li.status)}">${_esc(li.status)}</span>
        — обработано ${li.processed}, ошибок ${li.failed},
        создан ${_esc(li.created_at)}</div>`
    : "Ингестов ещё не было.";
}

export async function renderDashboard(container) {
  const html = await (await fetch("/ui/static/pages/dashboard.html")).text();
  container.innerHTML = html;

  const picker = container.querySelector('[data-slot="period"]');
  picker.querySelectorAll("button[data-period]").forEach((btn) => {
    btn.addEventListener("click", () => {
      picker.querySelectorAll("button").forEach((b) =>
        b.setAttribute("aria-pressed", b === btn ? "true" : "false"),
      );
      _load(container, btn.dataset.period).catch((e) => {
        console.error(e);
      });
    });
  });
  await _load(container, "week");
}

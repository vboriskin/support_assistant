import { api } from "../api.js";

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function _fmtN(n) {
  if (n == null) return "—";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}

function _renderTimeseries(slot, points) {
  if (!points || !points.length) {
    slot.innerHTML = "<div class='empty-state'>Нет данных</div>";
    return;
  }
  const w = 720, h = 180, pad = 24;
  const max = Math.max(...points.map((p) => p.calls), 1);
  const stepX = (w - pad * 2) / Math.max(points.length - 1, 1);
  const pts = points
    .map((p, i) => `${pad + i * stepX},${h - pad - (p.calls / max) * (h - pad * 2)}`)
    .join(" ");
  const bars = points
    .map((p, i) => {
      const x = pad + i * stepX - 2;
      const bh = (p.tokens / Math.max(...points.map((q) => q.tokens || 1), 1)) * (h - pad * 2);
      return `<rect x="${x}" y="${h - pad - bh}" width="4" height="${bh}" class="costs-bar"/>`;
    })
    .join("");
  slot.innerHTML = `
    <svg viewBox="0 0 ${w} ${h}" class="line-chart" preserveAspectRatio="none">
      ${bars}
      <polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="1.5"/>
    </svg>
    <div class="row" style="justify-content: space-between; margin-top: var(--space-2);">
      <span class="t-secondary t-body-small">${points[0].date}</span>
      <span class="t-secondary t-body-small">${points[points.length - 1].date}</span>
    </div>
    <div class="t-body-small t-muted">Линия — кол-во вызовов; столбики — токены/день.</div>`;
}

function _renderByPurpose(slot, rows) {
  if (!rows || !rows.length) {
    slot.innerHTML = "<div class='empty-state'>Нет вызовов в этом периоде.</div>";
    return;
  }
  slot.innerHTML = `
    <table class="table">
      <thead>
        <tr>
          <th>purpose</th><th>модель</th>
          <th>вызовов</th><th>prompt tok</th><th>completion tok</th>
          <th>avg lat</th><th>p95 lat</th><th>ошибок</th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .map(
            (r) => `
          <tr>
            <td><code>${_esc(r.purpose)}</code></td>
            <td class="t-secondary">${_esc(r.model || "—")}</td>
            <td class="t-mono">${r.calls}</td>
            <td class="t-mono">${_fmtN(r.prompt_tokens)}</td>
            <td class="t-mono">${_fmtN(r.completion_tokens)}</td>
            <td class="t-mono">${r.avg_latency_ms} мс</td>
            <td class="t-mono">${r.p95_latency_ms} мс</td>
            <td class="t-mono ${r.errors > 0 ? "t-error" : "t-muted"}">${r.errors}</td>
          </tr>`
          )
          .join("")}
      </tbody>
    </table>`;
}

async function _load(container, period) {
  const kpi = container.querySelector('[data-slot="kpi"]');
  const ts = container.querySelector('[data-slot="timeseries"]');
  const bp = container.querySelector('[data-slot="by-purpose"]');
  kpi.innerHTML = '<span class="loader-l2"></span>';
  ts.innerHTML = '<span class="loader-l1"></span>';
  bp.innerHTML = '<span class="loader-l1"></span>';
  try {
    const d = await api.llmCosts(period);
    kpi.innerHTML = `
      <div class="kpi"><div class="kpi__value">${_fmtN(d.total_calls)}</div><div class="kpi__label">Вызовов LLM</div></div>
      <div class="kpi"><div class="kpi__value">${_fmtN(d.total_tokens)}</div><div class="kpi__label">Всего токенов</div></div>
      <div class="kpi"><div class="kpi__value">${d.p95_latency_ms} мс</div><div class="kpi__label">p95 латентность</div></div>
      <div class="kpi"><div class="kpi__value">${d.total_errors}</div><div class="kpi__label">Ошибок LLM</div></div>`;
    _renderTimeseries(ts, d.timeseries);
    _renderByPurpose(bp, d.by_purpose);
  } catch (e) {
    kpi.innerHTML = `<div class='empty-state'>Не удалось: ${_esc(e.message)}</div>`;
    ts.innerHTML = "";
    bp.innerHTML = "";
  }
}

export async function renderCosts(container) {
  const html = await (await fetch("/ui/static/pages/costs.html")).text();
  container.innerHTML = html;
  let period = "week";
  const picker = container.querySelector('[data-slot="period"]');
  await _load(container, period);
  picker.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-period]");
    if (!btn) return;
    picker.querySelectorAll("button").forEach((b) => b.removeAttribute("aria-pressed"));
    btn.setAttribute("aria-pressed", "true");
    period = btn.dataset.period;
    _load(container, period);
  });
}

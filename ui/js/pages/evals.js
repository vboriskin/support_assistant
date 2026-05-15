import { api, ApiError } from "../api.js";
import { showToast } from "../components/toast.js";

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function _pct(x) {
  return x == null ? "—" : `${(x * 100).toFixed(0)}%`;
}

function _scoreClass(x) {
  if (x == null) return "score--unknown";
  if (x >= 0.8) return "score--good";
  if (x >= 0.5) return "score--mid";
  return "score--bad";
}

function _runRow(r) {
  const agg = r.aggregate || {};
  return `
    <tr data-run-id="${_esc(r.run_id)}">
      <td><code>${_esc((r.run_id || "").slice(0, 8))}</code></td>
      <td>${_esc(r.case_set || "—")}</td>
      <td><span class="status-chip" data-status="${_esc(r.status)}">${_esc(r.status)}</span></td>
      <td>${r.completed_cases ?? 0}/${r.total_cases ?? 0}</td>
      <td class="t-mono">${_pct(agg.faithfulness_avg)}</td>
      <td class="t-mono">${_pct(agg.helpfulness_avg)}</td>
      <td class="t-mono">${_pct(agg.recall_at_5_avg)}</td>
      <td class="t-mono t-muted">${(r.started_at || "").slice(0, 19).replace("T", " ")}</td>
    </tr>`;
}

function _caseCard(c) {
  const adv = c.adversarial_passed;
  const advChip = adv === null || adv === undefined
    ? ""
    : adv
    ? `<span class="chip chip--ok">adversarial: passed</span>`
    : `<span class="chip chip--bad">adversarial: failed</span>`;
  const failed = (c.errors && c.errors.length) || (adv === false);
  return `
    <div class="case-card ${failed ? "case-card--failed" : ""}">
      <header class="case-card__header">
        <div>
          <div class="t-secondary">${_esc(c.case_id)} · ${_esc(c.edge_case_type || "—")}</div>
          <div class="case-card__query">${_esc(c.query)}</div>
        </div>
        <div class="case-card__scores">
          <span class="score ${_scoreClass(c.faithfulness)}" title="faithfulness">F ${_pct(c.faithfulness)}</span>
          <span class="score ${_scoreClass(c.helpfulness)}" title="helpfulness">H ${_pct(c.helpfulness)}</span>
          <span class="score ${_scoreClass(c.recall_at_5)}" title="recall@5">R5 ${_pct(c.recall_at_5)}</span>
          <span class="score ${_scoreClass(c.mrr)}" title="MRR">MRR ${_pct(c.mrr)}</span>
        </div>
      </header>
      <details class="case-card__body">
        <summary>Подробнее</summary>
        <div class="case-card__detail">
          ${c.answer_text ? `<h5>Ответ</h5>
            <div class="case-card__answer">${_esc(c.answer_text).replace(/\n/g, "<br>")}</div>` : ""}
          ${c.faithfulness_explanation ? `<p class="t-secondary"><strong>Faithfulness:</strong> ${_esc(c.faithfulness_explanation)}</p>` : ""}
          ${c.helpfulness_explanation ? `<p class="t-secondary"><strong>Helpfulness:</strong> ${_esc(c.helpfulness_explanation)}</p>` : ""}
          <div class="t-secondary">must_mention: ${c.must_mention_hits ?? 0}/${c.must_mention_total ?? 0} · must_not violations: ${c.must_not_mention_violations ?? 0} · latency: ${c.latency_ms ?? 0} мс</div>
          ${advChip ? `<div style="margin-top: var(--space-2);">${advChip}</div>` : ""}
          ${c.expected_source_ids?.length ? `<p class="t-secondary"><strong>Ожидаемые источники:</strong> ${c.expected_source_ids.map(_esc).join(", ")}</p>` : ""}
          ${c.retrieved_source_ids?.length ? `<p class="t-secondary"><strong>Найденные:</strong> ${c.retrieved_source_ids.map(_esc).join(", ")}</p>` : ""}
          ${c.errors?.length ? `<p class="t-muted"><strong>Ошибки:</strong> ${c.errors.map(_esc).join("; ")}</p>` : ""}
        </div>
      </details>
    </div>`;
}

function _renderRunDetail(slot, run) {
  const agg = run.aggregate || {};
  const cases = run.results || [];
  const failed = cases.filter((c) => (c.errors && c.errors.length) || c.adversarial_passed === false);
  slot.innerHTML = `
    <div class="card">
      <header class="detail__header">
        <div>
          <h3>Прогон <code>${_esc((run.run_id || "").slice(0, 8))}</code></h3>
          <div class="t-secondary">${_esc(run.case_set || "—")} · ${(run.started_at || "").slice(0, 19).replace("T", " ")}</div>
        </div>
        <button type="button" class="btn btn--ghost btn--sm" data-slot="close-detail">Закрыть</button>
      </header>
      <div class="grid-4" style="margin-top: var(--space-3);">
        <div class="kpi"><div class="kpi__value">${run.completed_cases ?? 0}/${run.total_cases ?? 0}</div><div class="kpi__label">Кейсов</div></div>
        <div class="kpi"><div class="kpi__value">${_pct(agg.faithfulness_avg)}</div><div class="kpi__label">Faithfulness</div></div>
        <div class="kpi"><div class="kpi__value">${_pct(agg.helpfulness_avg)}</div><div class="kpi__label">Helpfulness</div></div>
        <div class="kpi"><div class="kpi__value">${_pct(agg.recall_at_5_avg)}</div><div class="kpi__label">Recall@5</div></div>
      </div>
      <div class="row" style="gap: var(--space-2); margin-top: var(--space-4);" data-slot="filter">
        <button type="button" class="btn btn--ghost btn--sm is-selected" data-filter="all">Все (${cases.length})</button>
        <button type="button" class="btn btn--ghost btn--sm" data-filter="failed">Только проваленные (${failed.length})</button>
      </div>
      <div class="case-list" data-slot="cases" style="margin-top: var(--space-3);">
        ${cases.length ? cases.map(_caseCard).join("") : "<div class='empty-state'>Кейсов нет.</div>"}
      </div>
    </div>`;

  slot.querySelector('[data-slot="close-detail"]').addEventListener("click", () => {
    slot.innerHTML = "";
  });
  const filterRow = slot.querySelector('[data-slot="filter"]');
  filterRow.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-filter]");
    if (!btn) return;
    filterRow.querySelectorAll("[data-filter]").forEach((b) => b.classList.remove("is-selected"));
    btn.classList.add("is-selected");
    const subset = btn.dataset.filter === "failed" ? failed : cases;
    slot.querySelector('[data-slot="cases"]').innerHTML = subset.length
      ? subset.map(_caseCard).join("")
      : "<div class='empty-state'>Кейсов нет.</div>";
  });
}

async function _loadRuns(runsSlot, detailSlot) {
  try {
    const runs = await api.listEvalRuns();
    if (!runs.length) {
      runsSlot.innerHTML = "<div class='empty-state'>Прогонов ещё не было.</div>";
      return;
    }
    runsSlot.innerHTML = `
      <table class="table">
        <thead>
          <tr>
            <th>ID</th><th>Набор</th><th>Статус</th><th>Кейсы</th>
            <th>Faith</th><th>Help</th><th>R@5</th><th>Начат</th>
          </tr>
        </thead>
        <tbody>${runs.map(_runRow).join("")}</tbody>
      </table>`;
    runsSlot.querySelectorAll("tbody tr").forEach((tr) => {
      tr.addEventListener("click", async () => {
        detailSlot.innerHTML = '<span class="loader-l2"></span>';
        try {
          const run = await api.getEvalRun(tr.dataset.runId);
          _renderRunDetail(detailSlot, run);
          detailSlot.scrollIntoView({ behavior: "smooth", block: "start" });
        } catch (e) {
          detailSlot.innerHTML = `<div class="t-muted">Не удалось загрузить прогон</div>`;
        }
      });
    });
  } catch (e) {
    runsSlot.textContent = `Не удалось загрузить: ${e.message}`;
  }
}

function _renderDiff(slot, d) {
  const s = d.summary || {};
  const rows = (d.cases || [])
    .filter((c) => c.status !== "same")
    .map((c) => {
      const cls =
        c.status === "regressed"
          ? "chip--bad"
          : c.status === "improved"
          ? "chip--ok"
          : "";
      const dlt = c.deltas || {};
      const fmt = (x) => (x == null ? "" : (x >= 0 ? "+" : "") + (x * 100).toFixed(0) + "%");
      return `
        <tr>
          <td><code>${(c.case_id || "").slice(0, 24)}</code></td>
          <td><span class="chip ${cls}">${c.status}</span></td>
          <td class="t-mono">${fmt(dlt.faithfulness)}</td>
          <td class="t-mono">${fmt(dlt.helpfulness)}</td>
          <td class="t-mono">${fmt(dlt.recall_at_5)}</td>
          <td class="t-mono">${fmt(dlt.mrr)}</td>
          <td class="t-secondary">${(c.query || "").slice(0, 80)}</td>
        </tr>`;
    })
    .join("");
  const aggD = d.aggregate_delta || {};
  const fmt = (x) => (x >= 0 ? "+" : "") + (x * 100).toFixed(1) + "%";
  slot.innerHTML = `
    <div class="grid-4">
      <div class="kpi"><div class="kpi__value">${s.improved || 0}</div><div class="kpi__label">Улучшилось</div></div>
      <div class="kpi"><div class="kpi__value">${s.regressed || 0}</div><div class="kpi__label">Просело</div></div>
      <div class="kpi"><div class="kpi__value">${s.same || 0}</div><div class="kpi__label">Без изменений</div></div>
      <div class="kpi"><div class="kpi__value">${(s.only_a || 0) + (s.only_b || 0)}</div><div class="kpi__label">Только в одном</div></div>
    </div>
    <div class="t-secondary" style="margin-top: var(--space-3);">
      Δ агрегатов: faith ${fmt(aggD.faithfulness_avg || 0)} · help ${fmt(aggD.helpfulness_avg || 0)} · R@5 ${fmt(aggD.recall_at_5_avg || 0)}
    </div>
    ${rows
      ? `<table class="table" style="margin-top: var(--space-3);">
          <thead><tr><th>Кейс</th><th>Статус</th><th>Δ Faith</th><th>Δ Help</th><th>Δ R@5</th><th>Δ MRR</th><th>Запрос</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>`
      : "<div class='empty-state' style='margin-top: var(--space-3);'>Изменений нет — все кейсы стабильны.</div>"}`;
}

export async function renderEvals(container) {
  const html = await (await fetch("/ui/static/pages/evals.html")).text();
  container.innerHTML = html;

  const form = container.querySelector('[data-slot="run-form"]');
  const resultSlot = container.querySelector('[data-slot="run-result"]');
  const runsSlot = container.querySelector('[data-slot="runs"]');
  const detailSlot = container.querySelector('[data-slot="run-detail"]');
  const diffForm = container.querySelector('[data-slot="diff-form"]');
  const diffA = container.querySelector('[data-slot="diff-a"]');
  const diffB = container.querySelector('[data-slot="diff-b"]');
  const diffResult = container.querySelector('[data-slot="diff-result"]');

  // Заполняем селекторы прогонов и связываем с diff
  api.listEvalRuns().then((runs) => {
    const opts = runs
      .map((r) => `<option value="${_esc(r.run_id)}">${_esc(r.run_id.slice(0, 8))} · ${_esc(r.case_set || "")} · ${(r.started_at || "").slice(0, 10)}</option>`)
      .join("");
    diffA.innerHTML = opts;
    diffB.innerHTML = opts;
    if (runs.length >= 2) {
      diffA.value = runs[1].run_id;
      diffB.value = runs[0].run_id;
    }
  }).catch(() => {});

  diffForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!diffA.value || !diffB.value) {
      showToast("Выберите оба прогона", "info");
      return;
    }
    diffResult.innerHTML = '<span class="loader-l2"></span>';
    try {
      const d = await api.diffEvalRuns(diffA.value, diffB.value);
      _renderDiff(diffResult, d);
    } catch (err) {
      diffResult.innerHTML = `<div class='empty-state'>Не удалось: ${_esc(err.message)}</div>`;
    }
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const body = {
      case_set: fd.get("case_set") || "default",
      sample_size: fd.get("sample_size") ? Number(fd.get("sample_size")) : null,
    };
    resultSlot.textContent = "Запуск…";
    try {
      const resp = await api.runEvals(body);
      resultSlot.innerHTML = `Запущено: <code>${_esc(resp.run_id)}</code>. Обновите таблицу через несколько секунд.`;
      showToast("Запуск отправлен", "success");
      setTimeout(() => _loadRuns(runsSlot, detailSlot), 1500);
    } catch (e) {
      if (e instanceof ApiError && e.status === 501) {
        resultSlot.textContent = "Runner ещё не реализован.";
        showToast("Endpoint вернул 501", "info");
      } else {
        resultSlot.textContent = `Ошибка: ${e.message}`;
        showToast("Не удалось запустить", "error");
      }
    }
  });

  await _loadRuns(runsSlot, detailSlot);
}

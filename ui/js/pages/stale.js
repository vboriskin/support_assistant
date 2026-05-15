import { api } from "../api.js";

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function _row(a) {
  return `
    <tr>
      <td><a href="#/kb">${_esc(a.title)}</a></td>
      <td>${_esc(a.module || "—")}</td>
      <td class="t-mono t-muted">${(a.updated_at || "").slice(0, 10)}</td>
      <td class="t-mono">${a.days_since_update}</td>
      <td class="t-mono">${a.refs_in_recent_answers}</td>
      <td class="t-mono ${a.negative_feedback > 0 ? "t-error" : ""}">${a.negative_feedback}</td>
      <td class="t-mono">${a.positive_feedback}</td>
    </tr>`;
}

async function _load(container, params) {
  const wrap = container.querySelector('[data-slot="list-wrap"]');
  wrap.innerHTML = '<span class="loader-l2"></span>';
  try {
    const items = await api.listStaleKB(params);
    if (!items.length) {
      wrap.innerHTML = "<div class='empty-state'>Свежее всё. 👍</div>";
      return;
    }
    wrap.innerHTML = `
      <table class="table">
        <thead>
          <tr>
            <th>Статья</th><th>Модуль</th><th>Обновлена</th><th>Дней назад</th>
            <th>Ссылок в ответах</th><th>👎</th><th>👍</th>
          </tr>
        </thead>
        <tbody>${items.map(_row).join("")}</tbody>
      </table>`;
  } catch (e) {
    wrap.innerHTML = `<div class='empty-state'>Не удалось: ${_esc(e.message)}</div>`;
  }
}

export async function renderStale(container) {
  const html = await (await fetch("/ui/static/pages/stale.html")).text();
  container.innerHTML = html;
  const form = container.querySelector('[data-slot="filters"]');
  const params = () => {
    const fd = new FormData(form);
    const out = {};
    const m = fd.get("months");
    if (m) out.months = m;
    if (fd.get("only_with_negative_feedback")) out.only_with_negative_feedback = "true";
    return out;
  };
  await _load(container, params());
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    _load(container, params());
  });
}

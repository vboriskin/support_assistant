import { api } from "../api.js";

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function _row(a) {
  const status = a.status >= 400 ? "cancelled" : a.status >= 200 ? "resolved" : "open";
  return `
    <tr>
      <td class="t-mono t-muted">${(a.created_at || "").slice(0, 19).replace("T", " ")}</td>
      <td>${_esc(a.user_id || "anonymous")}</td>
      <td><code>${_esc(a.action)}</code></td>
      <td>${_esc(a.target_type || "—")}</td>
      <td>${a.target_id ? `<code>${_esc(a.target_id.slice(0, 8))}</code>` : "—"}</td>
      <td><span class="status-chip" data-status="${status}">${a.status || "—"}</span></td>
      <td class="t-mono t-muted">${_esc(a.method || "")}</td>
      <td class="t-mono t-muted">${_esc(a.path || "")}</td>
    </tr>`;
}

async function _load(container, params) {
  const wrap = container.querySelector('[data-slot="list-wrap"]');
  wrap.innerHTML = '<span class="loader-l2"></span>';
  try {
    const items = await api.listAudit(params);
    if (!items.length) {
      wrap.innerHTML = "<div class='empty-state'>Записей нет.</div>";
      return;
    }
    wrap.innerHTML = `
      <table class="table">
        <thead>
          <tr><th>Когда</th><th>Кто</th><th>Действие</th><th>Тип</th><th>ID</th><th>Статус</th><th>Метод</th><th>Path</th></tr>
        </thead>
        <tbody>${items.map(_row).join("")}</tbody>
      </table>`;
  } catch (e) {
    wrap.innerHTML = `<div class='empty-state'>Не удалось загрузить: ${_esc(e.message)}</div>`;
  }
}

export async function renderAudit(container) {
  const html = await (await fetch("/ui/static/pages/audit.html")).text();
  container.innerHTML = html;
  const form = container.querySelector('[data-slot="filters"]');
  const params = () => Object.fromEntries(new FormData(form).entries());
  await _load(container, params());
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    _load(container, params());
  });
}

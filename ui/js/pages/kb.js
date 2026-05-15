/* Страница «База знаний»: список + create/edit/delete + просмотр чанков. */

import { api, ApiError } from "../api.js";
import { showToast } from "../components/toast.js";

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function _row(a) {
  return `
    <tr data-id="${_esc(a.id)}">
      <td>${_esc(a.title)}</td>
      <td>${_esc(a.module || "—")}</td>
      <td class="t-mono t-muted">${(a.updated_at || "").slice(0, 10)}</td>
      <td>${
        a.is_deprecated
          ? `<span class="status-chip" data-status="cancelled">deprecated</span>`
          : `<span class="status-chip" data-status="resolved">active</span>`
      }</td>
    </tr>`;
}

async function _loadList(container, params) {
  const wrap = container.querySelector('[data-slot="list-wrap"]');
  wrap.innerHTML = '<span class="loader-l2"></span>';
  try {
    const items = await api.listKB(params);
    if (!items.length) {
      wrap.innerHTML =
        "<div class='empty-state'>Статей ещё нет. Создайте первую кнопкой «+ Новая статья».</div>";
      return;
    }
    wrap.innerHTML = `
      <table class="table">
        <thead>
          <tr><th>Название</th><th>Модуль</th><th>Обновлено</th><th>Статус</th></tr>
        </thead>
        <tbody>${items.map(_row).join("")}</tbody>
      </table>`;
    wrap.querySelectorAll("tbody tr").forEach((tr) => {
      tr.addEventListener("click", () => _openDetail(container, tr.dataset.id));
    });
  } catch (e) {
    wrap.innerHTML = `<div class='empty-state'>Не удалось загрузить: ${_esc(e.message)}</div>`;
  }
}

async function _openDetail(container, id) {
  const slot = container.querySelector('[data-slot="detail"]');
  const editorSlot = container.querySelector('[data-slot="editor"]');
  editorSlot.hidden = true;
  editorSlot.innerHTML = "";
  slot.innerHTML = '<div class="card"><span class="loader-l2"></span></div>';

  let art;
  try {
    art = await api.getKB(id);
  } catch (e) {
    slot.innerHTML = `<div class='empty-state'>Не удалось получить статью: ${_esc(e.message)}</div>`;
    return;
  }
  slot.innerHTML = `
    <div class="card" style="margin-top: var(--space-4);" data-slot="card">
      <div class="row" style="justify-content: space-between; align-items: flex-start;">
        <div>
          <h3 style="margin-bottom: var(--space-2);">${_esc(art.title)}</h3>
          <div class="t-secondary t-body-small">
            ${_esc(art.module || "—")} · ${_esc(art.category || "—")} ·
            обновлено ${(art.updated_at || "").slice(0, 10)}
            ${art.is_deprecated ? ` · <span class="status-chip" data-status="cancelled">deprecated</span>` : ""}
          </div>
        </div>
        <div class="row" style="gap: var(--space-2);">
          <button class="btn btn--ghost btn--sm" data-slot="edit">Редактировать</button>
          <button class="btn btn--ghost btn--sm" data-slot="delete">Удалить</button>
        </div>
      </div>
      <pre style="margin-top: var(--space-4); white-space: pre-wrap;">${_esc(art.body)}</pre>
      <h4 style="margin-top: var(--space-4);">Чанки (${(art.chunks || []).length})</h4>
      <div class="t-secondary t-body-small" style="margin-bottom: var(--space-2);">
        Эти куски попадают в индекс ассистента отдельно — соответствие гранулярности поиска.
      </div>
      <ol class="numbered" style="padding-left: var(--space-5);">
        ${(art.chunks || [])
          .map(
            (c) => `
              <li>
                ${
                  c.section_title
                    ? `<strong>${_esc(c.section_title)}</strong> — `
                    : ""
                }<span class="t-secondary">${_esc(c.text.slice(0, 160))}${c.text.length > 160 ? "…" : ""}</span>
              </li>`
          )
          .join("")}
      </ol>
    </div>`;

  slot.querySelector('[data-slot="edit"]').addEventListener("click", () =>
    _openEditor(container, art),
  );
  slot.querySelector('[data-slot="delete"]').addEventListener("click", async () => {
    if (!confirm(`Удалить статью «${art.title}»? Чанки удалятся из индекса.`)) return;
    try {
      await api.deleteKB(id);
      showToast("Статья удалена", "success");
      slot.innerHTML = "";
      await _loadList(container, _currentFilters(container));
    } catch (e) {
      showToast(`Ошибка: ${e.message}`, "error");
    }
  });
}

function _openEditor(container, existing) {
  const editorSlot = container.querySelector('[data-slot="editor"]');
  const detailSlot = container.querySelector('[data-slot="detail"]');
  detailSlot.innerHTML = "";
  editorSlot.hidden = false;
  const isNew = !existing;
  const initial = existing || {
    title: "",
    body: "",
    module: "",
    category: "",
    audience: "internal",
    tags: [],
    is_deprecated: false,
  };
  editorSlot.innerHTML = `
    <form class="card" style="margin-top: var(--space-4); display: flex; flex-direction: column; gap: var(--space-3);">
      <h3>${isNew ? "Новая статья" : "Редактирование"}</h3>
      <div class="field">
        <label>Название *</label>
        <input name="title" required maxlength="300" value="${_esc(initial.title)}">
      </div>
      <div class="grid-2">
        <div class="field">
          <label>Модуль</label>
          <input name="module" value="${_esc(initial.module || "")}">
        </div>
        <div class="field">
          <label>Категория</label>
          <input name="category" value="${_esc(initial.category || "")}">
        </div>
      </div>
      <div class="grid-2">
        <div class="field">
          <label>Audience</label>
          <select name="audience">
            <option value="internal" ${initial.audience === "internal" ? "selected" : ""}>internal</option>
            <option value="external" ${initial.audience === "external" ? "selected" : ""}>external</option>
          </select>
        </div>
        <div class="field">
          <label>Теги (через запятую)</label>
          <input name="tags" value="${_esc((initial.tags || []).join(", "))}">
        </div>
      </div>
      <div class="field">
        <label>Содержимое (markdown)</label>
        <textarea name="body" rows="14" required>${_esc(initial.body || "")}</textarea>
      </div>
      ${
        isNew
          ? ""
          : `<label class="row" style="align-items: center; gap: var(--space-2);">
              <input type="checkbox" name="is_deprecated" ${initial.is_deprecated ? "checked" : ""}>
              <span>Помечена как deprecated</span>
            </label>`
      }
      <div class="row" style="gap: var(--space-2);">
        <button type="submit" class="btn btn--primary">${isNew ? "Создать" : "Сохранить"}</button>
        <button type="button" class="btn btn--ghost" data-slot="cancel">Отмена</button>
      </div>
    </form>`;
  const form = editorSlot.querySelector("form");
  editorSlot.querySelector('[data-slot="cancel"]').addEventListener("click", () => {
    editorSlot.hidden = true;
    editorSlot.innerHTML = "";
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    const tags = String(fd.get("tags") || "")
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    const payload = {
      title: fd.get("title"),
      body: fd.get("body"),
      module: fd.get("module") || null,
      category: fd.get("category") || null,
      audience: fd.get("audience") || "internal",
      tags,
    };
    try {
      if (isNew) {
        const created = await api.createKB(payload);
        showToast(`Создано: ${created.title}`, "success");
      } else {
        payload.is_deprecated = !!fd.get("is_deprecated");
        await api.updateKB(existing.id, payload);
        showToast("Сохранено", "success");
      }
      editorSlot.hidden = true;
      editorSlot.innerHTML = "";
      await _loadList(container, _currentFilters(container));
      if (!isNew) await _openDetail(container, existing.id);
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.code}: ${e.message}` : e.message;
      showToast(`Ошибка: ${msg}`, "error");
    }
  });
}

function _currentFilters(container) {
  const form = container.querySelector('[data-slot="filters"]');
  const fd = new FormData(form);
  const params = {};
  const m = (fd.get("module") || "").toString().trim();
  if (m) params.module = m;
  return params;
}

export async function renderKB(container) {
  const html = await (await fetch("/ui/static/pages/kb.html")).text();
  container.innerHTML = html;

  await _loadList(container, {});

  container.querySelector('[data-slot="new-btn"]').addEventListener("click", () => {
    _openEditor(container, null);
  });
  container.querySelector('[data-slot="bulk-btn"]').addEventListener("click", () => {
    _openBulkDialog(container);
  });
  container.querySelector('[data-slot="apply"]').addEventListener("click", () => {
    _loadList(container, _currentFilters(container));
  });
}

function _openBulkDialog(container) {
  const modal = document.createElement("div");
  modal.className = "modal-backdrop";
  modal.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true" aria-label="Массовый импорт KB">
      <header class="modal__header">
        <h3>Массовый импорт</h3>
        <button type="button" class="btn btn--ghost btn--sm" data-slot="close" aria-label="Закрыть">✕</button>
      </header>
      <form class="modal__body" data-slot="bulk-form">
        <p class="t-secondary t-body-small">
          Загрузите zip-архив с *.md (или *.html) файлами или одиночный файл. Заголовок — из первого
          <code>#</code>-заголовка либо из имени файла.
        </p>
        <div class="field">
          <label>Файл</label>
          <input type="file" name="file" required accept=".zip,.md,.markdown,.html,.htm">
        </div>
        <div class="row">
          <div class="field" style="flex: 1;">
            <label>Формат</label>
            <select name="kind">
              <option value="markdown">markdown</option>
              <option value="html">html</option>
            </select>
          </div>
          <div class="field" style="flex: 1;">
            <label>Модуль по умолчанию</label>
            <input name="module" placeholder="опционально">
          </div>
        </div>
        <div class="t-secondary t-body-small" data-slot="bulk-result"></div>
        <footer class="modal__footer">
          <button type="button" class="btn btn--ghost" data-slot="cancel">Отмена</button>
          <button type="submit" class="btn btn--primary">Импортировать</button>
        </footer>
      </form>
    </div>`;
  document.body.appendChild(modal);
  const close = () => modal.remove();
  modal.querySelector('[data-slot="close"]').addEventListener("click", close);
  modal.querySelector('[data-slot="cancel"]').addEventListener("click", close);
  modal.addEventListener("click", (e) => {
    if (e.target === modal) close();
  });

  const form = modal.querySelector('[data-slot="bulk-form"]');
  const resultSlot = modal.querySelector('[data-slot="bulk-result"]');

  // Защитная проверка — если querySelector почему-то не нашёл <form>,
  // показываем ошибку понятным сообщением, а не падаем на FormData.
  if (!(form instanceof HTMLFormElement)) {
    showToast("Не удалось открыть форму импорта (DOM)", "error");
    return;
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    // Читаем поля напрямую через querySelector — не используем FormData,
    // чтобы не зависеть от того, что браузер видит <form> «правильно».
    const fileInput = form.querySelector('input[name="file"]');
    const kindEl = form.querySelector('select[name="kind"]');
    const moduleEl = form.querySelector('input[name="module"]');

    const file = fileInput && fileInput.files && fileInput.files[0];
    if (!file || !file.size) {
      showToast("Выберите файл", "error");
      return;
    }
    const kind = (kindEl && kindEl.value) || "markdown";
    const moduleVal = ((moduleEl && moduleEl.value) || "").trim() || null;

    const submitBtn = form.querySelector("button[type=submit]");
    submitBtn.disabled = true;
    resultSlot.innerHTML = '<span class="loader-l1"></span>';
    try {
      const resp = await api.bulkKB({ file, kind, module: moduleVal });
      const s = resp.stats || {};
      resultSlot.innerHTML = `Готово: загружено <strong>${s.indexed ?? 0}</strong>, пропущено ${s.skipped ?? 0}, ошибок ${s.failed ?? 0}, чанков ${s.chunks ?? 0}.`;
      showToast("Импорт завершён", "success");
      await _loadList(container, _currentFilters(container));
    } catch (err) {
      const msg = err instanceof ApiError ? `${err.code}: ${err.message}` : err.message;
      resultSlot.innerHTML = `<span class="t-muted">Ошибка: ${_esc(msg)}</span>`;
      showToast("Импорт не удался", "error");
    } finally {
      submitBtn.disabled = false;
    }
  });
}

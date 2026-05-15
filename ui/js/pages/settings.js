/* Страница «Настройки»: чтение /api/settings, редактирование, PATCH в .env. */

import { api, ApiError } from "../api.js";
import { showToast } from "../components/toast.js";

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function _fieldControl(f) {
  const name = `attr-${f.name}`;
  const placeholder = f.secret && f.value ? "значение задано — введите новое, чтобы заменить" : "";
  if (f.type === "bool") {
    return `
      <label class="settings__switch">
        <input type="checkbox" name="${_esc(f.name)}" ${f.value === true || f.value === "true" ? "checked" : ""}>
        <span class="settings__switch-track"></span>
      </label>`;
  }
  if (f.type === "enum" && Array.isArray(f.options)) {
    const cur = String(f.value ?? "");
    return `
      <select name="${_esc(f.name)}">
        ${f.options
          .map(
            (o) =>
              `<option value="${_esc(o)}" ${o === cur ? "selected" : ""}>${_esc(o || "(пусто)")}</option>`
          )
          .join("")}
      </select>`;
  }
  if (f.type === "int" || f.type === "float") {
    return `<input type="number" step="${f.type === "float" ? "0.01" : "1"}" name="${_esc(f.name)}" value="${_esc(String(f.value ?? ""))}">`;
  }
  if (f.type === "secret" || f.secret) {
    return `<input type="password" name="${_esc(f.name)}" placeholder="${_esc(placeholder || "введите значение")}" value="${_esc(String(f.value ?? ""))}">`;
  }
  return `<input type="text" name="${_esc(f.name)}" value="${_esc(String(f.value ?? ""))}">`;
}

function _renderGroup(g) {
  return `
    <section class="card settings__group" id="group-${_esc(g.id)}" data-group-id="${_esc(g.id)}">
      <header class="settings__group-head">
        <h3 style="margin: 0;">${_esc(g.title)}</h3>
        ${g.description ? `<div class="t-secondary t-body-small">${_esc(g.description)}</div>` : ""}
      </header>
      <div class="settings__fields">
        ${g.fields
          .map(
            (f) => `
          <div class="settings__field" data-field-name="${_esc(f.name)}">
            <div class="settings__field-head">
              <label class="settings__label" for="attr-${_esc(f.name)}">
                <code class="settings__envname">${_esc(f.name)}</code>
                ${f.secret ? `<span class="chip">secret</span>` : ""}
                ${f.restart_required ? `<span class="chip">требует рестарт</span>` : ""}
              </label>
              ${f.description ? `<div class="t-secondary t-body-small">${_esc(f.description)}</div>` : ""}
            </div>
            <div class="settings__field-input">
              ${_fieldControl(f)}
            </div>
          </div>`
          )
          .join("")}
      </div>
    </section>`;
}

function _renderTabs(slot, groups) {
  slot.querySelectorAll("[data-tab]").forEach((b) => b.remove());
  groups.forEach((g, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn btn--ghost btn--sm" + (i === 0 ? " is-selected" : "");
    btn.dataset.tab = g.id;
    btn.textContent = g.title;
    btn.addEventListener("click", () => {
      const target = document.getElementById("group-" + g.id);
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      slot.querySelectorAll("[data-tab]").forEach((x) => x.classList.remove("is-selected"));
      btn.classList.add("is-selected");
    });
    slot.appendChild(btn);
  });
}

function _collectValues(container, groups) {
  const out = {};
  for (const g of groups) {
    const section = container.querySelector(`[data-group-id="${g.id}"]`);
    if (!section) continue;
    for (const f of g.fields) {
      const fieldEl = section.querySelector(`[data-field-name="${f.name}"] [name="${f.name}"]`);
      if (!fieldEl) continue;
      if (f.type === "bool") {
        out[f.name] = !!fieldEl.checked;
      } else if (f.type === "int") {
        const v = fieldEl.value;
        if (v !== "") out[f.name] = Number(v);
      } else if (f.type === "float") {
        const v = fieldEl.value;
        if (v !== "") out[f.name] = Number(v);
      } else {
        out[f.name] = fieldEl.value;
      }
    }
  }
  return out;
}

function _diffAgainstInitial(initial, current) {
  const diff = {};
  for (const k of Object.keys(current)) {
    const a = initial[k];
    const b = current[k];
    // Маскированное secret-поле: если значение начинается с * и осталось как есть — не патчим.
    if (typeof b === "string" && b.startsWith("****")) continue;
    if (typeof b === "string" && b.startsWith("*") && b === a) continue;
    if (String(a) !== String(b)) diff[k] = b;
  }
  return diff;
}

export async function renderSettings(container) {
  const html = await (await fetch("/ui/static/pages/settings.html")).text();
  container.innerHTML = html;

  const groupsSlot = container.querySelector('[data-slot="groups"]');
  const tabsSlot = container.querySelector('[data-slot="group-tabs"]');
  const envFileSlot = container.querySelector('[data-slot="env-file"]');
  const restartSlot = container.querySelector('[data-slot="restart-banner"]');

  let groups = [];
  let initial = {};

  async function reload() {
    groupsSlot.innerHTML = '<span class="loader-l2"></span>';
    restartSlot.hidden = true;
    restartSlot.innerHTML = "";
    try {
      const data = await api.getSettings();
      groups = data.groups || [];
      envFileSlot.innerHTML = `Конфигурация ассистента — файл: <code>${_esc(data.env_file)}</code>`;
      groupsSlot.innerHTML = groups.map(_renderGroup).join("");
      _renderTabs(tabsSlot, groups);
      initial = _collectValues(container, groups);
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.code}: ${e.message}` : e.message;
      groupsSlot.innerHTML = `<div class='empty-state'>Не удалось загрузить настройки: ${_esc(msg)}</div>`;
    }
  }

  container.querySelector('[data-slot="reload"]').addEventListener("click", reload);

  // ---------------- Выгрузить логи (diag JSON) ----------------
  container.querySelector('[data-slot="download-diag"]').addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    const origLabel = btn.textContent;
    btn.textContent = "Собираю…";
    try {
      // Открываем как Blob, чтобы корректно отдалось имя файла из заголовка.
      const resp = await fetch("/api/diag", {
        headers: {
          "X-User-Id": localStorage.getItem("userId") || "anonymous",
          "Accept": "application/json",
        },
      });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const blob = await resp.blob();
      // Имя файла из Content-Disposition, либо timestamp
      let filename = "diag.json";
      const cd = resp.headers.get("Content-Disposition") || "";
      const m = cd.match(/filename="([^"]+)"/);
      if (m) filename = m[1];
      else {
        const ts = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
        filename = `diag_${ts}.json`;
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
      showToast(`Сохранено: ${filename}`, "success");
    } catch (err) {
      showToast(`Не удалось собрать логи: ${err.message}`, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = origLabel;
    }
  });

  // ---------------- Обновить приложение (hard reload + cache bust) ----------------
  container.querySelector('[data-slot="hard-refresh"]').addEventListener("click", async () => {
    if (!confirm("Сбросить кеш и перезагрузить страницу? Локальные настройки темы/userId сохранятся.")) return;

    // 1. Cache Storage API — удалить всё, что закэшировали (service workers, fetch cache)
    try {
      if ("caches" in window) {
        const keys = await caches.keys();
        await Promise.all(keys.map((k) => caches.delete(k)));
      }
    } catch (_e) {
      // не критично
    }

    // 2. Снять service workers, если зачем-то прописаны
    try {
      if (navigator.serviceWorker && navigator.serviceWorker.getRegistrations) {
        const regs = await navigator.serviceWorker.getRegistrations();
        await Promise.all(regs.map((r) => r.unregister()));
      }
    } catch (_e) {
      // не критично
    }

    // 3. Чистим sessionStorage, но СОХРАНЯЕМ userId и theme
    const keepUserId = localStorage.getItem("userId");
    const keepTheme = localStorage.getItem("theme");
    try {
      sessionStorage.clear();
    } catch (_e) {
      // ignore
    }
    try {
      localStorage.clear();
      if (keepUserId) localStorage.setItem("userId", keepUserId);
      if (keepTheme) localStorage.setItem("theme", keepTheme);
    } catch (_e) {
      // ignore
    }

    // 4. Cache-buster — добавим к URL уникальный query-параметр, заставит браузер
    //    перезапросить index.html, а из него все статики (мы их грузим как ES modules).
    const base = location.origin + "/ui";
    const bust = "?nc=" + Date.now();
    location.replace(base + bust);
  });

  container.querySelector('[data-slot="save"]').addEventListener("click", async () => {
    const current = _collectValues(container, groups);
    const diff = _diffAgainstInitial(initial, current);
    if (!Object.keys(diff).length) {
      showToast("Нет изменений", "info");
      return;
    }
    if (!confirm(`Записать ${Object.keys(diff).length} значений в .env? Большинство настроек требуют перезапуска uvicorn.`)) return;
    try {
      const resp = await api.patchSettings(diff);
      if (resp.status === "noop") {
        showToast("Изменений не было", "info");
        return;
      }
      showToast("Сохранено в .env", "success");
      if (resp.restart_required) {
        restartSlot.hidden = false;
        restartSlot.innerHTML = `
          <div class="card settings__restart">
            <strong>Перезапусти uvicorn, чтобы новые настройки подхватились.</strong>
            <div class="t-secondary t-body-small" style="margin-top: var(--space-1);">
              В окне терминала где работает <code>./run.sh</code>: Ctrl+C, затем <code>./run.sh --no-install</code>.
            </div>
            <div class="t-muted t-body-small" style="margin-top: var(--space-2);">
              Файл: <code>${_esc(resp.env_file || "")}</code>
            </div>
          </div>`;
        restartSlot.scrollIntoView({ behavior: "smooth", block: "start" });
      }
      // Обновим baseline
      initial = current;
    } catch (e) {
      const msg = e instanceof ApiError ? `${e.code}: ${e.message}` : e.message;
      showToast(`Ошибка сохранения: ${msg}`, "error");
    }
  });

  await reload();
}

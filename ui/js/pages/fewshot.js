import { api } from "../api.js";
import { showToast } from "../components/toast.js";

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

const STATUS_CLS = { pending: "chip", approved: "chip--ok", rejected: "chip--bad" };

function _row(e) {
  return `
    <article class="fewshot-card" data-id="${_esc(e.id)}">
      <header class="fewshot-card__head">
        <span class="chip ${STATUS_CLS[e.status] || ""}">${_esc(e.status)}</span>
        <span class="t-muted t-body-small">${(e.created_at || "").slice(0, 19).replace("T", " ")} · ${_esc(e.created_by || "—")}</span>
        <div class="row" style="gap: var(--space-2); margin-left: auto;">
          ${e.status !== "approved" ? `<button class="btn btn--ghost btn--sm" data-act="approve">Approve</button>` : ""}
          ${e.status !== "rejected" ? `<button class="btn btn--ghost btn--sm" data-act="reject">Reject</button>` : ""}
          <button class="btn btn--ghost btn--sm" data-act="delete">✕</button>
        </div>
      </header>
      <div class="fewshot-card__body">
        <div><strong>User:</strong> <span class="t-secondary">${_esc(e.user_text)}</span></div>
        <div style="margin-top: var(--space-2);"><strong>Assistant:</strong> <span class="t-secondary">${_esc(e.assistant_text)}</span></div>
        ${e.note ? `<div class="t-muted t-body-small" style="margin-top: var(--space-2);">Заметка: ${_esc(e.note)}</div>` : ""}
      </div>
    </article>`;
}

async function _load(container, params) {
  const wrap = container.querySelector('[data-slot="list-wrap"]');
  wrap.innerHTML = '<span class="loader-l2"></span>';
  try {
    const items = await api.fewshotList(params);
    if (!items.length) {
      wrap.innerHTML = "<div class='empty-state'>Примеров нет.</div>";
      return;
    }
    wrap.innerHTML = items.map(_row).join("");
    wrap.querySelectorAll(".fewshot-card").forEach((card) => {
      const id = card.dataset.id;
      card.querySelectorAll("[data-act]").forEach((btn) => {
        btn.addEventListener("click", async (e) => {
          e.stopPropagation();
          const act = btn.dataset.act;
          try {
            if (act === "approve") await api.fewshotReview(id, { status: "approved" });
            else if (act === "reject") await api.fewshotReview(id, { status: "rejected" });
            else if (act === "delete") {
              if (!confirm("Удалить пример?")) return;
              await api.fewshotDelete(id);
            }
            showToast("Готово", "success");
            await _load(container, params);
          } catch (err) {
            showToast(`Ошибка: ${err.message}`, "error");
          }
        });
      });
    });
  } catch (e) {
    wrap.innerHTML = `<div class='empty-state'>Не удалось: ${_esc(e.message)}</div>`;
  }
}

function _openEditor(container, onSave) {
  const slot = container.querySelector('[data-slot="editor"]');
  slot.hidden = false;
  slot.innerHTML = `
    <form class="card" style="margin-top: var(--space-4); display: flex; flex-direction: column; gap: var(--space-3);">
      <h3>Новый few-shot пример</h3>
      <div class="field">
        <label>User-сообщение</label>
        <textarea name="user_text" rows="3" required></textarea>
      </div>
      <div class="field">
        <label>Эталонный ответ ассистента</label>
        <textarea name="assistant_text" rows="8" required></textarea>
      </div>
      <div class="field">
        <label>Заметка</label>
        <input name="note">
      </div>
      <div class="row" style="gap: var(--space-2);">
        <button type="submit" class="btn btn--primary">Сохранить (на модерацию)</button>
        <button type="button" class="btn btn--ghost" data-slot="cancel">Отмена</button>
      </div>
    </form>`;
  const form = slot.querySelector("form");
  slot.querySelector('[data-slot="cancel"]').addEventListener("click", () => {
    slot.hidden = true;
    slot.innerHTML = "";
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(form);
    try {
      await api.fewshotCreate({
        set_name: "assistant",
        user_text: fd.get("user_text"),
        assistant_text: fd.get("assistant_text"),
        note: fd.get("note") || null,
      });
      showToast("Создано", "success");
      slot.hidden = true;
      slot.innerHTML = "";
      onSave();
    } catch (err) {
      showToast(`Ошибка: ${err.message}`, "error");
    }
  });
}

export async function renderFewshot(container) {
  const html = await (await fetch("/ui/static/pages/fewshot.html")).text();
  container.innerHTML = html;

  let status = "all";
  const params = () => (status === "all" ? {} : { status });

  await _load(container, params());

  container.querySelectorAll("[data-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      container.querySelectorAll("[data-filter]").forEach((b) => b.classList.remove("is-selected"));
      btn.classList.add("is-selected");
      status = btn.dataset.filter;
      _load(container, params());
    });
  });

  container.querySelector('[data-slot="new-btn"]').addEventListener("click", () => {
    _openEditor(container, () => _load(container, params()));
  });
}

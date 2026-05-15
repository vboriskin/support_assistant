/* «История диалогов» — список conversations + сообщения выбранного. */

import { api } from "../api.js";
import { renderSourceCard } from "../components/source-card.js";

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function _renderMarkup(text) {
  return _esc(text)
    .replace(/\[(\d+)\]/g, "<strong>[$1]</strong>")
    .replace(/\n/g, "<br>");
}

function _fbBadge(value) {
  if (value === 1) return `<span class="chip chip--ok">👍</span>`;
  if (value === -1) return `<span class="chip chip--bad">👎</span>`;
  return "";
}

function _convRow(c) {
  const updated = (c.updated_at || "").slice(0, 16).replace("T", " ");
  return `
    <li data-id="${_esc(c.id)}" class="history__item">
      <div class="history__title">${_esc(c.title || "Без названия")}</div>
      <div class="t-secondary t-body-small">
        ${updated}${c.ticket_id ? ` · тикет <code>${_esc(c.ticket_id.slice(0, 8))}</code>` : ""}
      </div>
    </li>`;
}

function _renderMessages(detailSlot, conv) {
  const msgs = conv.messages || [];
  if (!msgs.length) {
    detailSlot.innerHTML = `
      <div class="empty-state">В этом диалоге пока нет сообщений.</div>`;
    return;
  }
  const items = msgs
    .map((m) => {
      const sources = m.used_sources || [];
      const isUser = m.role === "user";
      return `
        <div class="message ${isUser ? "message--user" : ""}">
          <div class="message__role">
            ${isUser ? "Вы" : "Ассистент"}
            <span class="t-muted t-body-small"> · ${(m.created_at || "").slice(0, 19).replace("T", " ")}</span>
            ${!isUser ? _fbBadge(m.feedback) : ""}
          </div>
          <div class="message__text">${_renderMarkup(m.content || "")}</div>
          ${
            !isUser && sources.length
              ? `<div class="message__sources" data-msg-sources="${_esc(m.id)}"></div>`
              : ""
          }
        </div>`;
    })
    .join("");
  detailSlot.innerHTML = `
    <h3>${_esc(conv.title || "Без названия")}</h3>
    <div class="t-secondary t-body-small" style="margin-bottom: var(--space-3);">
      ${(conv.created_at || "").slice(0, 19).replace("T", " ")}
      ${conv.ticket_id ? ` · тикет <code>${_esc(conv.ticket_id)}</code>` : ""}
    </div>
    ${items}`;
  // Источники
  msgs.forEach((m) => {
    if (m.role !== "assistant") return;
    const slot = detailSlot.querySelector(`[data-msg-sources="${CSS.escape(m.id)}"]`);
    if (!slot) return;
    for (const src of m.used_sources || []) {
      slot.appendChild(renderSourceCard(src));
    }
  });
}

async function _openConv(detailSlot, id) {
  detailSlot.innerHTML = '<span class="loader-l2"></span>';
  try {
    const conv = await api.getConversation(id);
    _renderMessages(detailSlot, conv);
  } catch (e) {
    detailSlot.innerHTML = `<div class="t-muted">Не удалось загрузить диалог: ${_esc(e.message)}</div>`;
  }
}

export async function renderHistory(container) {
  const html = await (await fetch("/ui/static/pages/history.html")).text();
  container.innerHTML = html;

  const listWrap = container.querySelector('[data-slot="list-wrap"]');
  const detailSlot = container.querySelector('[data-slot="detail"]');

  try {
    const items = await api.listConversations();
    if (!items.length) {
      listWrap.innerHTML = "<div class='empty-state'>История пуста — начните диалог на странице «Ассистент».</div>";
      return;
    }
    listWrap.innerHTML = `<ul class="history__list">${items.map(_convRow).join("")}</ul>`;
    const lis = listWrap.querySelectorAll(".history__item");
    lis.forEach((li) => {
      li.addEventListener("click", () => {
        listWrap.querySelectorAll(".history__item").forEach((x) => x.classList.remove("is-selected"));
        li.classList.add("is-selected");
        _openConv(detailSlot, li.dataset.id);
      });
    });
    // авто-открыть первый
    lis[0].click();
  } catch (e) {
    listWrap.innerHTML = `<div class='empty-state'>Не удалось загрузить: ${_esc(e.message)}</div>`;
  }
}

import { api, ApiError } from "../api.js";
import { renderSourceCard } from "../components/source-card.js";
import { showToast } from "../components/toast.js";

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function _renderMarkup(text) {
  return _esc(text)
    .replace(/\[(\d+)\]/g, '<span class="cite" data-cite="$1">[$1]</span>')
    .replace(/\n/g, "<br>");
}

function _bindCitationHighlight(bubble) {
  const sourcesSlot = bubble.querySelector('[data-slot="sources"]');
  const textSlot = bubble.querySelector('[data-slot="text"]');
  if (!sourcesSlot || !textSlot) return;
  const cites = textSlot.querySelectorAll(".cite");
  cites.forEach((cite) => {
    const idx = cite.dataset.cite;
    cite.addEventListener("mouseenter", () => {
      const card = sourcesSlot.querySelector(`[data-src-idx="${idx}"]`);
      if (card) {
        card.classList.add("is-highlight");
        card.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
      cite.classList.add("is-active");
    });
    cite.addEventListener("mouseleave", () => {
      const card = sourcesSlot.querySelector(`[data-src-idx="${idx}"]`);
      if (card) card.classList.remove("is-highlight");
      cite.classList.remove("is-active");
    });
  });
  sourcesSlot.querySelectorAll("[data-src-idx]").forEach((card) => {
    const idx = card.dataset.srcIdx;
    card.addEventListener("mouseenter", () => {
      textSlot.querySelectorAll(`[data-cite="${idx}"]`).forEach((c) => c.classList.add("is-active"));
    });
    card.addEventListener("mouseleave", () => {
      textSlot.querySelectorAll(`[data-cite="${idx}"]`).forEach((c) => c.classList.remove("is-active"));
    });
  });
}

function _userBubble(text, attachedLabel) {
  const el = document.createElement("div");
  el.className = "message message--user";
  el.innerHTML = `
    <div class="message__role">Вы${attachedLabel ? ` · контекст: ${_esc(attachedLabel)}` : ""}</div>
    <div class="message__text">${_esc(text)}</div>`;
  return el;
}

function _assistantBubble(query) {
  const el = document.createElement("div");
  el.className = "message";
  el.innerHTML = `
    <div class="message__role">Ассистент</div>
    <div class="message__text" data-slot="text"><span class="loader-l1"></span></div>
    <div class="message__sources" data-slot="sources"></div>
    <div class="message__clarify" data-slot="clarify" hidden></div>
    <div class="message__feedback" data-slot="feedback" hidden>
      <button class="btn btn--ghost btn--sm" data-fb="1">👍 Помогло</button>
      <button class="btn btn--ghost btn--sm" data-fb="-1">👎 Не помогло</button>
      <button class="btn btn--ghost btn--sm" data-slot="add-eval">+ В eval-набор</button>
      <button class="btn btn--ghost btn--sm" data-slot="add-fewshot">★ В few-shot</button>
    </div>`;
  // Сохраним query на пузыре — пригодится для «в eval-набор»
  el.dataset.query = query;
  return el;
}

// ----------------- Прицепить тикет -----------------

let _attached = null; // {id, external_id, subject, module, description}

function _renderAttached(container) {
  const slot = container.querySelector('[data-slot="attached"]');
  if (!_attached) {
    slot.hidden = true;
    slot.innerHTML = "";
    return;
  }
  slot.hidden = false;
  slot.innerHTML = `
    <div class="attached-ticket">
      <div class="attached-ticket__body">
        <span class="t-caption">Прицеплен</span>
        <span><code>${_esc(_attached.external_id || _attached.id)}</code>
          ${_attached.module ? ` · <span class="t-secondary">${_esc(_attached.module)}</span>` : ""}
          ${_attached.subject ? ` — ${_esc(_attached.subject)}` : ""}</span>
      </div>
      <button type="button" class="btn btn--ghost btn--sm" data-slot="detach" aria-label="Отцепить">✕</button>
    </div>`;
  slot.querySelector('[data-slot="detach"]').addEventListener("click", () => {
    _attached = null;
    _renderAttached(container);
  });
}

function _initPicker(container) {
  const input = container.querySelector('[data-slot="picker-input"]');
  const results = container.querySelector('[data-slot="picker-results"]');
  if (!input || !results) return;
  let timer = null;

  const hide = () => {
    results.hidden = true;
    results.innerHTML = "";
  };

  input.addEventListener("input", () => {
    const q = input.value.trim();
    if (timer) clearTimeout(timer);
    if (!q) {
      hide();
      return;
    }
    timer = setTimeout(async () => {
      try {
        const data = await api.listTickets({ q, page_size: 6 });
        const items = data.items || [];
        if (!items.length) {
          results.hidden = false;
          results.innerHTML = `<div class="ticket-picker__empty">Ничего не найдено</div>`;
          return;
        }
        results.hidden = false;
        results.innerHTML = items
          .map(
            (t) => `
              <button type="button" data-id="${_esc(t.id)}" class="ticket-picker__item">
                <code>${_esc(t.external_id)}</code>
                <span class="t-secondary"> · ${_esc(t.module || "—")}</span>
                · ${_esc((t.subject || "").slice(0, 60))}
              </button>`
          )
          .join("");
        results.querySelectorAll("[data-id]").forEach((btn) => {
          btn.addEventListener("mousedown", async (ev) => {
            ev.preventDefault(); // не уводить focus с input до клика
            try {
              const full = await api.getTicket(btn.dataset.id);
              _attached = {
                id: full.id,
                external_id: full.external_id,
                subject: full.subject,
                module: full.module,
                description: full.description,
              };
              _renderAttached(container);
              input.value = "";
              hide();
            } catch (e) {
              showToast("Не удалось получить тикет", "error");
            }
          });
        });
      } catch (e) {
        // тихо — пользователь продолжит вводить
      }
    }, 200);
  });
  input.addEventListener("blur", () => setTimeout(hide, 200));
}

// ----------------- Add to eval set -----------------

function _openEvalDialog({ query, sources }) {
  const expectedDefault = sources.slice(0, 3).map((s) => s.source_id).join("\n");
  const modal = document.createElement("div");
  modal.className = "modal-backdrop";
  modal.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true" aria-label="Добавить кейс в eval-набор">
      <header class="modal__header">
        <h3>Добавить кейс в eval-набор</h3>
        <button type="button" class="btn btn--ghost btn--sm" data-slot="close" aria-label="Закрыть">✕</button>
      </header>
      <form class="modal__body" data-slot="eval-form">
        <div class="field">
          <label>Запрос</label>
          <textarea name="query" rows="2" required>${_esc(query)}</textarea>
        </div>
        <div class="row">
          <div class="field" style="flex: 1;">
            <label>Тип кейса</label>
            <select name="edge_case_type">
              <option value="typical">typical</option>
              <option value="no_answer_in_kb">no_answer_in_kb</option>
              <option value="ambiguous">ambiguous</option>
              <option value="adversarial">adversarial</option>
            </select>
          </div>
          <div class="field" style="flex: 1;">
            <label>Категория</label>
            <input name="category" placeholder="например, документы">
          </div>
        </div>
        <div class="field">
          <label>Ожидаемые источники <span class="t-secondary">(по одному на строку)</span></label>
          <textarea name="expected_sources" rows="3" placeholder="kb_chunk:doc-loading#2">${_esc(expectedDefault)}</textarea>
        </div>
        <div class="field">
          <label>Должно упомянуть <span class="t-secondary">(через запятую)</span></label>
          <input name="must_mention" placeholder="PDF, до 10 МБ">
        </div>
        <div class="field">
          <label>Не должно упомянуть <span class="t-secondary">(через запятую)</span></label>
          <input name="must_not_mention" placeholder="пароль клиента">
        </div>
        <div class="field">
          <label>Краткое описание эталонного ответа</label>
          <textarea name="expected_answer_summary" rows="2"></textarea>
        </div>
        <footer class="modal__footer">
          <button type="button" class="btn btn--ghost" data-slot="cancel">Отмена</button>
          <button type="submit" class="btn btn--primary">Сохранить</button>
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

  modal.querySelector('[data-slot="eval-form"]').addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    const splitLines = (s) =>
      (s || "").split(/\r?\n/).map((x) => x.trim()).filter(Boolean);
    const splitComma = (s) =>
      (s || "").split(",").map((x) => x.trim()).filter(Boolean);
    const body = {
      query: fd.get("query").trim(),
      edge_case_type: fd.get("edge_case_type"),
      category: fd.get("category")?.trim() || null,
      expected_sources: splitLines(fd.get("expected_sources")),
      must_mention: splitComma(fd.get("must_mention")),
      must_not_mention: splitComma(fd.get("must_not_mention")),
      expected_answer_summary: fd.get("expected_answer_summary")?.trim() || "",
    };
    try {
      await api.createEvalCase(body);
      showToast("Кейс добавлен в eval-набор", "success");
      close();
    } catch (err) {
      showToast("Не удалось добавить кейс", "error");
    }
  });
}

// ----------------- Главный рендер -----------------

export async function renderAssistant(container) {
  const html = await (await fetch("/ui/static/pages/assistant.html")).text();
  container.innerHTML = html;

  _attached = null;
  _renderAttached(container);
  _initPicker(container);

  let conversationId = null;

  const history = container.querySelector('[data-slot="history"]');
  const form = container.querySelector('[data-slot="composer"]');
  const textarea = form.querySelector("textarea");

  textarea.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      form.requestSubmit();
    }
  });

  let pending = false;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (pending) return;
    const query = textarea.value.trim();
    if (!query) return;

    if (history.querySelector(".empty-state")) history.innerHTML = "";

    const attachedLabel = _attached
      ? `${_attached.external_id || _attached.id}${_attached.subject ? " — " + _attached.subject : ""}`
      : "";
    history.appendChild(_userBubble(query, attachedLabel));
    const bubble = _assistantBubble(query);
    history.appendChild(bubble);
    bubble.scrollIntoView({ behavior: "smooth", block: "end" });

    textarea.value = "";
    pending = true;
    form.querySelector("button[type=submit]").disabled = true;

    if (!conversationId) {
      try {
        const conv = await api.createConversation({
          title: query.slice(0, 80),
          ticket_id: _attached ? _attached.id : null,
        });
        conversationId = conv.id;
      } catch (e) {
        // тихо — feedback просто не подключится, чат продолжит работать
      }
    }

    const textSlot = bubble.querySelector('[data-slot="text"]');
    const sourcesSlot = bubble.querySelector('[data-slot="sources"]');
    const feedbackSlot = bubble.querySelector('[data-slot="feedback"]');
    let accumulated = "";
    let lastSources = [];
    let messageId = null;

    const ticketContext = _attached
      ? {
          ticket_id: _attached.id,
          subject: _attached.subject,
          description: _attached.description,
          module: _attached.module,
        }
      : null;

    const allowClarify = !!form.querySelector('[name="allow_clarify"]')?.checked;

    try {
      await api.chatStream({
        query,
        conversationId,
        ticketContext,
        allowClarify,
        onChunk: (chunk) => {
          if (chunk.type === "sources" && Array.isArray(chunk.sources)) {
            sourcesSlot.innerHTML = "";
            lastSources = chunk.sources;
            for (const src of chunk.sources) {
              sourcesSlot.appendChild(renderSourceCard(src));
            }
          } else if (chunk.type === "delta" && chunk.delta) {
            if (accumulated === "") textSlot.innerHTML = "";
            accumulated += chunk.delta;
            textSlot.innerHTML = _renderMarkup(accumulated);
            bubble.scrollIntoView({ behavior: "smooth", block: "end" });
          } else if (chunk.type === "final" && chunk.answer) {
            if (!accumulated) {
              textSlot.innerHTML = _renderMarkup(chunk.answer.text);
            }
            if (chunk.answer.used_sources && !sourcesSlot.childElementCount) {
              lastSources = chunk.answer.used_sources;
              for (const src of chunk.answer.used_sources) {
                sourcesSlot.appendChild(renderSourceCard(src));
              }
            }
            messageId = chunk.answer.message_id || null;
            // Bind citation highlight
            _bindCitationHighlight(bubble);

            // Multi-turn clarify
            const clarifySlot = bubble.querySelector('[data-slot="clarify"]');
            const clarifyQ = chunk.answer.clarify_question;
            if (clarifyQ && clarifySlot) {
              clarifySlot.hidden = false;
              clarifySlot.innerHTML = `
                <div class="clarify-banner">
                  <span class="chip chip--ok">уточнение</span>
                  <span>${_esc(clarifyQ)}</span>
                </div>
                <textarea class="clarify-reply" rows="2" placeholder="Ответьте оператору одной фразой и нажмите «Отправить»"></textarea>
                <button type="button" class="btn btn--ghost btn--sm" data-slot="clarify-send">Отправить уточнение</button>`;
              const sendBtn = clarifySlot.querySelector('[data-slot="clarify-send"]');
              const ta = clarifySlot.querySelector(".clarify-reply");
              ta.focus();
              sendBtn.addEventListener("click", () => {
                const reply = ta.value.trim();
                if (!reply) return;
                textarea.value = `${query} — уточнение: ${reply}`;
                clarifySlot.hidden = true;
                form.requestSubmit();
              });
            }

            feedbackSlot.hidden = false;
            const addBtn = feedbackSlot.querySelector('[data-slot="add-eval"]');
            if (addBtn) {
              addBtn.addEventListener("click", () =>
                _openEvalDialog({ query, sources: lastSources })
              );
            }
            const addFs = feedbackSlot.querySelector('[data-slot="add-fewshot"]');
            if (addFs) {
              addFs.addEventListener("click", async () => {
                try {
                  await api.fewshotCreate({
                    set_name: "assistant",
                    user_text: query,
                    assistant_text: chunk.answer.text || accumulated,
                    source_message_id: messageId,
                  });
                  showToast("Отправлено в few-shot на модерацию", "success");
                } catch (err) {
                  showToast(`Ошибка: ${err.message}`, "error");
                }
              });
            }
            const fbButtons = feedbackSlot.querySelectorAll("[data-fb]");
            const disableFb = () => {
              fbButtons.forEach((b) => (b.disabled = true));
            };
            if (!conversationId || !messageId) {
              disableFb();
            } else {
              fbButtons.forEach((btn) => {
                btn.addEventListener("click", async () => {
                  const value = parseInt(btn.dataset.fb, 10);
                  try {
                    await api.submitFeedback(conversationId, {
                      message_id: messageId,
                      feedback: value,
                    });
                    btn.classList.add("is-selected");
                    disableFb();
                    showToast("Спасибо за оценку", "success");
                  } catch (err) {
                    showToast("Не удалось сохранить оценку", "error");
                  }
                });
              });
            }
          } else if (chunk.type === "error") {
            textSlot.innerHTML = `<span class="t-muted">Ошибка: ${_esc(chunk.error || "unknown")}</span>`;
          }
        },
      });
    } catch (e) {
      console.error(e);
      const msg = e instanceof ApiError ? `${e.code}: ${e.message}` : e.message || "ошибка";
      textSlot.innerHTML = `<span class="t-muted">${_esc(msg)}</span>`;
      showToast("Не удалось получить ответ", "error");
    } finally {
      pending = false;
      form.querySelector("button[type=submit]").disabled = false;
    }
  });
}

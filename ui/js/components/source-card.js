/* Карточка источника + retrieval debug (этап «13 идей», #4). */

const TYPE_LABELS = {
  kb_chunk: "KB",
  kb_article: "KB",
  ticket_summary: "Тикет",
  ticket_symptom: "Симптом",
  ticket_full: "Тикет",
  playbook: "Плейбук",
};

const RETRIEVAL_LABEL = {
  vector: "вектор",
  fts: "FTS",
  both: "оба",
};

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function _debugRow(source) {
  const bits = [];
  if (source.retrieval_source) {
    bits.push(`<span class="chip">${_esc(RETRIEVAL_LABEL[source.retrieval_source] || source.retrieval_source)}</span>`);
  }
  if (source.vector_rank != null) {
    bits.push(`<span class="t-muted">v#${source.vector_rank} ${(source.vector_score ?? 0).toFixed(3)}</span>`);
  }
  if (source.text_rank != null) {
    bits.push(`<span class="t-muted">fts#${source.text_rank} ${(source.text_score ?? 0).toFixed(3)}</span>`);
  }
  return bits.length
    ? `<div class="source-card__debug t-body-small">${bits.join(" · ")}</div>`
    : "";
}

export function renderSourceCard(source) {
  const wrap = document.createElement("article");
  wrap.className = "source-card";
  wrap.dataset.type = source.source_type || "";
  wrap.dataset.srcIdx = String((source.rank ?? 0) + 1);
  const md = source.metadata || {};
  const meta = [];
  if (md.module) meta.push(_esc(md.module));
  if (md.created_at) meta.push(String(md.created_at).slice(0, 10));
  wrap.innerHTML = `
    <header class="source-card__header">
      <span class="source-card__badge">${_esc(TYPE_LABELS[source.source_type] || "Источник")}</span>
      <span class="t-caption">${meta.join(" · ")}</span>
      <span class="source-card__score">${(source.score || 0).toFixed(2)}</span>
    </header>
    <h4 class="source-card__title">[${(source.rank ?? 0) + 1}] ${_esc(source.title || "")}</h4>
    <p class="source-card__excerpt">${_esc(String(source.content || "").slice(0, 220))}${source.content && source.content.length > 220 ? "…" : ""}</p>
    ${_debugRow(source)}`;
  return wrap;
}

/* API-обёртка: единый формат заголовков, парсинг ошибок, SSE-поток, CSRF. */

const BASE = "/api";
const UNSAFE_METHODS = new Set(["POST", "PUT", "DELETE", "PATCH"]);
let _csrfToken = null;

export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message || code || `HTTP ${status}`);
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

function _headers(extra = {}) {
  return {
    "Content-Type": "application/json",
    "X-User-Id": localStorage.getItem("userId") || "anonymous",
    ...extra,
  };
}

async function ensureCsrfToken() {
  if (_csrfToken) return _csrfToken;
  try {
    const resp = await fetch(`${BASE}/csrf`, { headers: _headers() });
    if (!resp.ok) return null;
    const data = await resp.json();
    _csrfToken = data.token || null;
  } catch {
    _csrfToken = null;
  }
  return _csrfToken;
}

async function request(path, opts = {}) {
  const url = `${BASE}${path}`;
  const method = (opts.method || "GET").toUpperCase();
  const headers = _headers(opts.headers || {});
  if (UNSAFE_METHODS.has(method)) {
    const token = await ensureCsrfToken();
    if (token) headers["X-CSRF-Token"] = token;
  }
  let resp = await fetch(url, { ...opts, method, headers });
  // Токен мог протухнуть — один retry с новым.
  if (resp.status === 403 && UNSAFE_METHODS.has(method)) {
    _csrfToken = null;
    const token = await ensureCsrfToken();
    if (token) {
      headers["X-CSRF-Token"] = token;
      resp = await fetch(url, { ...opts, method, headers });
    }
  }
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({ error: "unknown", message: resp.statusText }));
    throw new ApiError(resp.status, body.error || "unknown", body.message || "", body.details);
  }
  const text = await resp.text();
  return text ? JSON.parse(text) : null;
}

export const api = {
  // Assistant
  chat({ query, conversationId = null, ticketContext = null, filters = null }) {
    return request("/assistant/chat", {
      method: "POST",
      body: JSON.stringify({
        query,
        conversation_id: conversationId,
        ticket_context: ticketContext,
        filters,
      }),
    });
  },

  async chatStream({ query, conversationId = null, ticketContext = null, allowClarify = false, onChunk, signal }) {
    const token = await ensureCsrfToken();
    const headers = _headers();
    if (token) headers["X-CSRF-Token"] = token;
    const resp = await fetch(`${BASE}/assistant/chat/stream`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        query,
        conversation_id: conversationId,
        ticket_context: ticketContext,
        allow_clarify: allowClarify,
      }),
      signal,
    });
    if (!resp.ok) {
      throw new ApiError(resp.status, "stream_failed", resp.statusText);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() || "";
      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data:")) continue;
        const data = line.slice(5).trim();
        if (data === "[DONE]") return;
        try {
          onChunk(JSON.parse(data));
        } catch (e) {
          console.warn("SSE: cannot parse", data);
        }
      }
    }
  },

  // Categorize
  categorize(body) {
    return request("/categorize", { method: "POST", body: JSON.stringify(body) });
  },

  // Tickets
  listTickets(params = {}) {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v != null && v !== "")
    ).toString();
    return request(`/tickets${qs ? "?" + qs : ""}`);
  },
  getTicket(id) {
    return request(`/tickets/${encodeURIComponent(id)}`);
  },

  // Ingest
  listIngestJobs() { return request("/ingest/jobs"); },
  getIngestJob(id) { return request(`/ingest/jobs/${encodeURIComponent(id)}`); },
  async uploadCsv(file) {
    const fd = new FormData();
    fd.append("file", file);
    const token = await ensureCsrfToken();
    const headers = { "X-User-Id": localStorage.getItem("userId") || "anonymous" };
    if (token) headers["X-CSRF-Token"] = token;
    const resp = await fetch(`${BASE}/ingest/csv`, {
      method: "POST",
      headers,
      body: fd,
    });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({ error: "upload_failed" }));
      throw new ApiError(resp.status, body.error || "upload_failed", body.message || resp.statusText);
    }
    return resp.json();
  },

  // Conversations
  listConversations() { return request("/conversations"); },
  createConversation(body) { return request("/conversations", { method: "POST", body: JSON.stringify(body) }); },
  getConversation(id) { return request(`/conversations/${encodeURIComponent(id)}`); },
  submitFeedback(conversationId, body) {
    return request(`/conversations/${encodeURIComponent(conversationId)}/feedback`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  // Tickets reindex
  reindexTicket(id) {
    return request(`/tickets/${encodeURIComponent(id)}/reindex`, { method: "POST" });
  },

  // Assistant analyze (категоризация + RAG + draft)
  analyze(body) {
    return request("/assistant/analyze", { method: "POST", body: JSON.stringify(body) });
  },

  // KB
  listKB(params = {}) {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v != null && v !== "")
    ).toString();
    return request(`/kb${qs ? "?" + qs : ""}`);
  },
  getKB(id) { return request(`/kb/${encodeURIComponent(id)}`); },
  createKB(body) { return request("/kb", { method: "POST", body: JSON.stringify(body) }); },
  updateKB(id, body) {
    return request(`/kb/${encodeURIComponent(id)}`, { method: "PUT", body: JSON.stringify(body) });
  },
  deleteKB(id) { return request(`/kb/${encodeURIComponent(id)}`, { method: "DELETE" }); },
  async bulkKB({ file, kind = "markdown", module = null }) {
    const fd = new FormData();
    fd.append("file", file);
    fd.append("kind", kind);
    if (module) fd.append("module", module);
    const token = await ensureCsrfToken();
    const headers = { "X-User-Id": localStorage.getItem("userId") || "anonymous" };
    if (token) headers["X-CSRF-Token"] = token;
    const resp = await fetch(`${BASE}/kb/bulk`, { method: "POST", headers, body: fd });
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({ error: "upload_failed" }));
      throw new ApiError(resp.status, body.error || "upload_failed", body.message || resp.statusText);
    }
    return resp.json();
  },

  // Stats / Evals
  dashboard(period = "week") {
    return request(`/stats/dashboard?period=${encodeURIComponent(period)}`);
  },
  runEvals(body) { return request("/evals/run", { method: "POST", body: JSON.stringify(body) }); },
  listEvalRuns() { return request("/evals/runs"); },
  getEvalRun(id) { return request(`/evals/runs/${encodeURIComponent(id)}`); },
  createEvalCase(body) {
    return request("/evals/cases", { method: "POST", body: JSON.stringify(body) });
  },
  diffEvalRuns(a, b) {
    return request(`/evals/diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
  },

  // ---- Новые ручки (этап «13 идей») ----
  llmCosts(period = "week") {
    return request(`/stats/llm-costs?period=${encodeURIComponent(period)}`);
  },
  coverage() { return request("/stats/coverage"); },
  healthDetails() { return request("/stats/health-details"); },

  listWeak(params = {}) {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v != null && v !== "")
    ).toString();
    return request(`/weak${qs ? "?" + qs : ""}`);
  },

  listAudit(params = {}) {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v != null && v !== "")
    ).toString();
    return request(`/audit${qs ? "?" + qs : ""}`);
  },

  listStaleKB(params = {}) {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v != null && v !== "")
    ).toString();
    return request(`/stale/kb${qs ? "?" + qs : ""}`);
  },

  pii_test(body) {
    return request("/pii/test", { method: "POST", body: JSON.stringify(body) });
  },

  promptsList(name = "system_assistant") {
    return request(`/prompts?name=${encodeURIComponent(name)}`);
  },
  promptCreate(body) {
    return request("/prompts", { method: "POST", body: JSON.stringify(body) });
  },
  promptActivate(id) {
    return request(`/prompts/${encodeURIComponent(id)}/activate`, { method: "POST" });
  },
  promptDelete(id) {
    return request(`/prompts/${encodeURIComponent(id)}`, { method: "DELETE" });
  },
  promptPreview(body) {
    return request("/prompts/preview", { method: "POST", body: JSON.stringify(body) });
  },

  fewshotList(params = {}) {
    const qs = new URLSearchParams(
      Object.entries(params).filter(([, v]) => v != null && v !== "")
    ).toString();
    return request(`/fewshot${qs ? "?" + qs : ""}`);
  },
  fewshotCreate(body) {
    return request("/fewshot", { method: "POST", body: JSON.stringify(body) });
  },
  fewshotReview(id, body) {
    return request(`/fewshot/${encodeURIComponent(id)}/review`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },
  fewshotDelete(id) {
    return request(`/fewshot/${encodeURIComponent(id)}`, { method: "DELETE" });
  },

  alertsStatus() { return request("/alerts/status"); },
  alertsTrigger() { return request("/alerts/trigger", { method: "POST" }); },

  // Settings
  getSettings() { return request("/settings"); },
  patchSettings(values) {
    return request("/settings", { method: "PATCH", body: JSON.stringify({ values }) });
  },
};

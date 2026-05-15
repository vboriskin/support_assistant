import { api } from "../api.js";
import { showToast } from "../components/toast.js";

function _esc(s) {
  return (s || "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

function _row(j, overrideProgress) {
  const meta = j.metadata || {};
  const total = j.total_items || 0;
  const processed = j.processed_items || 0;
  const failed = j.failed_items || 0;
  const skipped = meta.skipped || 0;
  const indexed = meta.indexed || 0;
  // Если есть live-данные от SSE — используем их (актуальнее, чем БД).
  const ov = overrideProgress;
  const handled = ov
    ? (ov.indexed || 0) + (ov.skipped || 0) + (ov.failed || 0)
    : processed + failed + skipped;
  const totalShown = ov?.total || total;
  const pct = totalShown > 0 ? Math.min(100, Math.round((handled / totalShown) * 100)) : 0;
  const detailIndexed = ov?.indexed ?? indexed;
  const detailSkipped = ov?.skipped ?? skipped;
  const detailFailed = ov?.failed ?? failed;
  const detail = totalShown > 0
    ? `${detailIndexed} indexed · ${detailSkipped} skipped · ${detailFailed} failed (из ${totalShown})`
    : "—";
  return `
    <tr data-job-id="${_esc(j.id)}">
      <td><code>${_esc(j.id.slice(0, 8))}</code></td>
      <td><span class="status-chip" data-status="${_esc(j.status)}">${_esc(j.status)}</span></td>
      <td class="t-mono"><span class="job-progress" data-slot="progress">${handled}/${totalShown} (${pct}%)</span></td>
      <td class="t-muted" style="font-size: 12px;" data-slot="detail">${_esc(detail)}</td>
      <td class="t-mono t-muted">${(j.created_at || "").slice(0, 19).replace("T", " ")}</td>
      <td class="t-muted">${_esc(j.error_message || "")}</td>
    </tr>`;
}

async function _refreshJobs(slot, liveMap) {
  const items = await api.listIngestJobs();
  if (!items.length) {
    slot.innerHTML = "<div class='empty-state'>Прогонов ещё не было.</div>";
    return items;
  }
  slot.innerHTML = `
    <table class="table">
      <thead><tr><th>ID</th><th>Статус</th><th>Прогресс</th><th>Сводка</th><th>Создан</th><th>Ошибка</th></tr></thead>
      <tbody>${items.map((j) => _row(j, liveMap?.get(j.id))).join("")}</tbody>
    </table>`;
  return items;
}

function _subscribeSSE(jobId, onProgress, onDone) {
  // Используем EventSource для GET SSE. CSRF не нужен (GET — safe).
  const es = new EventSource(`/api/ingest/jobs/${encodeURIComponent(jobId)}/stream`);
  es.onmessage = (e) => {
    if (e.data === "[DONE]") {
      es.close();
      onDone?.();
      return;
    }
    try {
      const payload = JSON.parse(e.data);
      if (payload.event === "progress" || payload.event === "done") {
        onProgress?.(payload.stats);
      }
      if (payload.event === "done") {
        es.close();
        onDone?.();
      }
    } catch {
      // ignore
    }
  };
  es.onerror = () => {
    // Сервер закроет соединение по done или 30s timeout — это нормально.
    // Здесь не реконнектимся: refreshJobs в фоне доберёт финальный статус.
  };
  return es;
}

export async function renderIngest(container) {
  const html = await (await fetch("/ui/static/pages/ingest.html")).text();
  container.innerHTML = html;

  const dz = container.querySelector('[data-slot="dropzone"]');
  const input = dz.querySelector("input");
  const jobsSlot = container.querySelector('[data-slot="jobs"]');

  /** job_id → {indexed, skipped, failed, total} (live от SSE) */
  const liveMap = new Map();

  const upload = async (file) => {
    try {
      const resp = await api.uploadCsv(file);
      const jobId = resp.job_id;
      showToast(`Запущен job ${jobId.slice(0, 8)}`, "success");
      liveMap.set(jobId, { indexed: 0, skipped: 0, failed: 0, total: 0 });
      await _refreshJobs(jobsSlot, liveMap);
      _subscribeSSE(
        jobId,
        (stats) => {
          liveMap.set(jobId, stats);
          const row = jobsSlot.querySelector(`tr[data-job-id="${CSS.escape(jobId)}"]`);
          if (!row) return;
          const total = stats.total || 0;
          const handled = (stats.indexed || 0) + (stats.skipped || 0) + (stats.failed || 0);
          const pct = total > 0 ? Math.min(100, Math.round((handled / total) * 100)) : 0;
          row.querySelector('[data-slot="progress"]').textContent =
            `${handled}/${total} (${pct}%)`;
          row.querySelector('[data-slot="detail"]').textContent =
            total > 0
              ? `${stats.indexed} indexed · ${stats.skipped} skipped · ${stats.failed} failed (из ${total})`
              : "—";
        },
        () => {
          // По завершении — финальный refresh подтягивает статус + metadata из БД.
          _refreshJobs(jobsSlot, liveMap).catch(() => {});
        },
      );
    } catch (e) {
      showToast(`Ошибка загрузки: ${e.message}`, "error");
    }
  };

  input.addEventListener("change", (e) => {
    const f = e.target.files?.[0];
    if (f) upload(f);
  });
  dz.addEventListener("dragover", (e) => {
    e.preventDefault();
    dz.classList.add("dropzone--over");
  });
  dz.addEventListener("dragleave", () => dz.classList.remove("dropzone--over"));
  dz.addEventListener("drop", (e) => {
    e.preventDefault();
    dz.classList.remove("dropzone--over");
    const f = e.dataTransfer.files?.[0];
    if (f) upload(f);
  });

  await _refreshJobs(jobsSlot, liveMap);

  // Резервный refresh раз в 10 сек — на случай если SSE отвалился.
  const timer = setInterval(() => {
    if (container.querySelector('[data-page="ingest"]')) {
      _refreshJobs(jobsSlot, liveMap).catch(() => {});
    } else {
      clearInterval(timer);
    }
  }, 10000);
}

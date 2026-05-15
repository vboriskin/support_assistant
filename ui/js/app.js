/* Корневой entrypoint UI. */

import { router } from "./router.js";
import { initTheme, renderThemeToggle } from "./components/theme-toggle.js";
import { renderDashboard } from "./pages/dashboard.js";
import { renderAssistant } from "./pages/assistant.js";
import { renderTickets } from "./pages/tickets.js";
import { renderHistory } from "./pages/history.js";
import { renderKB } from "./pages/kb.js";
import { renderIngest } from "./pages/ingest.js";
import { renderEvals } from "./pages/evals.js";
import { renderDescription } from "./pages/description.js";
import { renderArtifacts } from "./pages/artifacts.js";
import { renderWeak } from "./pages/weak.js";
import { renderAudit } from "./pages/audit.js";
import { renderHealth } from "./pages/health.js";
import { renderCosts } from "./pages/costs.js";
import { renderStale } from "./pages/stale.js";
import { renderPII } from "./pages/pii.js";
import { renderAlerts } from "./pages/alerts.js";
import { renderPrompts } from "./pages/prompts.js";
import { renderFewshot } from "./pages/fewshot.js";
import { renderInstructions } from "./pages/instructions.js";
import { renderSettings } from "./pages/settings.js";

initTheme();
renderThemeToggle(document.querySelector('[data-slot="theme"]'));

router.start(
  {
    "/dashboard": renderDashboard,
    "/assistant": renderAssistant,
    "/tickets": renderTickets,
    "/history": renderHistory,
    "/kb": renderKB,
    "/ingest": renderIngest,
    "/evals": renderEvals,
    "/weak": renderWeak,
    "/health": renderHealth,
    "/costs": renderCosts,
    "/prompts": renderPrompts,
    "/fewshot": renderFewshot,
    "/stale": renderStale,
    "/pii": renderPII,
    "/audit": renderAudit,
    "/alerts": renderAlerts,
    "/artifacts": renderArtifacts,
    "/instructions": renderInstructions,
    "/settings": renderSettings,
    "/description": renderDescription,
  },
  { container: document.getElementById("main"), defaultRoute: "/dashboard" },
);

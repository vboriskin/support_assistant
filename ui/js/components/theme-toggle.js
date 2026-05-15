/* Theme toggle: light | dark | auto. Persist в localStorage. */

const STORAGE_KEY = "app-theme";

export function initTheme() {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") {
    document.documentElement.setAttribute("data-theme", stored);
  }
}

export function setTheme(theme) {
  if (theme === "auto") {
    document.documentElement.removeAttribute("data-theme");
    localStorage.removeItem(STORAGE_KEY);
  } else {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem(STORAGE_KEY, theme);
  }
  document.dispatchEvent(new CustomEvent("theme:change", { detail: { theme } }));
  _refresh();
}

export function currentTheme() {
  const attr = document.documentElement.getAttribute("data-theme");
  if (attr === "light" || attr === "dark") return attr;
  return "auto";
}

function _refresh() {
  document.querySelectorAll(".theme-toggle button").forEach((btn) => {
    btn.setAttribute("aria-pressed", btn.dataset.theme === currentTheme() ? "true" : "false");
  });
}

const ICONS = {
  light: `
    <svg class="ico ico--sm" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="2.5"/>
      <line x1="8" y1="2"  x2="8"  y2="3"/>
      <line x1="8" y1="13" x2="8"  y2="14"/>
      <line x1="2" y1="8"  x2="3"  y2="8"/>
      <line x1="13" y1="8" x2="14" y2="8"/>
      <line x1="3.8" y1="3.8"  x2="4.5"  y2="4.5"/>
      <line x1="11.5" y1="11.5" x2="12.2" y2="12.2"/>
      <line x1="3.8" y1="12.2"  x2="4.5"  y2="11.5"/>
      <line x1="11.5" y1="4.5"  x2="12.2" y2="3.8"/>
    </svg>`,
  auto: `
    <svg class="ico ico--sm" viewBox="0 0 16 16" aria-hidden="true">
      <circle cx="8" cy="8" r="5"/>
      <path d="M8 3a5 5 0 0 0 0 10z" class="ico--fill"/>
    </svg>`,
  dark: `
    <svg class="ico ico--sm" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M12 9.5A5.5 5.5 0 1 1 6.5 4 4.5 4.5 0 0 0 12 9.5z"/>
    </svg>`,
};

export function renderThemeToggle(target) {
  target.innerHTML = `
    <div class="theme-toggle" role="group" aria-label="Тема">
      <button data-theme="light" type="button" title="Светлая" aria-label="Светлая тема">${ICONS.light}</button>
      <button data-theme="auto"  type="button" title="Системная" aria-label="Системная тема">${ICONS.auto}</button>
      <button data-theme="dark"  type="button" title="Тёмная" aria-label="Тёмная тема">${ICONS.dark}</button>
    </div>`;
  target.querySelectorAll(".theme-toggle button").forEach((btn) => {
    btn.addEventListener("click", () => setTheme(btn.dataset.theme));
  });
  _refresh();
}

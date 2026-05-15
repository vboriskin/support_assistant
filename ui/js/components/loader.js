/* Утилиты для loader'ов. */

export function loader(level = 2) {
  const el = document.createElement("span");
  el.className = `loader-l${level}`;
  el.setAttribute("aria-busy", "true");
  return el;
}

/* Простейший hash-роутер: #/path → render(container). */

const _routes = new Map();
let _container = null;
let _default = "/dashboard";

async function _navigate() {
  const hash = window.location.hash.replace(/^#/, "") || _default;
  const [path] = hash.split("?");
  // подсветка sidebar
  document.querySelectorAll("[data-route]").forEach((el) => {
    if (el.dataset.route === path) {
      el.setAttribute("aria-current", "page");
    } else {
      el.removeAttribute("aria-current");
    }
  });

  const render = _routes.get(path) || _routes.get(_default);
  if (!render) {
    _container.innerHTML = "<div class='empty-state'>Страница не найдена.</div>";
    return;
  }
  _container.innerHTML = "<div class='loader-l2' aria-busy='true'></div>";
  try {
    await render(_container);
  } catch (e) {
    console.error(e);
    _container.innerHTML = `<div class='empty-state'>Ошибка загрузки страницы: ${e.message}</div>`;
  }
}

export const router = {
  start(routes, { container, defaultRoute = "/dashboard" }) {
    _container = container;
    _default = defaultRoute;
    for (const [k, v] of Object.entries(routes)) _routes.set(k, v);
    window.addEventListener("hashchange", _navigate);
    if (!window.location.hash) window.location.hash = `#${_default}`;
    else _navigate();
  },
  go(path) {
    if (window.location.hash === `#${path}`) {
      _navigate();
    } else {
      window.location.hash = `#${path}`;
    }
  },
};

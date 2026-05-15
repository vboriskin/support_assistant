/* Страница «Инструкция» — статика, грузит HTML и подключает smooth-scroll по якорям. */

export async function renderInstructions(container) {
  const html = await (await fetch("/ui/static/pages/instructions.html")).text();
  container.innerHTML = html;

  // Smooth-scroll по якорям ToC
  container.querySelectorAll('.instructions__toc a[href^="#step-"]').forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      const id = a.getAttribute("href").slice(1);
      const target = container.querySelector("#" + CSS.escape(id));
      if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });
}

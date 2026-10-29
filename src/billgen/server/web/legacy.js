/* Shared editor chrome. Original IDs, handlers and document engines stay intact. */
(() => {
  "use strict";
  function boot() {
    if (document.getElementById("bg-legacy-nav")) return;
    const id = location.pathname.split("/")[1];
    const names = {
      wf: "Wells Fargo",
      ae: "American Express",
      merc: "Mercury / Capital One / SoFi",
      boa: "Bank of America",
      chase: "Chase · Personal",
      chasebiz: "Chase · Business",
      us: "US Bank · Personal",
      usbiz: "US Bank · Business",
      alliant: "Alliant",
      ally: "Ally Bank",
    };
    if (!names[id]) return;
    const body = document.body;
    body.classList.add("bg-legacy", `bg-legacy-${id}`);
    if (!document.querySelector('meta[name="viewport"]')) {
      const meta = document.createElement("meta");
      meta.name = "viewport";
      meta.content = "width=device-width, initial-scale=1";
      document.head.append(meta);
    }
    const sidebar = document.querySelector(
      id === "ae"
        ? "main > section:first-child"
        : id === "merc"
          ? "#layout > aside"
          : "#form, #side, #editor, #panel",
    );
    const preview = document.querySelector(
      id === "ae"
        ? "#previewWrap"
        : id === "wf"
          ? "#main"
          : id === "merc"
            ? "#layout > main"
            : "#preview",
    );
    if (sidebar) sidebar.classList.add("bg-editor-panel");
    if (preview) preview.classList.add("bg-preview-panel");
    const toolbars = [
      ...document.querySelectorAll(".toolbar, #bar, #toolbar, body > header"),
    ];
    toolbars.forEach((el) => el.classList.add("bg-editor-toolbar"));
    const nav = document.createElement("nav");
    nav.id = "bg-legacy-nav";
    nav.setAttribute("aria-label", "编辑器导航");
    const back = document.createElement("a");
    back.href = "/";
    back.textContent = "← 返回工作台";
    const title = document.createElement("strong");
    title.textContent = names[id];
    const tag = document.createElement("span");
    tag.className = "bg-editor-tag";
    tag.textContent = "专属编辑器";
    const actions = document.createElement("div");
    actions.className = "bg-nav-actions";
    nav.append(back, title, tag, actions);
    body.prepend(nav);
    function button(text, handler) {
      const el = document.createElement("button");
      el.type = "button";
      el.textContent = text;
      el.addEventListener("click", handler);
      actions.append(el);
      return el;
    }
    if (sidebar) {
      const toggle = button("收起表单", () => {
        const collapsed = body.classList.toggle("bg-panel-collapsed");
        toggle.textContent = collapsed ? "展开表单" : "收起表单";
        toggle.setAttribute("aria-expanded", String(!collapsed));
        // Existing fit handlers listen to resize; no engine methods are replaced.
        window.dispatchEvent(new Event("resize"));
      });
      toggle.setAttribute("aria-expanded", "true");
      if (sidebar.id) toggle.setAttribute("aria-controls", sidebar.id);
    }
    if (preview) {
      const fit = button("适应宽度", () => {
        const nativeFit = document.querySelector("#zoom-fit, #zFit");
        const zoom = document.getElementById("zoomSel");
        if (nativeFit) nativeFit.click();
        else if (zoom) {
          zoom.value = "fit";
          zoom.dispatchEvent(new Event("change", { bubbles: true }));
        } else body.classList.remove("bg-preview-actual");
        window.dispatchEvent(new Event("resize"));
      });
      fit.title = "按可用空间适配预览，保留原有缩放功能";
      if (!["ally", "alliant", "merc"].includes(id)) {
        button("100%", () => body.classList.toggle("bg-preview-actual"));
      }
    }
    const measure = () => {
      body.style.setProperty(
        "--bg-nav-height",
        `${nav.getBoundingClientRect().height}px`,
      );
      if (id === "merc") {
        const header = document.querySelector("body > header");
        if (header)
          body.style.setProperty(
            "--bg-toolbar-height",
            `${header.getBoundingClientRect().height}px`,
          );
      }
    };
    measure();
    if ("ResizeObserver" in window) {
      const observer = new ResizeObserver(measure);
      observer.observe(nav);
      if (id === "merc") {
        const header = document.querySelector("body > header");
        if (header) observer.observe(header);
      }
    } else window.addEventListener("resize", measure);
    // Improve accessible names for generated fields without changing labels/IDs.
    function labelFields() {
      if (!sidebar) return;
      sidebar
        .querySelectorAll("input:not([type=hidden]), textarea, select")
        .forEach((field) => {
          if (field.hasAttribute("aria-label") || field.labels?.length) return;
          const previous = field.previousElementSibling;
          const row = field.closest(".row, .frow, .field, .fld");
          const label =
            previous?.tagName === "LABEL"
              ? previous
              : row?.querySelector("label");
          const text = label?.textContent.trim() || field.placeholder;
          if (text) field.setAttribute("aria-label", text);
        });
    }
    labelFields();
    if (sidebar)
      new MutationObserver(labelFields).observe(sidebar, {
        childList: true,
        subtree: true,
      });
  }
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
})();

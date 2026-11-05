/* 文档页签：主面板内嵌本地 Markdown，数据来自 /api/docs。 */

const DOCS = {
  items: null,
  active: "guide.md",
  loading: false,
};

function docsPath(path) {
  return String(path || "")
    .split("/")
    .map((part) => encodeURIComponent(part))
    .join("/");
}

function docsPanel() {
  const wrap = el("div", "docs-page docs-embedded");
  const side = el("aside", "docs-side");
  const nav = el("nav", "docs-nav");
  nav.append(el("div", "docs-loading", "目录读取中…"));
  side.append(nav);

  const content = el("article", "card docs-content md-body");
  content.append(el("p", "hint", "正在读取文档…"));
  content.addEventListener("click", (event) => {
    const link = event.target.closest("a");
    if (!link) return;
    const href = link.getAttribute("href") || "";
    if (!href.startsWith("/manual/")) return;
    event.preventDefault();
    loadDoc(decodeURIComponent(href.slice("/manual/".length)));
  });

  wrap.append(side, content);
  setTimeout(loadDocsIndex, 0);
  return wrap;
}

async function loadDocsIndex() {
  if (DOCS.loading) return;
  const nav = document.querySelector(".docs-embedded .docs-nav");
  if (!nav) return;
  if (DOCS.items) {
    paintDocsNav();
    loadDoc(DOCS.active);
    return;
  }
  DOCS.loading = true;
  try {
    const response = await fetch("/api/docs");
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.message || "读取目录失败");
    DOCS.items = data.items || [];
    const known = DOCS.items.some((item) => item.path === DOCS.active);
    if (!known) DOCS.active = data.default || DOCS.items[0]?.path || "guide.md";
    paintDocsNav();
    loadDoc(DOCS.active);
  } catch (error) {
    nav.replaceChildren(el("div", "docs-loading", `读取目录失败：${error.message}`));
  } finally {
    DOCS.loading = false;
  }
}

function paintDocsNav() {
  const nav = document.querySelector(".docs-embedded .docs-nav");
  if (!nav || !DOCS.items) return;
  nav.replaceChildren();
  const groups = new Map();
  for (const item of DOCS.items) {
    if (!groups.has(item.group)) groups.set(item.group, []);
    groups.get(item.group).push(item);
  }
  for (const [group, items] of groups) {
    const box = el("div", "docs-group");
    box.append(el("div", "docs-group-title", group));
    for (const item of items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "docs-link";
      button.textContent = item.label;
      button.dataset.path = item.path;
      if (item.path === DOCS.active) {
        button.classList.add("is-active");
        button.setAttribute("aria-current", "page");
      }
      button.addEventListener("click", () => loadDoc(item.path));
      box.append(button);
    }
    nav.append(box);
  }
}

async function loadDoc(path) {
  DOCS.active = path;
  const content = document.querySelector(".docs-embedded .docs-content");
  const nav = document.querySelector(".docs-embedded .docs-nav");
  if (!content) return;
  if (nav) {
    for (const button of nav.querySelectorAll(".docs-link")) {
      const active = button.dataset.path === path;
      button.classList.toggle("is-active", active);
      if (active) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
  }
  content.replaceChildren(el("p", "hint", "正在读取文档…"));
  try {
    const response = await fetch(`/api/docs/${docsPath(path)}`);
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.message || "读取文档失败");
    content.innerHTML = data.html;
    for (const link of content.querySelectorAll("a")) {
      const href = link.getAttribute("href") || "";
      if (href.startsWith("http://") || href.startsWith("https://")) {
        link.target = "_blank";
        link.rel = "noopener noreferrer";
      }
    }
    content.scrollTop = 0;
  } catch (error) {
    content.replaceChildren(el("p", "hint", `读取文档失败：${error.message}`));
  }
}

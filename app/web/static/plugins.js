/* 插件页：卡片 + 设置弹窗。由 app.js 的 render() 调用。
   文档/删除/重载/ZIP 安装的入口在下一步接上。 */

const PLUGINS = {
  loading: false,
  items: null,
};

function pluginLogo(title) {
  const node = document.createElement("span");
  node.className = "plugin-logo";
  node.textContent = (title || "?").slice(0, 1);
  let hash = 0;
  for (const ch of title || "") hash = (hash * 31 + ch.codePointAt(0)) % 360;
  node.style.setProperty("--logo-hue", String(hash));
  return node;
}

function pluginCard(plugin) {
  const card = document.createElement("article");
  card.className = "card plugin";
  if (plugin.error) card.classList.add("bad");

  const top = el("div", "plugin-top");
  top.append(pluginLogo(plugin.title));

  const titleBox = el("div", "plugin-title");
  const titleRow = el("div", "plugin-title-row");
  titleRow.append(el("h3", null, plugin.title));
  titleRow.append(el("span", "plugin-ver", "v" + plugin.version));
  titleBox.append(titleRow);
  if (plugin.author) titleBox.append(el("span", "plugin-author", plugin.author));
  top.append(titleBox);

  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.role = "switch";
  toggle.className = "input";
  toggle.checked = Boolean(plugin.enabled);
  toggle.disabled = Boolean(plugin.error);
  toggle.setAttribute("aria-label", `启用 ${plugin.title}`);
  toggle.addEventListener("change", () => togglePlugin(plugin, toggle.checked, toggle));
  top.append(toggle);
  card.append(top);

  card.append(el("p", "plugin-desc", plugin.error || plugin.description || "没有写说明。"));
  if (plugin.last_error) card.append(el("p", "plugin-hint", plugin.last_error));

  const actions = el("div", "plugin-actions");
  const docs = uiButton("文档", { variant: "outline", size: "sm" });
  docs.type = "button";
  docs.addEventListener("click", () => openReadmeDialog(plugin));
  actions.append(docs);

  if (plugin.config?.length) {
    const settings = uiButton("设置", { variant: "outline", size: "sm" });
    settings.type = "button";
    settings.addEventListener("click", () => openPluginDialog(plugin));
    actions.append(settings);
  }
  if (plugin.panel) {
    const link = uiButton("打开面板", { variant: "outline", size: "sm", href: "#" });
    link.href = plugin.panel;
    link.target = "_blank";
    link.rel = "noopener";
    if (!plugin.enabled) {
      link.classList.add("disabled");
      link.addEventListener("click", (event) => event.preventDefault());
    }
    actions.append(link);
  }
  const reload = uiButton("重载", { variant: "outline", size: "sm" });
  reload.type = "button";
  reload.addEventListener("click", () => reloadPlugin(plugin, reload));
  actions.append(reload);

  if (!actions.childElementCount) actions.append(el("span", "plugin-hint", "没有可调项"));
  card.append(actions);
  return card;
}

function pluginsPanel() {
  const wrap = el("div", "plugins");
  const bar = el("div", "plugins-head");
  const info = el("span", "plugins-count", "读取中");
  const refresh = uiButton("刷新", { variant: "outline", size: "sm" });
  refresh.type = "button";
  refresh.addEventListener("click", () => loadPlugins(true));
  bar.append(info, refresh);
  wrap.append(bar);

  const grid = el("div", "plugin-grid");
  grid.id = "plugin-grid";
  wrap.append(grid);

  setTimeout(loadPlugins, 0);
  return wrap;
}

async function loadPlugins(force = false) {
  const grid = $("plugin-grid");
  if (!grid) return;
  if (!force && PLUGINS.items) {
    paintPlugins(PLUGINS.items);
    return;
  }

  // 手动刷新时不抽掉已有卡片：整块淡下去，回来再淡上来，避免闪一行骨架
  const hasCards = Boolean(grid.querySelector(".plugin"));
  const count = document.querySelector(".plugins-count");
  if (hasCards) {
    grid.classList.add("is-refreshing");
    grid.setAttribute("aria-busy", "true");
    if (count) count.textContent = "刷新中…";
  } else {
    const frag = document.createDocumentFragment();
    for (let i = 0; i < 3; i += 1) {
      const row = el("div", "skeleton-row");
      row.append(el("div", "skeleton"));
      row.append(el("div", "skeleton"));
      frag.append(row);
    }
    grid.replaceChildren(frag);
  }

  try {
    const response = await fetch("/api/plugins");
    const data = await response.json();
    PLUGINS.items = data.plugins || [];
    paintPlugins(PLUGINS.items);
  } catch (error) {
    if (hasCards) {
      toast(`刷新失败：${error.message}`, "bad");
    } else {
      grid.replaceChildren(el("div", "card blank-card", `读取插件失败：${error.message}`));
    }
  } finally {
    grid.classList.remove("is-refreshing");
    grid.removeAttribute("aria-busy");
  }
}

function paintPlugins(items) {
  const grid = $("plugin-grid");
  if (!grid) return;
  const count = document.querySelector(".plugins-count");
  const on = items.filter((item) => item.enabled).length;
  if (count) count.textContent = `${items.length} 个插件 · ${on} 个已启用`;
  const tabCount = $("plugin-count");
  if (tabCount) {
    tabCount.textContent = String(items.length);
    // 计数会让页签变宽，正在选中时把 ink 重新贴一下
    if (typeof moveInk === "function") moveInk(false);
  }

  const frag = document.createDocumentFragment();
  if (!items.length) {
    frag.append(emptyPlugins());
  } else {
    for (const plugin of items) frag.append(pluginCard(plugin));
  }
  grid.replaceChildren(frag);
  if (items.length) openDeepLinkPlugin(items);
}

// /?plugin=<id> 直接打开某个插件的设置：面板里的插件卡片可以这么链过来
let DEEP_LINK_DONE = false;
function openDeepLinkPlugin(items) {
  if (DEEP_LINK_DONE) return;
  const wanted = new URLSearchParams(location.search).get("plugin");
  if (!wanted) return;
  DEEP_LINK_DONE = true;
  const plugin = items.find((item) => item.name === wanted);
  if (plugin && plugin.config?.length) openPluginDialog(plugin);
}

function emptyPlugins() {
  const box = el("div", "card plugin plugin-empty");
  box.append(el("h3", null, "还没有插件"));
  box.append(
    el(
      "p",
      "plugin-desc",
      "把插件目录放到 plugins/ 下，或从插件仓库部署一份；刷新后这里就会出现。带 config 的插件会多一个「设置」按钮，挂上 router 就会多一个「打开面板」。",
    ),
  );
  return box;
}

async function reloadPlugin(plugin, button) {
  button.disabled = true;
  try {
    const response = await fetch(`/api/plugins/${encodeURIComponent(plugin.name)}/reload`, {
      method: "POST",
    });
    const data = await response.json().catch(() => ({}));
    if (Array.isArray(data.plugins)) {
      PLUGINS.items = data.plugins;
      paintPlugins(PLUGINS.items);
    }
    toast(data.message || (data.ok ? "已重载" : "重载失败"), data.ok ? "ok" : "bad");
  } catch (error) {
    toast(`重载失败：${error.message}`, "bad");
  } finally {
    button.disabled = false;
  }
}

async function togglePlugin(plugin, enabled, input) {  input.disabled = true;
  try {
    const response = await fetch(`/api/plugins/${encodeURIComponent(plugin.name)}/enabled`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      input.checked = !enabled;
      toast(data.message || "切换失败", "bad");
      return;
    }
    PLUGINS.items = data.plugins || PLUGINS.items;
    paintPlugins(PLUGINS.items);
    toast(data.message || "已切换");
  } catch (error) {
    input.checked = !enabled;
    toast(`切换失败：${error.message}`, "bad");
  } finally {
    input.disabled = false;
  }
}

/* ---------- README 弹窗 ---------- */

async function openReadmeDialog(plugin) {
  try {
    const response = await fetch(`/api/plugins/${encodeURIComponent(plugin.name)}/readme`);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      toast(data.message || "读取 README 失败", "bad");
      return;
    }
    buildReadmeDialog(plugin, data);
  } catch (error) {
    toast(`读取 README 失败：${error.message}`, "bad");
  }
}

function buildReadmeDialog(plugin, data) {
  closeDialog();
  const panel = document.createElement("dialog");
  panel.id = "dialog";
  panel.className = "dialog readme";
  panel.addEventListener("click", (event) => {
    if (event.target === panel) closeDialog();
  });

  const box = el("div", "dialog-panel");
  const head = document.createElement("header");
  const titleRow = el("div", "dialog-title-row");
  titleRow.append(el("h2", null, `文档 · ${plugin.title}`));
  const close = uiButton("×", { variant: "ghost", size: "icon" });
  close.setAttribute("aria-label", "关闭");
  close.addEventListener("click", closeDialog);
  titleRow.append(close);
  head.append(titleRow);
  const meta = `${plugin.name} / ${data.filename}${data.truncated ? "（内容过长，已截断）" : ""}`;
  head.append(el("p", null, meta));
  box.append(head);

  const body = document.createElement("section");
  body.append(el("pre", "readme-body", data.text || "（空文件）"));
  box.append(body);

  const foot = document.createElement("footer");
  const done = uiButton("关闭", { variant: "outline" });
  done.type = "button";
  done.addEventListener("click", closeDialog);
  foot.append(done);
  box.append(foot);

  panel.append(box);
  document.body.append(panel);
  panel.tabIndex = -1;
  panel.showModal();
  panel.focus();
}

/* ---------- 设置弹窗 ---------- */

async function openPluginDialog(plugin) {
  let fields = plugin.config || [];
  let values = plugin.settings || {};
  try {
    const response = await fetch(`/api/plugins/${encodeURIComponent(plugin.name)}/settings`);
    const data = await response.json();
    if (response.ok && data.ok) {
      fields = data.fields || fields;
      values = data.values || values;
    } else if (data.message) {
      toast(data.message, "bad");
    }
  } catch (error) { /* 接口不通就按清单里的字段渲染 */ }
  buildPluginDialog(plugin, fields, values);
}

function buildPluginDialog(plugin, fields, values) {
  closeDialog();
  // 原生 <dialog>：焦点陷阱、ESC、背景遮罩都由浏览器负责
  const panel = document.createElement("dialog");
  panel.id = "dialog";
  panel.className = "dialog";
  panel.addEventListener("click", (event) => {
    if (event.target === panel) closeDialog();
  });

  const panelBox = el("div", "dialog-panel");
  const head = document.createElement("header");
  const titleRow = el("div", "dialog-title-row");
  titleRow.append(el("h2", null, plugin.title));
  const close = uiButton("×", { variant: "ghost", size: "icon" });
  close.setAttribute("aria-label", "关闭");
  close.addEventListener("click", closeDialog);
  titleRow.append(close);
  head.append(titleRow);
  if (plugin.description) head.append(el("p", null, plugin.description));
  panelBox.append(head);

  const body = document.createElement("section");
  const controls = {};
  if (!fields.length) {
    body.append(el("p", "hint", "这个插件没有通用设置项，参数在它自己的页面里调。"));
  }
  const groups = [];
  for (const field of fields) {
    const key = field.group || "";
    let group = groups.find((item) => item.key === key);
    if (!group) {
      group = { key, label: field.group_label || "", fields: [] };
      groups.push(group);
    }
    group.fields.push(field);
  }
  for (const group of groups) {
    const section = el("div", "dialog-group");
    if (group.label) section.append(el("h4", "dialog-group-title", group.label));
    const grid = el("div", "dialog-grid");
    for (const field of group.fields) {
      controls[field.key] = buildDialogField(field, values?.[field.key], grid);
    }
    section.append(grid);
    body.append(section);
  }
  panelBox.append(body);

  // show_if：依赖项没打开时把子选项藏起来
  const applyConditions = () => {
    for (const field of fields) {
      const condition = field.show_if;
      if (!condition || !condition.key) continue;
      const node = body.querySelector(`[data-field="${field.key}"]`);
      const read = controls[condition.key];
      if (!node || !read) continue;
      node.hidden = String(read()) !== String(condition.value);
    }
  };
  body.addEventListener("input", applyConditions);
  body.addEventListener("change", applyConditions);
  applyConditions();

  const foot = document.createElement("footer");
  if (plugin.panel) {
    const link = uiButton("打开插件页面", { variant: "outline", size: "sm", href: plugin.panel });
    link.target = "_blank";
    link.rel = "noopener";
    link.classList.add("dialog-link");
    foot.append(link);
  }
  const cancel = uiButton("取消", { variant: "outline" });
  cancel.type = "button";
  cancel.addEventListener("click", closeDialog);
  const save = uiButton("保存");
  save.type = "button";
  if (!fields.length) save.hidden = true;
  save.addEventListener("click", async () => {
    const payload = {};
    for (const [key, control] of Object.entries(controls)) payload[key] = control();
    save.disabled = true;
    try {
      const response = await fetch(`/api/plugins/${encodeURIComponent(plugin.name)}/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values: payload }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        toast(data.message || "保存失败", "bad");
        return;
      }
      plugin.settings = data.settings;
      closeDialog();
      toast(data.message || "设置已保存");
    } catch (error) {
      toast(`保存失败：${error.message}`, "bad");
    } finally {
      save.disabled = false;
    }
  });
  foot.append(cancel, save);
  panelBox.append(foot);
  panel.append(panelBox);
  document.body.append(panel);
  panel.tabIndex = -1;
  panel.showModal();
  panel.focus();
}

function buildDialogField(field, value, host) {
  const wrap = el("div", "field");
  wrap.dataset.field = field.key;
  const head = el("div", "field-head");
  const label = document.createElement("label");
  label.textContent = field.label || field.key;
  label.htmlFor = `p-${field.key}`;
  head.append(label, el("span", "key", field.key));
  wrap.append(head);

  let read;
  if (field.type === "bool") {
    const row = el("div", "switch-row");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.role = "switch";
    input.className = "input";
    input.id = `p-${field.key}`;
    input.checked = Boolean(value);
    const mark = el("span", "state", input.checked ? "开" : "关");
    input.addEventListener("change", () => {
      mark.textContent = input.checked ? "开" : "关";
    });
    row.append(input, mark);
    wrap.append(row);
    read = () => input.checked;
  } else if (field.type === "options" && Array.isArray(field.options) && field.options.length) {
    const select = document.createElement("select");
    select.className = "select";
    select.id = `p-${field.key}`;
    const labels = Array.isArray(field.labels) ? field.labels : [];
    const current = value === null || value === undefined ? "" : String(value);
    let known = false;
    field.options.forEach((option, index) => {
      const node = document.createElement("option");
      node.value = String(option);
      node.textContent = labels[index] || String(option);
      if (String(option) === current) known = true;
      select.append(node);
    });
    if (!known && current) {
      const node = document.createElement("option");
      node.value = current;
      node.textContent = current;
      select.append(node);
    }
    select.value = current;
    wrap.append(select);
    read = () => select.value;
  } else if (field.multiline) {
    const area = document.createElement("textarea");
    area.id = `p-${field.key}`;
    area.rows = field.rows || 3;
    area.className = "textarea";
    area.value = value === null || value === undefined ? "" : String(value);
    wrap.append(area);
    read = () => area.value;
  } else {
    const input = document.createElement("input");
    input.className = "input";
    input.id = `p-${field.key}`;
    input.type = field.type === "number" ? "number" : "text";
    if (field.min !== undefined) input.min = field.min;
    if (field.max !== undefined) input.max = field.max;
    input.step = field.step ?? 1;
    input.value = value === null || value === undefined ? "" : String(value);
    wrap.append(input);
    read = () => (field.type === "number" ? Number(input.value) : input.value);
  }

  if (field.help) wrap.append(el("p", "hint", field.help));
  host.append(wrap);
  return read;
}

function closeDialog() {
  const node = $("dialog");
  if (!node) return;
  if (typeof node.close === "function") node.close();
  node.remove();
}

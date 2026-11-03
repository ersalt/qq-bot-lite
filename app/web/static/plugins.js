/* 插件页的前端片段。
   单独放在一个文件里，免得 app.js 继续膨胀；由 render() 调用。 */

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
  card.className = "plugin-card";
  if (plugin.error) card.classList.add("bad");

  const head = el("div", "plugin-head");
  head.append(pluginLogo(plugin.title));

  const titleBox = el("div", "plugin-title");
  const titleRow = el("div", "plugin-title-row");
  titleRow.append(el("h3", null, plugin.title));
  titleRow.append(el("span", "plugin-ver", "v" + plugin.version));
  titleBox.append(titleRow);
  if (plugin.author) titleBox.append(el("span", "plugin-author", plugin.author));
  head.append(titleBox);

  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "switch-btn";
  toggle.setAttribute("role", "switch");
  toggle.setAttribute("aria-checked", String(Boolean(plugin.enabled)));
  toggle.setAttribute("aria-label", `启用 ${plugin.title}`);
  toggle.disabled = Boolean(plugin.error);
  if (plugin.enabled) toggle.classList.add("on");
  toggle.append(el("span", "track"));
  toggle.addEventListener("click", () => {
    if (toggle.disabled) return;
    const next = !toggle.classList.contains("on");
    toggle.classList.toggle("on", next);
    toggle.setAttribute("aria-checked", String(next));
    togglePlugin(plugin, next, toggle);
  });
  head.append(toggle);
  card.append(head);

  card.append(el("p", "plugin-desc", plugin.error || plugin.description || "没有写说明。"));

  const actions = el("div", "plugin-actions");
  if (plugin.config?.length) {
    const settings = el("button", "btn btn-sm", "设置");
    settings.type = "button";
    settings.addEventListener("click", () => openPluginDialog(plugin));
    actions.append(settings);
  }
  if (plugin.panel) {
    const link = el("a", "btn btn-sm", "打开面板");
    link.href = plugin.panel;
    link.target = "_blank";
    link.rel = "noopener";
    if (!plugin.enabled) {
      link.classList.add("disabled");
      link.addEventListener("click", (event) => event.preventDefault());
    }
    actions.append(link);
  }
  if (!actions.childElementCount) actions.append(el("span", "plugin-hint", "没有可调项"));
  card.append(actions);
  return card;
}

function pluginsPanel() {
  const wrap = el("div", "plugins");
  const bar = el("div", "plugins-bar");
  const info = el("span", "plugins-count", "读取中");
  const refresh = el("button", "btn btn-sm", "刷新");
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
  grid.textContent = "";
  for (let i = 0; i < 3; i += 1) {
    const row = el("div", "skeleton-row");
    row.append(el("div", "skeleton"));
    row.append(el("div", "skeleton"));
    grid.append(row);
  }
  try {
    const response = await fetch("/api/plugins");
    const data = await response.json();
    PLUGINS.items = data.plugins || [];
    paintPlugins(PLUGINS.items);
  } catch (error) {
    grid.textContent = "";
    grid.append(el("div", "notice", `读取插件失败：${error.message}`));
  }
}

function paintPlugins(items) {
  const grid = $("plugin-grid");
  if (!grid) return;
  grid.textContent = "";
  const count = document.querySelector(".plugins-count");
  const on = items.filter((item) => item.enabled).length;
  if (count) count.textContent = `${items.length} 个插件 · ${on} 个已启用`;

  if (!items.length) {
    grid.append(emptyPlugins());
    return;
  }
  for (const plugin of items) grid.append(pluginCard(plugin));
  openDeepLinkPlugin(items);
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

function emptyPlugins(soft = false) {
  const box = el("div", soft ? "plugin-card plugin-empty soft" : "plugin-card plugin-empty");
  box.append(el("h3", null, "还没有插件"));
  box.append(
    el(
      "p",
      "plugin-desc",
      "在 app/extensions/ 下放一个 .py，导出 MANIFEST 就会被这里发现。带 config 的插件会多一个“设置”按钮，挂上 router 就会多一个“打开面板”。",
    ),
  );
  const link = el("a", "btn btn-sm", "看写插件说明");
  link.href = "https://github.com/";
  link.addEventListener("click", (event) => event.preventDefault());
  box.append(link);
  return box;
}

async function togglePlugin(plugin, enabled, button) {
  button.disabled = true;
  try {
    const response = await fetch(`/api/plugins/${encodeURIComponent(plugin.name)}/enabled`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      button.classList.toggle("on", !enabled);
      toast(data.message || "切换失败", "bad");
      return;
    }
    PLUGINS.items = data.plugins || PLUGINS.items;
    paintPlugins(PLUGINS.items);
    toast(data.message || "已切换");
    const restart = $("restart-notice");
    if (restart) restart.hidden = true;
  } catch (error) {
    button.classList.toggle("on", !enabled);
    toast(`切换失败：${error.message}`, "bad");
  } finally {
    button.disabled = false;
  }
}

/* ---------- 设置弹窗 ---------- */

/* 字段从 /api/plugins/<id>/settings 现拿：插件可以在这里补动态选项
   （比如本机装了哪些模型）。拿不到就用列表里带的那份兜底。 */
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
  const overlay = el("div", "overlay");
  overlay.id = "dialog";
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) closeDialog();
  });

  const panel = el("div", "dialog");
  const head = el("div", "dialog-head");
  head.append(el("h2", null, plugin.title));
  const close = el("button", "icon-btn", "×");
  close.type = "button";
  close.addEventListener("click", closeDialog);
  head.append(close);
  panel.append(head);
  if (plugin.description) panel.append(el("p", "dialog-sub", plugin.description));

  const body = el("div", "dialog-body");
  const controls = {};
  if (!fields.length) {
    // 插件把设置全放自己页面里了（settings_fields 返回空列表）
    body.append(el("p", "hint", "这个插件没有通用设置项，参数在它自己的页面里调。"));
  }
  // 按 group 分块：字段多了以后平铺一屏根本找不到
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
  panel.append(body);

  // show_if：依赖项没打开时把子选项藏起来（比如 wiki_enable 关掉就别管同步时间）
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

  const foot = el("div", "dialog-foot");
  // 愿意自己画界面的插件，设置弹窗里留个口子过去
  if (plugin.panel) {
    const left = el("div", "dialog-foot-left");
    const link = el("a", "btn btn-sm", "打开插件页面");
    link.href = plugin.panel;
    link.target = "_blank";
    link.rel = "noopener";
    left.append(link);
    foot.append(left);
  }
  const cancel = el("button", "btn", "取消");
  cancel.type = "button";
  cancel.addEventListener("click", closeDialog);
  const save = el("button", "btn btn-primary", "保存");
  save.type = "button";
  if (!fields.length) save.hidden = true;
  save.addEventListener("click", async () => {
    const values = {};
    for (const [key, control] of Object.entries(controls)) values[key] = control();
    save.disabled = true;
    try {
      const response = await fetch(`/api/plugins/${encodeURIComponent(plugin.name)}/settings`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ values }),
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
  panel.append(foot);
  overlay.append(panel);
  document.body.append(overlay);
  requestAnimationFrame(() => overlay.classList.add("show"));
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
    const box = el("div", "switch");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.id = `p-${field.key}`;
    input.checked = Boolean(value);
    const track = el("label", "track");
    track.htmlFor = input.id;
    const mark = el("span", "state", input.checked ? "开" : "关");
    input.addEventListener("change", () => {
      mark.textContent = input.checked ? "开" : "关";
    });
    box.append(input, track, mark);
    wrap.append(box);
    read = () => input.checked;
  } else if (field.type === "options" && Array.isArray(field.options) && field.options.length) {
    // 下拉框：选项和显示名分开给，值仍然是原始字符串
    const select = document.createElement("select");
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
    area.className = "dialog-textarea";
    area.value = value === null || value === undefined ? "" : String(value);
    wrap.append(area);
    read = () => area.value;
  } else {
    const input = document.createElement("input");
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
  if (node) node.remove();
}

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDialog();
});


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

  const toggle = document.createElement("input");
  toggle.type = "checkbox";
  toggle.checked = Boolean(plugin.enabled);
  toggle.disabled = Boolean(plugin.error);
  toggle.setAttribute("aria-label", `启用 ${plugin.title}`);
  const track = el("label", "track");
  const switchWrap = el("label", "switch plugin-switch");
  switchWrap.append(toggle, track);
  toggle.addEventListener("change", () => togglePlugin(plugin, toggle.checked, toggle));
  head.append(switchWrap);
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

async function togglePlugin(plugin, enabled, input) {
  input.disabled = true;
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
    const restart = $("restart-notice");
    if (restart) restart.hidden = true;
  } catch (error) {
    input.checked = !enabled;
    toast(`切换失败：${error.message}`, "bad");
  } finally {
    input.disabled = false;
  }
}

/* ---------- 设置弹窗 ---------- */

function openPluginDialog(plugin) {
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

  const body = el("div", "dialog-body grid");
  const controls = {};
  for (const field of plugin.config) {
    controls[field.key] = buildDialogField(field, plugin.settings?.[field.key], body);
  }
  panel.append(body);

  const foot = el("div", "dialog-foot");
  const cancel = el("button", "btn", "取消");
  cancel.type = "button";
  cancel.addEventListener("click", closeDialog);
  const save = el("button", "btn btn-primary", "保存");
  save.type = "button";
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


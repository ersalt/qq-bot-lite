/* 插件页：卡片 + 设置/文档/删除弹窗 + 重载/ZIP 安装。由 app.js 的 render() 调用。 */

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
  if (plugin.repository) {
    const repo = document.createElement("a");
    repo.className = "plugin-repo";
    repo.href = plugin.repository;
    repo.target = "_blank";
    repo.rel = "noopener noreferrer";
    repo.textContent = plugin.repository.includes("github.com") ? "GitHub" : "仓库";
    repo.title = plugin.repository;
    titleBox.append(repo);
  }
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
  if (plugin.warning) card.append(el("p", "plugin-warn", plugin.warning));
  if (plugin.last_error) card.append(el("p", "plugin-hint", plugin.last_error));

  const actions = el("div", "plugin-actions");
  if (plugin.config?.length) {
    const settings = uiButton("设置", { variant: "outline", size: "sm" });
    settings.type = "button";
    settings.addEventListener("click", () => openPluginDialog(plugin));
    actions.append(settings);
  }
  const docs = uiButton("文档", { variant: "outline", size: "sm" });
  docs.type = "button";
  docs.addEventListener("click", () => openReadmeDialog(plugin));
  actions.append(docs);
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

  const remove = uiButton("删除", { variant: "destructive", size: "sm" });
  remove.type = "button";
  remove.addEventListener("click", () => openDeleteDialog(plugin));
  actions.append(remove);

  if (!actions.childElementCount) actions.append(el("span", "plugin-hint", "没有可调项"));
  card.append(actions);
  return card;
}

function pluginsPanel() {
  const wrap = el("div", "plugins");
  const bar = el("div", "plugins-head");
  const info = el("span", "plugins-count", "读取中");

  const tools = el("div", "plugins-actions");
  const install = uiButton("安装 ZIP", { variant: "outline", size: "sm" });
  install.type = "button";
  install.title = "ZIP 里必须是 <插件id>/plugin.toml";
  install.addEventListener("click", () => openInstallDialog());

  const refresh = uiButton("刷新", { variant: "outline", size: "sm" });
  refresh.type = "button";
  refresh.addEventListener("click", () => loadPlugins(true));
  tools.append(install, refresh);
  bar.append(info, tools);
  wrap.append(bar);

  const grid = el("div", "plugin-grid");
  grid.id = "plugin-grid";
  wrap.append(grid);

  setTimeout(loadPlugins, 0);
  return wrap;
}

function isZipFile(file) {
  if (!file) return false;
  if (!file.name.toLowerCase().endsWith(".zip")) {
    toast("只支持 .zip 文件", "bad");
    return false;
  }
  if (file.size > 8 * 1024 * 1024) {
    toast("ZIP 不能超过 8MB", "bad");
    return false;
  }
  return true;
}

async function installPlugin(file, button) {
  if (!isZipFile(file)) return false;
  button.disabled = true;
  const label = button.textContent;
  button.textContent = "安装中…";
  try {
    const response = await fetch(
      `/api/plugins/install?name=${encodeURIComponent(file.name)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: file,
      },
    );
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) {
      toast(data.message || "安装失败", "bad");
      return false;
    }
    PLUGINS.items = data.plugins || [];
    paintPlugins(PLUGINS.items);
    toast(data.message || "已安装，记得在卡片上启用");
    return true;
  } catch (error) {
    toast(`安装失败：${error.message}`, "bad");
    return false;
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

function openInstallDialog() {
  closeDialog();
  const panel = document.createElement("dialog");
  panel.id = "dialog";
  panel.className = "modal install";
  panel.addEventListener("click", (event) => {
    if (event.target === panel) closeDialog();
  });

  const box = el("div", "dialog-panel");
  const head = document.createElement("header");
  const titleRow = el("div", "dialog-title-row");
  titleRow.append(el("h2", null, "安装插件 ZIP"));
  head.append(titleRow);
  head.append(el("p", null, "压缩包里只能有一个顶层插件目录"));
  box.append(head);

  const body = document.createElement("section");
  const drop = document.createElement("button");
  drop.type = "button";
  drop.className = "dropzone";
  drop.append(el("span", "drop-kicker", "插件包"));
  drop.append(el("strong", null, "把 ZIP 拖到这里"));
  drop.append(el("small", null, "或点击选择文件 · 最大 8MB"));
  const picked = el("span", "drop-file");
  picked.hidden = true;
  drop.append(picked);
  body.append(drop);
  body.append(
    el(
      "p",
      "hint",
      "支持 <插件id>/plugin.toml，或 ZIP 根目录直接放 plugin.toml（ZIP 名就是插件 id）。安装后默认不启用，用卡片开关启用。",
    ),
  );

  const picker = document.createElement("input");
  picker.type = "file";
  picker.accept = ".zip,application/zip";
  picker.hidden = true;
  body.append(picker);
  box.append(body);

  const foot = document.createElement("footer");
  const cancel = uiButton("取消", { variant: "outline" });
  cancel.type = "button";
  cancel.addEventListener("click", closeDialog);
  const submit = uiButton("安装");
  submit.type = "button";
  submit.disabled = true;
  foot.append(cancel, submit);
  box.append(foot);

  let selected = null;
  const choose = (file) => {
    if (!isZipFile(file)) return;
    selected = file;
    picked.textContent = `${file.name} · ${(file.size / 1048576).toFixed(2)} MB`;
    picked.hidden = false;
    drop.classList.add("has-file");
    submit.disabled = false;
    picker.value = "";
  };

  drop.addEventListener("click", () => picker.click());
  picker.addEventListener("change", () => choose(picker.files?.[0]));
  drop.addEventListener("dragover", (event) => {
    event.preventDefault();
    drop.classList.add("dragover");
  });
  drop.addEventListener("dragleave", () => drop.classList.remove("dragover"));
  drop.addEventListener("drop", (event) => {
    event.preventDefault();
    drop.classList.remove("dragover");
    choose(event.dataTransfer?.files?.[0]);
  });

  submit.addEventListener("click", async () => {
    if (!selected) return;
    const ok = await installPlugin(selected, submit);
    if (ok) closeDialog();
  });

  panel.append(box);
  document.body.append(panel);
  panel.tabIndex = -1;
  panel.showModal();
  drop.focus();
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

/* ---------- 删除弹窗 ---------- */

function openDeleteDialog(plugin) {
  closeDialog();
  const panel = document.createElement("dialog");
  panel.id = "dialog";
  panel.className = "modal";
  panel.addEventListener("click", (event) => {
    if (event.target === panel) closeDialog();
  });

  const box = el("div", "dialog-panel");
  const head = document.createElement("header");
  const titleRow = el("div", "dialog-title-row");
  titleRow.append(el("h2", null, "删除插件"));
  head.append(titleRow);
  head.append(el("p", null, `${plugin.title} · ${plugin.name}`));
  box.append(head);

  const body = document.createElement("section");
  body.append(el("p", "hint", "插件目录会被删除，不能撤销。"));
  if (plugin.enabled) body.append(el("p", "hint", "插件正在运行，删除前会先停用它。"));

  const options = el("div", "delete-options");
  const makeOption = (value, title, note) => {
    const label = el("label", "delete-option");
    const input = document.createElement("input");
    input.type = "radio";
    input.name = "delete-data";
    input.value = value;
    if (value === "keep") input.checked = true;
    const text = el("span");
    text.append(el("b", null, title));
    text.append(el("small", null, note));
    label.append(input, text);
    return label;
  };
  options.append(
    makeOption("keep", "只删插件", "保留 data/plugin_data/ 下的设置与数据"),
    makeOption("purge", "连持久化数据一起删", `同时删除 data/plugin_data/${plugin.name}/`),
  );
  body.append(options);

  const confirm = el("label", "delete-confirm");
  const check = document.createElement("input");
  check.type = "checkbox";
  check.id = "delete-confirm";
  confirm.append(check, el("span", null, "我知道删除后不能恢复"));
  body.append(confirm);
  box.append(body);

  const foot = document.createElement("footer");
  const cancel = uiButton("取消", { variant: "outline" });
  cancel.type = "button";
  cancel.addEventListener("click", closeDialog);
  const remove = uiButton("删除插件", { variant: "destructive" });
  remove.type = "button";
  remove.disabled = true;
  foot.append(cancel, remove);
  box.append(foot);

  const purgeSelected = () =>
    body.querySelector('input[name="delete-data"][value="purge"]').checked;
  const paintLabel = () => {
    remove.textContent = purgeSelected() ? "删除插件和数据" : "删除插件";
  };
  body.addEventListener("change", paintLabel);
  check.addEventListener("change", () => {
    remove.disabled = !check.checked;
  });

  remove.addEventListener("click", async () => {
    if (!check.checked) return;
    remove.disabled = true;
    remove.textContent = "正在删除…";
    try {
      const response = await fetch(
        `/api/plugins/${encodeURIComponent(plugin.name)}?data=${purgeSelected() ? 1 : 0}`,
        { method: "DELETE" },
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        toast(data.message || "删除失败", "bad");
        remove.disabled = false;
        paintLabel();
        return;
      }
      PLUGINS.items = data.plugins || [];
      paintPlugins(PLUGINS.items);
      closeDialog();
      toast(data.warning || data.message || "插件已删除", data.warning ? "bad" : "ok");
    } catch (error) {
      toast(`删除失败：${error.message}`, "bad");
      remove.disabled = false;
      paintLabel();
    }
  });

  panel.append(box);
  document.body.append(panel);
  panel.tabIndex = -1;
  panel.showModal();
  panel.focus();
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
  panel.className = "modal readme";
  panel.addEventListener("click", (event) => {
    if (event.target === panel) closeDialog();
  });

  const box = el("div", "dialog-panel");
  const head = document.createElement("header");
  const titleRow = el("div", "dialog-title-row");
  titleRow.append(el("h2", null, `文档 · ${plugin.title}`));
  head.append(titleRow);
  const meta = `${plugin.name} / ${data.filename}${data.truncated ? "（内容过长，已截断）" : ""}`;
  head.append(el("p", null, meta));
  box.append(head);

  const body = document.createElement("section");
  const content = el("div", "md-body");
  if (data.html) {
    // 服务端已经过 markdown + nh3 白名单清洗；这里只负责挂上去
    content.innerHTML = data.html;
    content.querySelectorAll("a").forEach((link) => {
      link.target = "_blank";
      link.rel = "noopener noreferrer";
    });
  } else {
    content.append(el("pre", "readme-body", data.text || "（空文件）"));
  }
  body.append(content);
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
  panel.className = "modal";
  panel.addEventListener("click", (event) => {
    if (event.target === panel) closeDialog();
  });

  const panelBox = el("div", "dialog-panel");
  const head = document.createElement("header");
  const titleRow = el("div", "dialog-title-row");
  titleRow.append(el("h2", null, plugin.title));
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

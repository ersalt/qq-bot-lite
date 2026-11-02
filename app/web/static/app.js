/* 设置面板前端：纯原生，无构建链。 */

const state = {
  schema: [],
  original: {},
  values: {},
  dirty: new Set(),
  saving: false,
  restart: [],
  active: null,
};

const $ = (id) => document.getElementById(id);

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

function toast(message, kind = "ok") {
  const node = $("toast");
  node.textContent = message;
  node.className = `toast show ${kind}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => {
    node.className = "toast";
  }, 2600);
}

/* ---------- 渲染 ---------- */

function buildField(field, value) {
  const wrap = el("div", "field");
  wrap.dataset.key = field.key;

  const head = el("div", "field-head");
  const label = el("label", null, field.label);
  label.htmlFor = `f-${field.key}`;
  head.append(label, el("span", "key", field.key));
  if (field.restart) head.append(el("span", "badge", "需重启"));
  wrap.append(head);

  let control;
  if (field.type === "bool") {
    const box = el("div", "switch");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.id = `f-${field.key}`;
    input.checked = Boolean(value);
    const track = el("label", "track");
    track.htmlFor = input.id;
    const mark = el("span", "state", input.checked ? "开" : "关");
    input.addEventListener("change", () => {
      mark.textContent = input.checked ? "开" : "关";
      onEdit(field, input.checked);
    });
    box.append(input, track, mark);
    control = box;
  } else if (field.type === "select") {
    const select = document.createElement("select");
    select.id = `f-${field.key}`;
    for (const option of field.options || []) {
      const node = document.createElement("option");
      node.value = option;
      node.textContent = option === "" ? "不发送（用模型默认）" : option;
      select.append(node);
    }
    select.value = value === null || value === undefined ? "" : String(value);
    select.addEventListener("change", () => onEdit(field, select.value));
    control = select;
  } else {
    const input = document.createElement("input");
    input.id = `f-${field.key}`;
    input.type = field.type === "password" ? "password" : field.type === "number" ? "number" : "text";
    if (field.type === "number") {
      if (field.min !== undefined) input.min = field.min;
      if (field.max !== undefined) input.max = field.max;
      input.step = field.step !== undefined ? field.step : 1;
    }
    if (field.type === "list") {
      input.value = Array.isArray(value) ? value.join(", ") : "";
      input.placeholder = "逗号分隔";
    } else if (value === null || value === undefined) {
      input.value = "";
      input.placeholder = field.optional ? "留空 = 不发送" : "";
    } else {
      input.value = String(value);
    }
    input.addEventListener("input", () => onEdit(field, input.value));
    control = input;
  }

  wrap.append(control);

  if (field.help) wrap.append(el("p", "hint", field.help));
  const err = el("p", "err");
  err.dataset.for = field.key;
  wrap.append(err);
  return wrap;
}

function render() {
  const host = $("sections");
  host.textContent = "";
  const nav = $("nav");
  nav.textContent = "";

  for (const section of state.schema) {
    const chip = el("a", "chip");
    chip.href = `#${section.id}`;
    chip.dataset.section = section.id;
    chip.append(el("span", null, section.title));
    chip.append(el("span", "dirty-dot"));
    chip.addEventListener("click", (event) => {
      event.preventDefault();
      showSection(section.id);
    });
    nav.append(chip);

    const block = el("section", "panel");
    block.id = `sec-${section.id}`;
    block.dataset.section = section.id;
    block.hidden = true;

    if (section.id === "plugins") {
      block.classList.add("panel-flush");
      block.append(pluginsPanel());
    } else {
      const grid = el("div", "grid");
      for (const field of section.fields) {
        grid.append(buildField(field, state.values[field.key]));
      }
      block.append(grid);
      extras(section, block);
    }
    host.append(block);
  }

  showSection(validSection(state.active) || state.schema[0]?.id);
}

function validSection(id) {
  return state.schema.some((section) => section.id === id) ? id : null;
}

function savedSection() {
  try {
    return validSection(localStorage.getItem("qqbotlite-section"));
  } catch (error) {
    return null;
  }
}

function showSection(id) {
  const target = validSection(id);
  if (!target) return;
  state.active = target;

  for (const panel of document.querySelectorAll("#sections > section")) {
    panel.hidden = panel.dataset.section !== target;
  }
  for (const chip of document.querySelectorAll("#nav a")) {
    chip.classList.toggle("active", chip.dataset.section === target);
  }

  const section = state.schema.find((item) => item.id === target);
  const actions = document.querySelector(".toolbar-actions");
  if (actions) actions.hidden = target === "plugins";
  $("page-title").textContent = section ? section.title : "设置";
  $("page-sub").textContent = section?.desc || "改动即时写回 config/config.toml";

  try {
    localStorage.setItem("qqbotlite-section", target);
    history.replaceState(null, "", `#${target}`);
  } catch (error) {}
  window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
}

/* ---------- 各组的额外挂件 ---------- */

function extras(section, block) {
  if (section.id === "llm") block.append(llmCheckRow());
  if (section.id === "persona") block.append(personaEditor());
}

function llmCheckRow() {
  const row = el("div", "checkrow");
  const button = el("button", "btn", "测试连接");
  button.type = "button";
  const result = el("span", "checkresult", "");
  button.addEventListener("click", async () => {
    button.disabled = true;
    result.textContent = "探测中";
    result.className = "checkresult";
    try {
      const response = await fetch("/api/llm/check");
      const data = await response.json();
      result.className = `checkresult ${data.ok && data.loaded ? "ok" : data.ok ? "warn" : "bad"}`;
      const latency = data.latency_ms !== undefined ? ` · ${data.latency_ms}ms` : "";
      result.textContent = `${data.message}${latency}`;
    } catch (error) {
      result.className = "checkresult bad";
      result.textContent = `探测失败：${error.message}`;
    } finally {
      button.disabled = false;
    }
  });
  row.append(button, result);
  return row;
}

function personaEditor() {
  const box = el("div", "editor");
  const head = el("div", "editor-head");
  head.append(el("span", "editor-title", "人设正文"));
  const path = el("span", "key", "");
  const counter = el("span", "key", "");
  const save = el("button", "btn btn-primary", "保存人设");
  save.type = "button";
  head.append(path, counter, save);

  const area = document.createElement("textarea");
  area.id = "persona-text";
  area.spellcheck = false;
  area.rows = 16;
  area.placeholder = "人设 Markdown 正文";

  const hint = el("p", "hint", "改完点保存即时生效，不用重启。人设里写死篇幅能直接决定回复长度和思考量。");

  box.append(head, area, hint);

  const refreshCounter = () => {
    counter.textContent = `${area.value.length} 字`;
  };
  area.addEventListener("input", refreshCounter);

  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      const response = await fetch("/api/persona", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: area.value }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        toast(data.message || "人设保存失败", "bad");
        return;
      }
      toast(`人设已保存并生效（${data.chars} 字）`);
      refreshCounter();
    } catch (error) {
      toast(`人设保存失败：${error.message}`, "bad");
    } finally {
      save.disabled = false;
    }
  });

  fetch("/api/persona")
    .then((response) => response.json())
    .then((data) => {
      area.value = data.text || "";
      path.textContent = data.path || "";
      refreshCounter();
    })
    .catch(() => {
      hint.textContent = "读取人设失败";
    });

  return box;
}

function fieldMeta(key) {
  for (const section of state.schema) {
    for (const field of section.fields) {
      if (field.key === key) return { field, section };
    }
  }
  return {};
}

/* ---------- 编辑 ---------- */

function readControl(field) {
  const input = $(`f-${field.key}`);
  if (field.type === "bool") return input.checked;
  const raw = input.value;
  if (field.type === "list") return raw;
  if (field.type === "number") {
    if (raw.trim() === "") return field.optional ? null : 0;
    return raw;
  }
  return raw;
}

function onEdit(field, _value) {
  const value = readControl(field);
  const before = state.original[field.key];
  const same = JSON.stringify(normalizeForCompare(value, field)) === JSON.stringify(normalizeForCompare(before, field));
  const key = field.key;
  if (same) {
    if (state.dirty.has(key)) {
      state.dirty.delete(key);
      state.values[key] = value;
    }
  } else {
    state.dirty.add(key);
    state.values[key] = value;
  }
  const err = document.querySelector(`.err[data-for="${key}"]`);
  if (err) err.textContent = "";
  $(`f-${key}`).classList.remove("invalid");
  refreshDirtyUi();
}

function normalizeForCompare(value, field) {
  if (value === null || value === undefined || value === "") {
    if (field.type === "bool") return false;
    if (field.type === "number") return field.optional ? null : 0;
    if (field.type === "list") return [];
    return "";
  }
  if (field.type === "number") return Number(value);
  if (field.type === "list") {
    if (Array.isArray(value)) return value;
    return String(value).split(",").map((item) => item.trim()).filter(Boolean);
  }
  if (field.type === "bool") return Boolean(value);
  return String(value);
}

function refreshDirtyUi() {
  $("save").disabled = state.dirty.size === 0 || state.saving;
  $("reload").disabled = state.dirty.size === 0 || state.saving;
  $("foot-state").textContent = state.dirty.size === 0 ? "已同步" : `${state.dirty.size} 项待保存`;

  const perSection = {};
  for (const key of state.dirty) {
    const meta = fieldMeta(key);
    if (meta.section) perSection[meta.section.id] = true;
  }
  for (const anchor of document.querySelectorAll("#nav a")) {
    anchor.classList.toggle("dirty", Boolean(perSection[anchor.dataset.section]));
  }
}

/* ---------- 保存 ---------- */

async function save() {
  if (state.saving || state.dirty.size === 0) return;
  state.saving = true;
  $("save").disabled = true;
  $("foot-state").textContent = "保存中";

  const updates = {};
  for (const key of state.dirty) {
    const meta = fieldMeta(key);
    if (!meta.field) continue;
    updates[key] = readControl(meta.field);
  }

  try {
    const response = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ updates }),
    });
    const payload = await response.json();
    if (!response.ok || payload.errors?.length) {
      for (const item of payload.errors || []) {
        const err = document.querySelector(`.err[data-for="${item.field}"]`);
        if (err) err.textContent = item.message;
        const input = document.getElementById(`f-${item.field}`);
        if (input) input.classList.add("invalid");
      }
      toast(payload.errors?.length ? "有字段不合法，已标红" : "保存失败", "bad");
      return;
    }
    state.original = { ...payload.values };
    state.values = { ...payload.values };
    state.dirty.clear();
    state.restart = payload.restart || [];
    const notice = $("restart-notice");
    if (state.restart.length) {
      notice.hidden = false;
      notice.textContent = `以下设置需要重启进程才生效：${state.restart.join("、")}`;
    } else {
      notice.hidden = true;
    }
    toast("已保存并生效");
    render();
  } catch (error) {
    toast(`保存失败：${error.message}`, "bad");
  } finally {
    state.saving = false;
    refreshDirtyUi();
  }
}

async function load() {
  const host = $("sections");
  host.textContent = "";
  for (let i = 0; i < 4; i += 1) {
    const row = el("div", "skeleton-row");
    row.append(el("div", "skeleton"));
    row.append(el("div", "skeleton"));
    host.append(row);
  }

  try {
    const response = await fetch("/api/config");
    const payload = await response.json();
    state.schema = [
      ...payload.sections,
      {
        id: "plugins",
        title: "插件",
        desc: "app/extensions/ 下的每个模块就是一张卡。带 config 的多一个“设置”，挂上 router 的多一个“打开面板”。",
        fields: [],
      },
    ];
    state.values = { ...payload.values };
    state.original = { ...payload.values };
    state.dirty.clear();
    state.active = validSection(location.hash.slice(1)) || savedSection() || payload.sections[0]?.id;
    $("foot-meta").textContent = ` · ${payload.sections.length} 组 ${Object.keys(payload.values).length} 项`;
    render();
    refreshDirtyUi();
  } catch (error) {
    host.textContent = "";
    const box = el("div", "notice", `读取配置失败：${error.message}`);
    host.append(box);
    $("foot-state").textContent = "加载失败";
  }
}

/* ---------- 主题 ---------- */

function initTheme() {
  const button = $("theme");
  if (!button) return;
  const paint = (theme) => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("qqbotlite-theme", theme);
    } catch (error) {}
    button.textContent = theme === "dark" ? "◑" : "◐";
    button.title = theme === "dark" ? "切到浅色" : "切到深色";
  };
  paint(document.documentElement.getAttribute("data-theme") || "light");
  button.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") || "light";
    paint(current === "dark" ? "light" : "dark");
  });
}

/* ---------- 状态与导航 ---------- */

async function pollStatus() {
  try {
    const response = await fetch("/api/status");
    const data = await response.json();
    const dot = $("conn-dot");
    const online = Boolean(data.connected);
    dot.className = `dot ${online ? "on" : "off"}`;
    $("conn-text").textContent = online ? `已连接 ${data.self_id ?? ""}` : "等待 SnowLuma 接入";
    $("stat-line").textContent = `${data.counters?.received ?? 0} 收 / ${data.counters?.replied ?? 0} 回`;
  } catch {
    $("conn-dot").className = "dot off";
    $("conn-text").textContent = "状态不可用";
  }
}

function watchSections() {
  // 分页之后不再需要滚动联动，保留空实现让老调用点不用改。
}

$("save").addEventListener("click", save);
$("form").addEventListener("submit", (event) => event.preventDefault());
$("reload").addEventListener("click", () => {
  load().then(watchSections);
  $("restart-notice").hidden = true;
  toast("已还原为当前生效的配置");
});

document.addEventListener("keydown", (event) => {
  if ((event.ctrlKey || event.metaKey) && event.key === "s") {
    event.preventDefault();
    save();
  }
});
window.addEventListener("beforeunload", (event) => {
  if (state.dirty.size > 0) event.preventDefault();
});

initTheme();
load().then(watchSections);
pollStatus();
setInterval(pollStatus, 10000);



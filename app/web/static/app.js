/* 设置面板前端：纯原生，无构建链。
   骨架：顶栏 + 页头 + 页内页签 + 平级白卡 + 设置行。规范见 docs/ui-spec.md。 */

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

const reducedMotion = () =>
  window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ---------- 字段 ---------- */

function buildRow(field, value) {
  const row = el("div", "row");
  const head = el("div", "row-head");
  const label = el("label", null, field.label);
  label.htmlFor = `f-${field.key}`;
  head.append(label, el("span", "key", field.key));
  if (field.restart) head.append(el("span", "tag", "需重启"));
  row.append(head);

  if (field.help) row.append(el("p", "hint", field.help));

  const ctl = el("div", "row-ctl");
  let control;

  if (field.type === "bool") {
    ctl.classList.add("tight");
    const wrap = el("div", "switch-row");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.role = "switch";
    input.className = "input";
    input.id = `f-${field.key}`;
    input.checked = Boolean(value);
    const mark = el("span", "state", input.checked ? "开" : "关");
    input.addEventListener("change", () => {
      mark.textContent = input.checked ? "开" : "关";
      onEdit(field);
    });
    wrap.append(input, mark);
    ctl.append(wrap);
    control = input;
  } else if (field.type === "select") {
    const select = document.createElement("select");
    select.className = "select";
    select.id = `f-${field.key}`;
    for (const option of field.options || []) {
      const node = document.createElement("option");
      node.value = option;
      node.textContent = option === "" ? "不发送（用模型默认）" : option;
      select.append(node);
    }
    select.value = value === null || value === undefined ? "" : String(value);
    select.addEventListener("change", () => onEdit(field));
    ctl.append(select);
    control = select;
  } else {
    const input = document.createElement("input");
    input.className = "input";
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
    input.addEventListener("input", () => onEdit(field));
    ctl.append(input);
    control = input;
  }

  const err = el("p", "err");
  err.dataset.for = field.key;
  ctl.append(err);
  row.append(ctl);
  return row;
}

function rowsOf(fields) {
  const box = el("div", "rows");
  for (const field of fields) box.append(buildRow(field, state.values[field.key]));
  return box;
}

function plainSection(section) {
  const card = el("section", "card");
  const pad = el("div", "card-pad");
  pad.append(rowsOf(section.fields));
  card.append(pad);
  return card;
}

/* ---------- 渲染 ---------- */

function render() {
  const tabs = $("tabs");
  const host = $("sections");
  tabs.textContent = "";
  host.textContent = "";

  for (const section of state.schema) {
    const tab = el("button", "tabbtn");
    tab.type = "button";
    tab.id = `tab-${section.id}`;
    tab.dataset.section = section.id;
    tab.setAttribute("aria-controls", `sec-${section.id}`);
    tab.setAttribute("aria-current", "false");
    tab.append(el("span", null, section.title));
    if (section.id === "plugins") {
      const count = el("span", "count", "0");
      count.id = "plugin-count";
      tab.append(count);
    }
    tab.append(el("i", "tab-dot"));
    tab.addEventListener("click", () => showSection(section.id));
    tabs.append(tab);

    const panel = el("section", "panel");
    panel.id = `sec-${section.id}`;
    panel.dataset.section = section.id;
    panel.setAttribute("role", "region");
    panel.setAttribute("aria-labelledby", tab.id);
    panel.hidden = true;

    if (section.id === "plugins") {
      panel.append(pluginsPanel());
    } else if (section.id === "llm") {
      panel.append(modelPanel(section));
    } else if (section.id === "persona") {
      panel.append(personaPanel(section));
    } else {
      panel.append(plainSection(section));
    }
    host.append(panel);
  }

  const ink = el("span", "tab-ink");
  ink.id = "tab-ink";
  ink.setAttribute("aria-hidden", "true");
  tabs.append(ink);

  showSection(validSection(state.active) || state.schema[0]?.id);
  refreshDirtyUi();
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
  const previous = state.active;
  state.active = target;

  for (const panel of document.querySelectorAll("#sections > section")) {
    const active = panel.dataset.section === target;
    panel.hidden = !active;
    if (active && previous && previous !== target && !reducedMotion()) {
      const order = state.schema.map((item) => item.id);
      const dir = order.indexOf(target) > order.indexOf(previous) ? 1 : -1;
      panel.animate(
        [
          { opacity: 0, transform: `translateX(${dir * 10}px)` },
          { opacity: 1, transform: "none" },
        ],
        { duration: 220, easing: "cubic-bezier(.16, 1, .3, 1)" },
      );
    }
  }

  for (const tab of document.querySelectorAll("#tabs .tabbtn")) {
    const on = tab.dataset.section === target;
    tab.setAttribute("aria-current", on ? "page" : "false");
    tab.tabIndex = on ? 0 : -1;
  }

  const section = state.schema.find((item) => item.id === target);
  const title = $("page-title");
  const sub = $("page-sub");
  if (title && sub) {
    const paint = () => {
      title.textContent = section ? section.title : "设置";
      sub.textContent = section?.desc || "改动即时写回 config/config.toml";
    };
    paint();
    if (!reducedMotion() && previous && previous !== target) {
      for (const node of [title, sub]) {
        node.animate(
          [
            { opacity: 0, transform: "translateY(2px)" },
            { opacity: 1, transform: "none" },
          ],
          { duration: 200, easing: "cubic-bezier(.16, 1, .3, 1)" },
        );
      }
    }
  }

  const actions = $("actions");
  // 人设页保留配置级保存（兜底昵称在配置里），正文另有自己的「保存人设」
  if (actions) actions.hidden = target === "plugins";

  try {
    localStorage.setItem("qqbotlite-section", target);
    history.replaceState(null, "", `#${target}`);
  } catch (error) {}
  moveInk(previous !== null && previous !== target);
  // 面板刚显示时实体卡才有尺寸，下一帧再贴选中框
  requestAnimationFrame(() => {
    if (typeof refreshEntityInk === "function") refreshEntityInk();
  });
  window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
}

function moveInk(animate) {
  const ink = $("tab-ink");
  const active = document.querySelector('#tabs [aria-current="page"]');
  if (!ink || !active) return;
  const left = active.offsetLeft;
  const width = active.offsetWidth;
  if (!animate) ink.style.transition = "none";
  ink.style.width = `${width}px`;
  ink.style.transform = `translateX(${left}px)`;
  if (!animate) {
    void ink.offsetWidth;
    ink.style.transition = "";
  }
}

/* ---------- 编辑 ---------- */

function readControl(field) {
  const input = $(`f-${field.key}`);
  if (!input) return state.values[field.key];
  if (field.type === "bool") return input.checked;
  const raw = input.value;
  if (field.type === "list") return raw;
  if (field.type === "number") {
    if (raw.trim() === "") return field.optional ? null : 0;
    return raw;
  }
  return raw;
}

function onEdit(field) {
  const value = readControl(field);
  const before = state.original[field.key];
  const same =
    JSON.stringify(normalizeForCompare(value, field)) ===
    JSON.stringify(normalizeForCompare(before, field));
  const key = field.key;
  if (same) {
    state.dirty.delete(key);
    state.values[key] = value;
  } else {
    state.dirty.add(key);
    state.values[key] = value;
  }
  const err = document.querySelector(`.err[data-for="${key}"]`);
  if (err) err.textContent = "";
  const input = $(`f-${key}`);
  if (input) input.classList.remove("invalid");
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
  const n = state.dirty.size;
  const label = $("dirty-label");
  if (label) {
    label.hidden = n === 0;
    label.textContent = `${n} 项待保存`;
  }
  const save = $("save");
  if (save) save.disabled = state.saving;
  const reload = $("reload");
  if (reload) reload.disabled = n === 0 || state.saving;
  const foot = $("foot-state");
  if (foot) foot.textContent = state.saving ? "保存中" : n === 0 ? "已同步" : `${n} 项待保存`;

  const perSection = {};
  for (const key of state.dirty) {
    const meta = fieldMeta(key);
    if (meta.section) perSection[meta.section.id] = true;
  }
  for (const tab of document.querySelectorAll("#tabs .tabbtn")) {
    tab.classList.toggle("dirty", Boolean(perSection[tab.dataset.section]));
  }
}

/* ---------- 保存 ---------- */

function fieldMeta(key) {
  for (const section of state.schema) {
    for (const field of section.fields) {
      if (field.key === key) return { field, section };
    }
  }
  return {};
}

/* 不重建 DOM，把当前值写回已有控件（模型/人设切换时用，避免整页闪一下） */
function syncSectionFields(sectionId) {
  const section = state.schema.find((item) => item.id === sectionId);
  if (!section) return;
  for (const field of section.fields) {
    const input = $(`f-${field.key}`);
    if (!input) continue;
    const value = state.values[field.key];
    if (field.type === "bool") input.checked = Boolean(value);
    else if (field.type === "list") input.value = Array.isArray(value) ? value.join(", ") : "";
    else input.value = value === null || value === undefined ? "" : String(value);
    input.classList.remove("invalid");
    const err = document.querySelector(`.err[data-for="${field.key}"]`);
    if (err) err.textContent = "";
  }
}

function flashCards(nodes) {
  if (reducedMotion()) return;
  for (const node of nodes || []) {
    node.animate(
      [{ opacity: .55 }, { opacity: 1 }],
      { duration: 200, easing: "cubic-bezier(.16, 1, .3, 1)" },
    );
  }
}

function showNotice(restart) {
  const node = $("restart-notice");
  const text = $("restart-text");
  if (!node || !text) return;
  if (!restart?.length) {
    node.hidden = true;
    return;
  }
  text.textContent = `以下设置需要重启进程才生效：${restart.join("、")}`;
  node.hidden = false;
}

async function save() {
  if (state.saving) return;
  if (state.dirty.size === 0) {
    toast("没有改动");
    return;
  }
  state.saving = true;
  refreshDirtyUi();

  const updates = {};
  let firstError = null;
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
        if (!firstError) firstError = item.field;
      }
      toast(payload.errors?.length ? "有字段不合法，已标红" : "保存失败", "bad");
      if (firstError) {
        const meta = fieldMeta(firstError);
        if (meta.section) showSection(meta.section.id);
      }
      return;
    }

    state.original = { ...payload.values };
    state.values = { ...payload.values };
    state.dirty.clear();
    state.restart = payload.restart || [];

    if (Object.keys(updates).some((key) => key.startsWith("llm."))) {
      fetch("/api/models", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "save" }),
      }).catch(() => {});
    }

    // 不整页重渲染：人设正文是独立编辑区，重渲染会吞掉没保存的草稿
    showNotice(state.restart);
    toast("已保存并生效");
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
        desc: "plugins/ 下的每个目录就是一张卡。",
        fields: [],
      },
    ];
    state.values = { ...payload.values };
    state.original = { ...payload.values };
    state.dirty.clear();
    state.active =
      validSection(location.hash.slice(1)) || savedSection() || payload.sections[0]?.id;
    const meta = $("foot-meta");
    if (meta) meta.textContent = ` · ${payload.sections.length} 组 ${Object.keys(payload.values).length} 项`;
    render();
  } catch (error) {
    host.textContent = "";
    const card = el("div", "card blank-card", `读取配置失败：${error.message}`);
    host.append(card);
    const foot = $("foot-state");
    if (foot) foot.textContent = "加载失败";
  }
}

/* ---------- 主题 / 顶栏 ---------- */

function paintTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  document.documentElement.style.colorScheme = theme;
  document.documentElement.classList.toggle("dark", theme === "dark");
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.setAttribute("content", theme === "dark" ? "#0a0f1a" : "#f9fcfe");
  const button = $("theme");
  if (button) {
    button.textContent = theme === "dark" ? "◑" : "◐";
    button.title = theme === "dark" ? "切到浅色" : "切到深色";
  }
}

function initTheme() {
  const button = $("theme");
  if (!button) return;
  paintTheme(document.documentElement.getAttribute("data-theme") || "light");
  button.addEventListener("click", (event) => {
    const current = document.documentElement.getAttribute("data-theme") || "light";
    const next = current === "dark" ? "light" : "dark";
    const x = event.clientX ?? window.innerWidth / 2;
    const y = event.clientY ?? 40;
    const radius = Math.hypot(Math.max(x, window.innerWidth - x), Math.max(y, window.innerHeight - y));
    const root = document.documentElement;
    root.style.setProperty("--vt-x", x + "px");
    root.style.setProperty("--vt-y", y + "px");
    root.style.setProperty("--vt-r", radius + "px");

    if (typeof document.startViewTransition !== "function" || reducedMotion()) {
      paintTheme(next);
      return;
    }
    document.startViewTransition(() => paintTheme(next));
    button.animate(
      [{ transform: "rotate(0deg)" }, { transform: "rotate(180deg)" }],
      { duration: 240, easing: "cubic-bezier(.22, 1.15, .36, 1)" },
    );
  });
}

function initTopbar() {
  const bar = $("topbar");
  if (!bar) return;
  let ticking = false;
  const update = () => {
    bar.classList.toggle("is-scrolled", window.scrollY > 8);
    ticking = false;
  };
  window.addEventListener(
    "scroll",
    () => {
      if (ticking) return;
      ticking = true;
      requestAnimationFrame(update);
    },
    { passive: true },
  );
  update();
}

function initFullscreen() {
  const button = $("full");
  if (!button) return;
  button.addEventListener("click", () => {
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    } else if (document.documentElement.requestFullscreen) {
      document.documentElement.requestFullscreen().catch(() => {});
    }
  });
}

/* ---------- 状态 ---------- */

async function pollStatus() {
  try {
    const response = await fetch("/api/status");
    const data = await response.json();
    const dot = $("conn-dot");
    const online = Boolean(data.connected);
    if (dot) dot.className = `dot ${online ? "on" : "off"}`;
    const conn = $("conn");
    if (conn) conn.classList.toggle("off", !online);
    $("conn-text").textContent = online ? `已连接 ${data.self_id ?? ""}`.trim() : "等待 SnowLuma 接入";
    $("stat-line").textContent = `${data.counters?.received ?? 0} 收 / ${data.counters?.replied ?? 0} 回`;
  } catch {
    const dot = $("conn-dot");
    if (dot) dot.className = "dot off";
    $("conn-text").textContent = "状态不可用";
    const conn = $("conn");
    if (conn) conn.classList.add("off");
  }
}

/* ---------- 事件 ---------- */

function initEvents() {
  $("save").addEventListener("click", save);
  $("form").addEventListener("submit", (event) => event.preventDefault());
  $("reload").addEventListener("click", () => {
    load().then(() => {
      showNotice([]);
      toast("已还原为当前生效的配置");
    });
  });

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "s") {
      event.preventDefault();
      save();
    }
    if (event.key === "Escape") closeDialog();
  });

  $("tabs").addEventListener("keydown", (event) => {
    const tabs = [...document.querySelectorAll("#tabs .tabbtn")];
    const current = tabs.findIndex((tab) => tab.getAttribute("aria-current") === "page");
    let next = null;
    if (event.key === "ArrowRight") next = (current + 1) % tabs.length;
    else if (event.key === "ArrowLeft") next = (current - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = tabs.length - 1;
    if (next === null) return;
    event.preventDefault();
    tabs[next].focus();
    showSection(tabs[next].dataset.section);
  });

  window.addEventListener("resize", () => {
    moveInk(false);
    refreshEntityInk();
  });
  window.addEventListener("beforeunload", (event) => {
    if (state.dirty.size > 0) event.preventDefault();
  });
}

/* ---------- 启动 ---------- */

initTheme();
initTopbar();
initFullscreen();
initEvents();
load().then(() => moveInk(false));
pollStatus();
setInterval(pollStatus, 10000);

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") moveInk(false);
});

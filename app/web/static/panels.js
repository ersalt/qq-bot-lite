/* 模型与人设两个特殊面板：都走「平级白卡 + 设置行」，不再套卡片。 */

const MODELS = { items: [], active: "", loading: false };
const PERSONAS = { items: [], active: "" };

/* ---------- 公共：卡片头 ---------- */

function cardHead(title, aside) {
  const head = el("div", "card-head");
  head.append(el("h2", null, title));
  if (aside !== undefined && aside !== null) head.append(el("span", "aside", aside));
  return head;
}

function stackCard(title, aside, body) {
  const card = el("section", "card");
  card.append(cardHead(title, aside));
  const pad = el("div", "card-pad");
  if (body) pad.append(body);
  card.append(pad);
  return card;
}

/* ---------- 模型 ---------- */

function modelPanel(section) {
  const wrap = el("div", "stack");

  const listCard = el("section", "card");
  const listHead = cardHead("模型配置", "");
  const aside = listHead.querySelector(".aside");
  aside.id = "model-aside";
  listCard.append(listHead);

  const pad = el("div", "card-pad");
  const list = el("div", "entities");
  list.id = "model-list";
  pad.append(list);
  pad.append(
    el(
      "p",
      "hint",
      "每张卡是一整套模型参数；切换时会替换接口地址、Key、模型、采样、超时和并发。其他页的设置不受影响。",
    ),
  );

  const addRow = el("div", "add-row");
  const input = document.createElement("input");
  input.className = "input";
  input.id = "model-new";
  input.placeholder = "新配置名，回车即添加";
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      createModel();
    }
  });
  input.addEventListener("input", () => setAddError("model", ""));
  const add = uiButton("新建", { variant: "outline", size: "sm" });
  add.type = "button";
  add.addEventListener("click", createModel);
  addRow.append(input, add, sortBar(() => saveModelOrder()));
  const addErr = el("p", "err add-err");
  addErr.id = "model-add-err";
  addErr.setAttribute("aria-live", "polite");
  pad.append(addRow, addErr);
  listCard.append(pad);
  wrap.append(listCard);
  makeSortable(list);

  const basic = el("div", "rows");
  const advanced = el("div", "rows");
  for (const field of section.fields) {
    const row = buildRow(field, state.values[field.key]);
    (field.group === "advanced" ? advanced : basic).append(row);
  }

  const connect = stackCard("连接", "保存时重连，不用重启", basic);
  connect.append(modelToolRow());
  wrap.append(connect);

  const details = document.createElement("details");
  details.className = "card";
  const summary = document.createElement("summary");
  summary.append("进阶设置");
  summary.append(el("span", "count", "10 项"));
  summary.append(el("span", "note", "采样、思考量、超时、并发，多数情况不用动"));
  summary.append(el("span", "chev", "›"));
  details.append(summary);
  const advPad = el("div", "card-pad");
  advPad.append(advanced);
  details.append(advPad);
  wrap.append(details);

  setTimeout(loadModels, 0);
  return wrap;
}

function modelToolRow() {
  const foot = el("div", "card-foot");
  const fetchBtn = uiButton("获取模型", { variant: "outline", size: "sm" });
  fetchBtn.type = "button";
  const testBtn = uiButton("测试连接", { variant: "outline", size: "sm" });
  testBtn.type = "button";
  const result = el("span", "checkresult", "");
  const picks = el("div", "model-picks");
  picks.id = "model-picks";

  const probe = async (label) => {
    result.className = "checkresult";
    result.textContent = "探测中";
    picks.textContent = "";
    try {
      const response = await fetch("/api/llm/check");
      const data = await response.json();
      const latency = data.latency_ms !== undefined ? ` · ${data.latency_ms}ms` : "";
      result.className = `checkresult ${data.ok ? (data.loaded ? "ok" : "warn") : "bad"}`;
      result.textContent = `${data.message}${latency}`;
      if (data.ok && data.models?.length) {
        for (const name of data.models) {
          const chip = el("button", "model-pick", name);
          chip.type = "button";
          if (name === state.values["llm.model"]) chip.classList.add("on");
          chip.addEventListener("click", () => {
            const input = $("f-llm.model");
            if (!input) return;
            input.value = name;
            input.dispatchEvent(new Event("input", { bubbles: true }));
            for (const other of picks.querySelectorAll(".model-pick")) other.classList.remove("on");
            chip.classList.add("on");
          });
          picks.append(chip);
        }
      }
      if (label === "获取模型" && !data.models?.length) result.textContent = "没拿到模型列表";
    } catch (error) {
      result.className = "checkresult bad";
      result.textContent = `探测失败：${error.message}`;
    }
  };

  fetchBtn.addEventListener("click", () => probe("获取模型"));
  testBtn.addEventListener("click", () => probe("测试连接"));
  foot.append(fetchBtn, testBtn, result, picks);
  return foot;
}

async function loadModels() {
  const list = $("model-list");
  if (!list) return;
  try {
    const response = await fetch("/api/models");
    const data = await response.json();
    MODELS.items = data.items || [];
    MODELS.active = data.active || "";
    paintModels();
  } catch (error) {
    list.textContent = "";
    list.append(el("p", "hint", `读取失败：${error.message}`));
  }
}

function paintModels(from = null) {
  const list = $("model-list");
  if (!list) return;
  const aside = $("model-aside");
  if (aside) aside.textContent = `${MODELS.items.length} 套 · 当前「${MODELS.active || "未选"}」`;
  const frag = document.createDocumentFragment();
  if (!MODELS.items.length) {
    frag.append(el("p", "hint", "还没有保存过的配置。改好参数后点「新建」存一套。"));
    list.replaceChildren(frag);
    placeInk(list);
    return;
  }
  for (const item of MODELS.items) {
    frag.append(
      entityCard({
        title: item.name,
        meta: item.model || "未指定模型",
        active: item.active,
        badge: "使用中",
        onActivate: () => activateModel(item.name),
        onDelete: () => deleteModel(item.name),
      }),
    );
  }
  list.replaceChildren(frag);
  placeInk(list, { animate: Boolean(from), from });
}

let MODEL_SWITCHING = false;

async function activateModel(name) {
  if (name === MODELS.active || MODEL_SWITCHING) return;
  MODEL_SWITCHING = true;
  const list = $("model-list");
  const from = activeEntityRect(list);
  try {
    const response = await fetch("/api/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "activate", name }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      toast(data.message || "切换失败", "bad");
      return;
    }
    MODELS.active = data.active;
    MODELS.items = data.items || MODELS.items;
    if (data.values) {
      for (const [key, value] of Object.entries(data.values)) {
        // /api/models 返回的是裸字段名（base_url/model/...），
        // 页面 state 用的是 llm.base_url/llm.model，必须补前缀才能同步控件。
        const stateKey =
          key.startsWith("llm.") || key.startsWith("context.") ? key : `llm.${key}`;
        if (stateKey in state.values) state.values[stateKey] = value;
      }
      state.original = { ...state.values };
      state.dirty.clear();
    }
    syncSectionFields("llm");
    paintModels(from);
    refreshDirtyUi();
    const fieldsCard = [...document.querySelectorAll("#sec-llm .card")][1];
    if (fieldsCard) flashCards([fieldsCard]);
    toast(`已切换到「${name}」`);
  } catch (error) {
    toast(`切换失败：${error.message}`, "bad");
  } finally {
    MODEL_SWITCHING = false;
  }
}

function setAddError(kind, message) {
  const err = $(`${kind}-add-err`);
  const input = $(`${kind}-new`);
  if (!err || !input) return;
  err.textContent = message || "";
  input.classList.toggle("invalid", Boolean(message));
  input.setAttribute("aria-invalid", message ? "true" : "false");
  if (message) {
    input.focus();
    input.select();
  }
}

async function createModel() {
  const input = $("model-new");
  const name = (input?.value || "").trim();
  setAddError("model", "");
  if (!name) {
    setAddError("model", "先给新配置起个名字");
    return;
  }
  if (MODELS.items.some((item) => item.name === name)) {
    setAddError("model", `同名配置「${name}」已存在`);
    return;
  }
  try {
    const response = await fetch("/api/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "create", name }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      setAddError("model", data.message || "添加失败");
      return;
    }
    if (input) input.value = "";
    MODELS.items = data.items || [];
    paintModels();
    toast(`已添加「${name}」，当前参数存了进去`);
  } catch (error) {
    setAddError("model", `添加失败：${error.message}`);
  }
}

async function deleteModel(name) {
  if (!name || name === MODELS.active) {
    toast("正在用的配置不能删，先切到别的", "bad");
    return;
  }
  try {
    const response = await fetch("/api/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "delete", name }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      toast(data.message || "删除失败", "bad");
      return;
    }
    MODELS.items = data.items || [];
    paintModels();
    toast(`已删除「${name}」`);
  } catch (error) {
    toast(`删除失败：${error.message}`, "bad");
  }
}

/* ---------- 人设 ---------- */

function personaPanel(section) {
  const wrap = el("div", "stack");

  const listCard = el("section", "card");
  const listHead = cardHead("人设列表", "");
  const aside = listHead.querySelector(".aside");
  aside.id = "persona-aside";
  listCard.append(listHead);
  const pad = el("div", "card-pad");
  const cards = el("div", "entities");
  cards.id = "persona-cards";
  pad.append(cards);

  const addRow = el("div", "add-row");
  const input = document.createElement("input");
  input.className = "input";
  input.id = "persona-new";
  input.placeholder = "新人设名，回车即新建";
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      createPersona();
    }
  });
  input.addEventListener("input", () => setAddError("persona", ""));
  const add = uiButton("新建", { variant: "outline", size: "sm" });
  add.type = "button";
  add.addEventListener("click", createPersona);
  addRow.append(input, add, sortBar(() => savePersonaOrder()));
  const addErr = el("p", "err add-err");
  addErr.id = "persona-add-err";
  addErr.setAttribute("aria-live", "polite");
  pad.append(addRow, addErr);
  listCard.append(pad);
  wrap.append(listCard);
  makeSortable(cards);

  const editorCard = el("section", "card");
  const editorHead = cardHead("人设正文", null);
  const path = el("span", "aside mono");
  path.id = "persona-path";
  editorHead.append(path);
  editorCard.append(editorHead);

  const editorPad = el("div", "card-pad");
  const nickRow = el("div", "row");
  const nickHead = el("div", "row-head");
  const nickLabel = document.createElement("label");
  nickLabel.htmlFor = "persona-nickname";
  nickLabel.textContent = "兜底昵称";
  nickHead.append(nickLabel, el("span", "hint", "拿不到群名片时用这个名字，跟随当前人设"));
  const nickCtl = el("div", "row-ctl");
  const nicknameInput = document.createElement("input");
  nicknameInput.className = "input";
  nicknameInput.id = "persona-nickname";
  nicknameInput.placeholder = "例如 小鲸鱼";
  nickCtl.append(nicknameInput);
  nickRow.append(nickHead, nickCtl);
  editorPad.append(nickRow);

  const editor = el("div", "editor");
  const area = document.createElement("textarea");
  area.className = "textarea";
  area.id = "persona-text";
  area.spellcheck = false;
  area.rows = 16;
  area.placeholder = "人设 Markdown 正文";
  const foot = el("div", "editor-foot");
  const counter = el("span", "key");
  counter.id = "persona-count";
  const save = uiButton("保存人设");
  save.type = "button";
  save.id = "persona-save";
  foot.append(counter, save);
  editor.append(foot, area, el("p", "hint", "改完点保存即时生效，不用重启。人设里写死篇幅能直接决定回复长度和思考量。"));
  editorPad.append(editor);
  editorCard.append(editorPad);
  wrap.append(editorCard);

  const updateCounter = () => {
    counter.textContent = `${area.value.length} 字`;
  };
  area.addEventListener("input", updateCounter);
  save.addEventListener("click", () => savePersona(area.value, nicknameInput.value, updateCounter));

  setTimeout(() => loadPersonas(area, path, nicknameInput, updateCounter), 0);
  return wrap;
}

async function loadPersonas(area, path, nicknameInput, updateCounter) {
  try {
    const response = await fetch("/api/personas");
    const data = await response.json();
    PERSONAS.items = data.items || [];
    PERSONAS.active = data.active || "";
    paintPersonas();
  } catch (error) {
    toast(`读取人设列表失败：${error.message}`, "bad");
  }
  try {
    const response = await fetch("/api/persona");
    const data = await response.json();
    if (area) area.value = data.text || "";
    if (path) path.textContent = data.path || "";
    if (nicknameInput) nicknameInput.value = data.nickname || "";
    if (updateCounter) updateCounter();
  } catch (error) {
    toast(`读取人设失败：${error.message}`, "bad");
  }
}

function paintPersonas(from = null) {
  const host = $("persona-cards");
  if (!host) return;
  const aside = $("persona-aside");
  if (aside) aside.textContent = `${PERSONAS.items.length} 个 · 当前「${PERSONAS.active || "未选"}」`;
  const frag = document.createDocumentFragment();
  if (!PERSONAS.items.length) {
    frag.append(el("p", "hint", "还没有人设。新建一个，正文会自动出现在下面。"));
    host.replaceChildren(frag);
    placeInk(host);
    return;
  }
  for (const item of PERSONAS.items) {
    frag.append(
      entityCard({
        title: item.name,
        meta: `${item.chars} 字 · 兜底 ${item.nickname || item.name}`,
        active: item.active,
        badge: "使用中",
        onActivate: () => activatePersona(item.name),
        onDelete: () => deletePersona(item.name),
      }),
    );
  }
  host.replaceChildren(frag);
  placeInk(host, { animate: Boolean(from), from });
}

let PERSONA_SWITCHING = false;

async function activatePersona(name) {
  if (name === PERSONAS.active || PERSONA_SWITCHING) return;
  PERSONA_SWITCHING = true;
  const host = $("persona-cards");
  const from = activeEntityRect(host);
  try {
    const response = await fetch("/api/personas/active", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      toast(data.message || "切换失败", "bad");
      return;
    }
    PERSONAS.items = data.items || [];
    PERSONAS.active = data.active;
    const area = $("persona-text");
    if (area && typeof data.text === "string") area.value = data.text;
    const nicknameInput = $("persona-nickname");
    if (nicknameInput && typeof data.nickname === "string") nicknameInput.value = data.nickname;
    const counter = $("persona-count");
    if (counter && area) counter.textContent = `${area.value.length} 字`;
    paintPersonas(from);
    const editorCard = document.querySelector("#sec-persona .editor")?.closest(".card");
    if (editorCard) flashCards([editorCard]);
    toast(`已切换到「${name}」`);
  } catch (error) {
    toast(`切换失败：${error.message}`, "bad");
  } finally {
    PERSONA_SWITCHING = false;
  }
}

async function createPersona() {
  const input = $("persona-new");
  const name = (input?.value || "").trim();
  setAddError("persona", "");
  if (!name) {
    setAddError("persona", "先给新人设起个名字");
    return;
  }
  if (PERSONAS.items.some((item) => item.name === name)) {
    setAddError("persona", `「${name}」已经存在`);
    return;
  }
  try {
    const response = await fetch("/api/personas/create", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      setAddError("persona", data.message || "新建失败");
      return;
    }
    if (input) input.value = "";
    PERSONAS.items = data.items || [];
    paintPersonas();
    await activatePersona(data.name);
    toast(`已新建「${data.name}」`);
  } catch (error) {
    setAddError("persona", `新建失败：${error.message}`);
  }
}

async function deletePersona(name) {
  if (!name || name === PERSONAS.active) {
    toast("正在用的人设不能删，先切到别的", "bad");
    return;
  }
  try {
    const response = await fetch(`/api/personas/${encodeURIComponent(name)}`, { method: "DELETE" });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      toast(data.message || "删除失败", "bad");
      return;
    }
    PERSONAS.items = data.items || [];
    paintPersonas();
    toast(`已删除「${name}」`);
  } catch (error) {
    toast(`删除失败：${error.message}`, "bad");
  }
}

async function savePersona(text, nickname, updateCounter) {
  try {
    const response = await fetch("/api/persona", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      toast(data.message || "保存失败", "bad");
      return;
    }
    const nickResponse = await fetch("/api/personas/nickname", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nickname }),
    });
    const nickData = await nickResponse.json();
    if (!nickResponse.ok || !nickData.ok) {
      toast(nickData.message || "兜底昵称保存失败", "bad");
      return;
    }
    PERSONAS.items = nickData.items || PERSONAS.items;
    paintPersonas();
    if (updateCounter) updateCounter();
    toast(`人设已保存（${data.chars} 字）`);
  } catch (error) {
    toast(`保存失败：${error.message}`, "bad");
  }
}

/* ---------- 实体卡与排序 ---------- */

/* 选中框：一个绝对定位的方框，在卡片之间滑动。
   卡片本身保持透明底，方框在下面（z-index 0），文字在上面。 */
function activeEntityRect(container) {
  const card = container?.querySelector('.entity[aria-current="true"]');
  if (!card) return null;
  return {
    x: card.offsetLeft,
    y: card.offsetTop,
    w: card.offsetWidth,
    h: card.offsetHeight,
  };
}

function placeInk(container, options = {}) {
  if (!container) return;
  let ink = container.querySelector(".entity-ink");
  if (!ink) {
    ink = el("span", "entity-ink");
    ink.setAttribute("aria-hidden", "true");
    container.prepend(ink);
  }
  const to = activeEntityRect(container);
  if (!to || !to.w) {
    ink.hidden = true;
    return;
  }
  ink.hidden = false;

  const apply = (r) => {
    ink.style.transform = `translate(${r.x}px, ${r.y}px)`;
    ink.style.width = `${r.w}px`;
    ink.style.height = `${r.h}px`;
  };
  const reduce =
    window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const from = !reduce && options.from && options.from.w ? options.from : null;

  ink.style.transition = "none";
  apply(from || to);
  void ink.offsetWidth;
  if (from && options.animate) {
    ink.style.transition = "";
    requestAnimationFrame(() => apply(to));
  } else {
    apply(to);
    void ink.offsetWidth;
    ink.style.transition = "";
  }
}

function refreshEntityInk() {
  for (const container of document.querySelectorAll(".entities")) {
    placeInk(container);
  }
}

function entityCard({ title, meta, active, badge, onActivate, onDelete }) {
  const card = el("div", "entity");
  card.setAttribute("role", "button");
  card.tabIndex = 0;
  card.draggable = true;
  card.dataset.name = title;
  if (active) card.setAttribute("aria-current", "true");

  card.append(el("span", "entity-name", title));
  card.append(el("span", "entity-meta", meta));
  if (active && badge) card.append(el("span", "tag", badge));

  const activate = () => {
    if (card.closest(".entities")?.classList.contains("sorting")) return;
    onActivate();
  };
  card.addEventListener("click", activate);
  card.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activate();
    }
  });

  if (onDelete && !active) {
    const del = el("span", "entity-del", "×");
    del.setAttribute("role", "button");
    del.setAttribute("title", "删除");
    del.addEventListener("click", (event) => {
      event.stopPropagation();
      askDelete(card, onDelete);
    });
    card.append(del);
  }

  const moves = el("span", "entity-moves");
  const left = el("span", "entity-move", "‹");
  left.setAttribute("role", "button");
  left.title = "往前挪";
  left.addEventListener("click", (event) => {
    event.stopPropagation();
    const previous = card.previousElementSibling;
    if (previous) card.parentElement.insertBefore(card, previous);
  });
  const right = el("span", "entity-move", "›");
  right.setAttribute("role", "button");
  right.title = "往后挪";
  right.addEventListener("click", (event) => {
    event.stopPropagation();
    const next = card.nextElementSibling;
    if (next) card.parentElement.insertBefore(next, card);
  });
  moves.append(left, right);
  card.append(moves);
  return card;
}

function sortBar(onDone) {
  const button = uiButton("排序", { variant: "outline", size: "sm" });
  button.type = "button";
  button.dataset.role = "sort";
  let sorting = false;
  button.addEventListener("click", () => {
    sorting = !sorting;
    const scope = button.closest(".stack") || document;
    const cards = scope.querySelector(".entities");
    if (!cards) return;
    cards.classList.toggle("sorting", sorting);
    button.textContent = sorting ? "完成" : "排序";
    if (sorting) delete button.dataset.variant;
    else button.dataset.variant = "outline";
    if (!sorting) {
      onDone();
      refreshEntityInk();
    }
  });
  return button;
}

function makeSortable(container) {
  let dragging = null;
  container.addEventListener("dragstart", (event) => {
    if (!container.classList.contains("sorting")) {
      event.preventDefault();
      return;
    }
    const card = event.target.closest(".entity");
    if (!card) return;
    dragging = card;
    card.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.dataset.name || "");
  });
  container.addEventListener("dragover", (event) => {
    if (!dragging) return;
    event.preventDefault();
    const target = event.target.closest(".entity");
    if (!target || target === dragging) return;
    const rect = target.getBoundingClientRect();
    const after = event.clientX - rect.left > rect.width / 2;
    container.insertBefore(dragging, after ? target.nextSibling : target);
  });
  container.addEventListener("drop", (event) => event.preventDefault());
  container.addEventListener("dragend", () => {
    if (!dragging) return;
    dragging.classList.remove("dragging");
    dragging = null;
  });
}

function cardOrder(container) {
  return Array.from(container.querySelectorAll(".entity")).map((card) => card.dataset.name);
}

function askDelete(card, onConfirm) {
  if (card.classList.contains("confirming")) return;
  card.classList.add("confirming");
  const box = el("div", "entity-confirm");
  const text = el("span", "entity-confirm-text", "确定删除？");
  const yes = uiButton("删除", { variant: "destructive", size: "sm" });
  yes.type = "button";
  const no = uiButton("取消", { variant: "outline", size: "sm" });
  no.type = "button";
  box.append(text, yes, no);
  card.append(box);
  yes.addEventListener("click", (event) => {
    event.stopPropagation();
    onConfirm();
  });
  no.addEventListener("click", (event) => {
    event.stopPropagation();
    box.remove();
    card.classList.remove("confirming");
  });
}

async function saveModelOrder() {
  const list = $("model-list");
  if (!list) return;
  const names = cardOrder(list);
  try {
    const response = await fetch("/api/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action: "reorder", names }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      toast(data.message || "排序保存失败", "bad");
      return;
    }
    MODELS.items = data.items || MODELS.items;
    toast("顺序已保存");
  } catch (error) {
    toast(`排序保存失败：${error.message}`, "bad");
  }
}

async function savePersonaOrder() {
  const host = $("persona-cards");
  if (!host) return;
  const names = cardOrder(host);
  try {
    const response = await fetch("/api/personas/order", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ names }),
    });
    const data = await response.json();
    if (!response.ok || !data.ok) {
      toast(data.message || "排序保存失败", "bad");
      return;
    }
    PERSONAS.items = data.items || PERSONAS.items;
    toast("顺序已保存");
  } catch (error) {
    toast(`排序保存失败：${error.message}`, "bad");
  }
}

/* 模型与人设两个自定义面板。
   它们不走通用的“字段网格”，而是各自有左侧列表 / 上方卡片。 */

const MODELS = { items: [], active: "", loading: false };
const PERSONAS = { items: [], active: "" };

/* ---------- 模型 ---------- */

function modelPanel(section) {
  const wrap = el("div", "entity-panel");

  const list = el("div", "entity-cards");
  list.id = "model-list";
  wrap.append(list);

  const addRow = el("div", "entity-add");
  const input = document.createElement("input");
  input.type = "text";
  input.id = "model-new";
  input.placeholder = "新配置名，回车即添加";
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      createModel();
    }
  });
  const add = el("button", "btn btn-sm", "新建");
  add.type = "button";
  add.addEventListener("click", createModel);
  addRow.append(input, add, sortBar(() => saveModelOrder()));
  wrap.append(addRow);
  makeSortable(list);

  const main = el("div", "entity-body");
  const basic = el("div", "grid");
  const advanced = el("div", "grid");
  for (const field of section.fields) {
    const node = buildField(field, state.values[field.key]);
    (field.group === "advanced" ? advanced : basic).append(node);
  }
  main.append(basic);
  main.append(modelToolRow());

  const divider = el("div", "divider");
  divider.append(el("span", null, "进阶设置"));
  main.append(divider);
  main.append(advanced);
  wrap.append(main);

  setTimeout(loadModels, 0);
  return wrap;
}

function modelToolRow() {
  const row = el("div", "checkrow");
  const fetchBtn = el("button", "btn btn-sm", "获取模型");
  fetchBtn.type = "button";
  const testBtn = el("button", "btn btn-sm", "测试连接");
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
  row.append(fetchBtn, testBtn, result);
  const box = el("div", "model-tools");
  box.append(row, picks);
  return box;
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


/* ---------- 排序 ---------- */

function sortBar(onDone) {
  const bar = el("div", "entity-sortbar");
  const button = el("button", "btn btn-sm", "排序");
  button.type = "button";
  button.dataset.role = "sort";
  let sorting = false;
  button.addEventListener("click", () => {
    sorting = !sorting;
    const scope = button.closest(".entity-panel");
    const cards = scope?.querySelector(".entity-cards");
    if (!cards) return;
    cards.classList.toggle("sorting", sorting);
    button.textContent = sorting ? "完成" : "排序";
    button.classList.toggle("btn-primary", sorting);
    if (!sorting) onDone();
  });
  bar.append(button);
  return bar;
}

function makeSortable(container) {
  let dragging = null;
  container.addEventListener("dragstart", (event) => {
    if (!container.classList.contains("sorting")) {
      event.preventDefault();
      return;
    }
    const card = event.target.closest(".entity-card");
    if (!card) return;
    dragging = card;
    card.classList.add("dragging");
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData("text/plain", card.dataset.name || "");
  });
  container.addEventListener("dragover", (event) => {
    if (!dragging) return;
    event.preventDefault();
    const target = event.target.closest(".entity-card");
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
  return Array.from(container.querySelectorAll(".entity-card")).map((card) => card.dataset.name);
}
function entityCard({ title, meta, active, badge, onActivate, onDelete }) {
  // 用 div 而不是 button：button 在 Chrome 里拖拽行为不稳定，
  // 而且 draggable 属性默认是关的，dragstart 根本不会触发。
  const card = el("div", "entity-card");
  card.setAttribute("role", "button");
  card.tabIndex = 0;
  card.draggable = true;
  card.dataset.name = title;
  if (active) card.classList.add("active");
  card.append(el("span", "entity-name", title));
  card.append(el("span", "entity-meta", meta));
  if (active && badge) card.append(el("span", "entity-badge", badge));

  const activate = () => {
    if (card.closest(".entity-cards")?.classList.contains("sorting")) return;
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
      onDelete();
    });
    card.append(del);
  }

  // 排序模式下的兜底：拖拽在某些浏览器/触控板上不灵，箭头总能点。
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

function paintModels() {
  const list = $("model-list");
  if (!list) return;
  list.textContent = "";
  if (!MODELS.items.length) {
    list.append(el("p", "hint", "还没有保存过的配置。改好参数后点「新建」存一套。"));
    return;
  }
  for (const item of MODELS.items) {
    list.append(
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
}

async function activateModel(name) {
  if (name === MODELS.active) return;
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
        if (key in state.values) state.values[key] = value;
      }
      state.original = { ...state.values };
      state.dirty.clear();
    }
    render();
    refreshDirtyUi();
    toast(`已切换到「${name}」`);
  } catch (error) {
    toast(`切换失败：${error.message}`, "bad");
  }
}

async function createModel() {
  const input = $("model-new");
  const name = (input?.value || "").trim();
  if (!name) {
    toast("先给新配置起个名字", "bad");
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
      toast(data.message || "添加失败", "bad");
      return;
    }
    if (input) input.value = "";
    MODELS.items = data.items || [];
    paintModels();
    toast(`已添加「${name}」，当前参数存了进去`);
  } catch (error) {
    toast(`添加失败：${error.message}`, "bad");
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

function personaPanel() {
  const wrap = el("div", "entity-panel");
  const cards = el("div", "entity-cards");
  cards.id = "persona-cards";
  wrap.append(cards);

  const addRow = el("div", "entity-add");
  const input = document.createElement("input");
  input.type = "text";
  input.id = "persona-new";
  input.placeholder = "新人设名，回车即新建";
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      createPersona();
    }
  });
  const add = el("button", "btn btn-sm", "新建");
  add.type = "button";
  add.addEventListener("click", createPersona);
  addRow.append(input, add, sortBar(() => savePersonaOrder()));
  wrap.append(addRow);
  makeSortable(cards);

  const editor = el("div", "editor");
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

  const hint = el("p", "hint", "改完点保存即时生效，不用重启。人设里写死篇幅能直接决定回复长度和思考量。");
  editor.append(head, area, hint);
  wrap.append(editor);

  const updateCounter = () => {
    counter.textContent = `${area.value.length} 字`;
  };
  area.addEventListener("input", updateCounter);
  save.addEventListener("click", () => savePersona(area.value, updateCounter));

  setTimeout(() => loadPersonas(area, path, updateCounter), 0);
  return wrap;
}

async function loadPersonas(area, path, updateCounter) {
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
    if (updateCounter) updateCounter();
  } catch (error) {
    toast(`读取人设失败：${error.message}`, "bad");
  }
}

function paintPersonas() {
  const host = $("persona-cards");
  if (!host) return;
  host.textContent = "";
  for (const item of PERSONAS.items) {
    host.append(
      entityCard({
        title: item.name,
        meta: `${item.chars} 字`,
        active: item.active,
        badge: "使用中",
        onActivate: () => activatePersona(item.name),
        onDelete: () => deletePersona(item.name),
      }),
    );
  }
}

async function activatePersona(name) {
  if (name === PERSONAS.active) return;
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
    paintPersonas();
    toast(`已切换到「${name}」`);
  } catch (error) {
    toast(`切换失败：${error.message}`, "bad");
  }
}

async function createPersona() {
  const input = $("persona-new");
  const name = (input?.value || "").trim();
  if (!name) {
    toast("先给新人设起个名字", "bad");
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
      toast(data.message || "新建失败", "bad");
      return;
    }
    if (input) input.value = "";
    PERSONAS.items = data.items || [];
    paintPersonas();
    await activatePersona(data.name);
    toast(`已新建「${data.name}」`);
  } catch (error) {
    toast(`新建失败：${error.message}`, "bad");
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

async function savePersona(text, updateCounter) {
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
    if (updateCounter) updateCounter();
    toast(`人设已保存（${data.chars} 字）`);
  } catch (error) {
    toast(`保存失败：${error.message}`, "bad");
  }
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

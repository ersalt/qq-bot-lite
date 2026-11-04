/* 前端小工具：统一按 Basecoat 的写法建按钮/控件，别在各处手拼 class。
   Basecoat 的按钮变体用 data-variant，尺寸用 data-size。 */

function uiEl(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

/**
 * 按钮。
 * @param {string} label
 * @param {{variant?: "default"|"outline"|"ghost"|"destructive"|"secondary"|"link", size?: "sm"|"icon", href?: string}} [options]
 */
function uiButton(label, options) {
  const opts = options || {};
  const node = opts.href ? uiEl("a", "btn", label) : uiEl("button", "btn", label);
  node.type = "button";
  if (opts.href) node.href = opts.href;
  if (opts.variant && opts.variant !== "default") node.dataset.variant = opts.variant;
  if (opts.size) node.dataset.size = opts.size;
  return node;
}

/** 输入类控件：text / number / password / select / textarea 都走 Basecoat 的类名。 */
function uiInput(kind, value, attrs) {
  const tag = kind === "select" ? "select" : kind === "textarea" ? "textarea" : "input";
  const node = document.createElement(tag);
  node.className = kind === "select" ? "select" : kind === "textarea" ? "textarea" : "input";
  if (tag === "input") node.type = kind === "number" ? "number" : kind === "password" ? "password" : "text";
  Object.assign(node, attrs || {});
  if (value !== undefined && value !== null) node.value = String(value);
  return node;
}

/**
 * 自绘下拉框，视觉/键盘行为参考 SnowLuma 调试台的下拉组件。
 * 返回一个 button；它的 .value 就是当前选项值，并会派发 input/change 事件。
 *
 * @param {{
 *   id?: string,
 *   label?: string,
 *   value?: string|number,
 *   items?: Array<string|{value: any, label?: string, hint?: string, disabled?: boolean}>,
 *   placeholder?: string,
 *   disabled?: boolean,
 *   onChange?: (value: string) => void
 * }} [options]
 */
function uiSelect(options) {
  const config = options || {};
  const items = (config.items || []).map((item) => {
    if (item && typeof item === "object") {
      return {
        value: item.value === undefined || item.value === null ? "" : String(item.value),
        label: item.label === undefined || item.label === null ? String(item.value ?? "") : String(item.label),
        hint: item.hint ? String(item.hint) : "",
        disabled: Boolean(item.disabled),
      };
    }
    return { value: String(item), label: String(item), hint: "", disabled: false };
  });

  let current = config.value === undefined || config.value === null ? "" : String(config.value);
  if (current && !items.some((item) => item.value === current)) {
    items.push({ value: current, label: current, hint: "", disabled: false });
  }

  const id = config.id || `ui-select-${++uiSelectSeq}`;
  const labelText = config.label || "";
  const button = document.createElement("button");
  button.type = "button";
  button.className = "ui-select";
  button.id = id;
  button.value = current;
  button.disabled = Boolean(config.disabled);
  button.setAttribute("aria-haspopup", "listbox");
  button.setAttribute("aria-expanded", "false");

  const valueNode = document.createElement("span");
  valueNode.className = "ui-select-value";
  button.append(valueNode, uiSelectIcon("0 0 12 12", "M3 4.75 6 7.75 9 4.75", "ui-select-chevron", 1.4));

  const menu = document.createElement("div");
  menu.className = "ui-select-menu";
  menu.hidden = true;
  const list = document.createElement("ul");
  list.className = "ui-select-list";
  list.id = `${id}-list`;
  list.setAttribute("role", "listbox");
  list.setAttribute("aria-label", labelText || "选项");
  list.tabIndex = -1;
  menu.append(list);

  const activeBg = document.createElement("span");
  activeBg.className = "ui-select-active";
  activeBg.setAttribute("aria-hidden", "true");
  list.append(activeBg);

  const optionNodes = [];
  items.forEach((item, index) => {
    const node = document.createElement("li");
    node.className = "ui-select-option";
    node.id = `${id}-opt-${index}`;
    node.dataset.index = String(index);
    node.setAttribute("role", "option");
    node.setAttribute("aria-selected", String(item.value === current));
    if (item.disabled) node.setAttribute("aria-disabled", "true");

    const label = document.createElement("span");
    label.className = "ui-select-option-label";
    label.textContent = item.label;
    node.append(label);

    if (item.hint) {
      const hint = document.createElement("span");
      hint.className = "ui-select-option-hint";
      hint.textContent = item.hint;
      node.append(hint);
    }
    node.append(uiSelectIcon("0 0 14 14", "M3 7.4 5.8 10.2 11 4.4", "ui-select-check", 1.5));
    list.append(node);
    optionNodes.push(node);
  });

  if (!items.length) {
    const empty = document.createElement("li");
    empty.className = "ui-select-empty";
    empty.setAttribute("role", "presentation");
    empty.textContent = "没有可选项";
    list.append(empty);
  }

  let open = false;
  let activeIndex = -1;
  let hostDialog = null;
  let typeahead = "";
  let typeaheadTimer = 0;

  const closeSelf = () => closeMenu(false);

  function syncSelected() {
    optionNodes.forEach((node, index) => {
      node.setAttribute("aria-selected", String(items[index].value === current));
    });
  }

  function syncLabel() {
    const selected = items.find((item) => item.value === current);
    const text = selected ? selected.label : current || config.placeholder || "请选择";
    valueNode.textContent = text;
    valueNode.classList.toggle("is-placeholder", !selected);
    button.setAttribute("aria-label", labelText ? `${labelText}：${text}` : text);
  }

  function firstEnabled() {
    return items.findIndex((item) => !item.disabled);
  }

  function lastEnabled() {
    for (let index = items.length - 1; index >= 0; index -= 1) {
      if (!items[index].disabled) return index;
    }
    return -1;
  }

  function step(index, delta) {
    if (!items.length) return -1;
    let next = index;
    for (let guard = 0; guard < items.length; guard += 1) {
      next = (next + delta + items.length) % items.length;
      if (!items[next].disabled) return next;
    }
    return -1;
  }

  function setActive(index, scroll) {
    activeIndex = index;
    const node = optionNodes[index];
    if (!node) {
      activeBg.style.opacity = "0";
      list.removeAttribute("aria-activedescendant");
      return;
    }
    activeBg.style.opacity = "1";
    activeBg.style.transform = `translateY(${index * 32}px)`;
    optionNodes.forEach((option, at) => option.classList.toggle("is-active", at === index));
    list.setAttribute("aria-activedescendant", node.id);
    if (scroll) node.scrollIntoView({ block: "nearest" });
  }

  function setValue(next) {
    current = next === undefined || next === null ? "" : String(next);
    button.value = current;
    syncLabel();
    syncSelected();
  }

  function choose(index) {
    const item = items[index];
    if (!item || item.disabled) return;
    setValue(item.value);
    closeMenu(true);
    if (typeof config.onChange === "function") config.onChange(current);
    button.dispatchEvent(new Event("input", { bubbles: true }));
    button.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function onDocPointerDown(event) {
    if (menu.contains(event.target) || button.contains(event.target)) return;
    closeMenu(false);
  }

  function onScroll(event) {
    if (menu.contains(event.target)) return;
    positionMenu();
  }

  function onResize() {
    positionMenu();
  }

  function onDialogCancel(event) {
    event.preventDefault();
    closeMenu(true);
  }

  function onDialogClose() {
    closeMenu(false);
  }

  function positionMenu() {
    if (!open) return;
    const rect = button.getBoundingClientRect();
    const gap = 6;
    const margin = 8;
    if (rect.bottom < 0 || rect.top > window.innerHeight) {
      closeMenu(false);
      return;
    }
    menu.style.left = `${rect.left}px`;
    menu.style.top = `${rect.bottom + gap}px`;
    menu.style.minWidth = `${rect.width}px`;
    menu.style.maxWidth = `${Math.max(160, window.innerWidth - margin * 2)}px`;

    const width = menu.offsetWidth;
    const height = menu.offsetHeight;
    let left = Math.min(rect.left, window.innerWidth - width - margin);
    left = Math.max(margin, left);
    let top = rect.bottom + gap;
    let placement = "bottom";
    if (top + height > window.innerHeight - margin && rect.top - gap - height >= margin) {
      top = rect.top - gap - height;
      placement = "top";
    }
    top = Math.max(margin, Math.min(top, window.innerHeight - height - margin));
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    menu.dataset.placement = placement;
  }

  function openMenu(direction) {
    if (open || button.disabled) return;
    if (uiSelectOpen) uiSelectOpen();
    open = true;
    hostDialog = button.closest("dialog[open]");
    const host = hostDialog || document.body;
    host.append(menu);
    if (hostDialog) {
      hostDialog.classList.add("select-open");
      hostDialog.addEventListener("cancel", onDialogCancel);
      hostDialog.addEventListener("close", onDialogClose, { once: true });
    }

    menu.hidden = false;
    button.setAttribute("aria-expanded", "true");
    button.setAttribute("aria-controls", list.id);
    uiSelectOpen = closeSelf;
    positionMenu();

    const selected = items.findIndex((item) => item.value === current);
    let index = selected >= 0 && !items[selected].disabled ? selected : firstEnabled();
    if (direction === "last") index = lastEnabled();
    if (!items.length) index = -1;
    setActive(index, false);
    list.focus({ preventScroll: true });

    document.addEventListener("pointerdown", onDocPointerDown, true);
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onResize);
  }

  function closeMenu(returnFocus) {
    if (!open) return;
    open = false;
    button.setAttribute("aria-expanded", "false");
    button.removeAttribute("aria-controls");
    menu.hidden = true;
    menu.remove();
    if (hostDialog) {
      hostDialog.classList.remove("select-open");
      hostDialog.removeEventListener("cancel", onDialogCancel);
      hostDialog.removeEventListener("close", onDialogClose);
      hostDialog = null;
    }
    document.removeEventListener("pointerdown", onDocPointerDown, true);
    window.removeEventListener("scroll", onScroll, true);
    window.removeEventListener("resize", onResize);
    window.clearTimeout(typeaheadTimer);
    if (uiSelectOpen === closeSelf) uiSelectOpen = null;
    if (returnFocus) button.focus({ preventScroll: true });
  }

  function onListKeyDown(event) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      setActive(step(activeIndex, event.key === "ArrowDown" ? 1 : -1), true);
      return;
    }
    if (event.key === "Home" || event.key === "End") {
      event.preventDefault();
      setActive(event.key === "Home" ? firstEnabled() : lastEnabled(), true);
      return;
    }
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      choose(activeIndex);
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeMenu(true);
      return;
    }
    if (event.key === "Tab") {
      closeMenu(true);
      return;
    }
    if (event.key.length === 1 && !event.metaKey && !event.ctrlKey && !event.altKey) {
      event.preventDefault();
      typeahead += event.key.toLowerCase();
      window.clearTimeout(typeaheadTimer);
      typeaheadTimer = window.setTimeout(() => {
        typeahead = "";
      }, 600);
      const start = typeahead.length > 1 ? activeIndex : 0;
      for (let offset = 0; offset < items.length; offset += 1) {
        const index = (start + offset + items.length) % items.length;
        if (!items[index].disabled && items[index].label.toLowerCase().startsWith(typeahead)) {
          setActive(index, true);
          break;
        }
      }
    }
  }

  list.addEventListener("keydown", onListKeyDown);
  list.addEventListener("pointermove", (event) => {
    const node = event.target.closest(".ui-select-option");
    if (!node || !list.contains(node)) return;
    const index = Number(node.dataset.index);
    if (items[index] && !items[index].disabled && index !== activeIndex) setActive(index, false);
  });
  list.addEventListener("click", (event) => {
    const node = event.target.closest(".ui-select-option");
    if (!node || !list.contains(node)) return;
    choose(Number(node.dataset.index));
  });

  button.addEventListener("click", () => {
    if (open) closeMenu(true);
    else openMenu("first");
  });
  button.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openMenu("first");
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      openMenu("last");
    }
  });

  syncLabel();
  syncSelected();
  return button;
}

let uiSelectSeq = 0;
let uiSelectOpen = null;

function uiSelectIcon(viewBox, pathData, className, width) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", viewBox);
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("class", className);
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", pathData);
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", String(width));
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  svg.append(path);
  return svg;
}

/** Basecoat 的开关：input[role=switch] + 标签。返回 { wrap, input }。 */
function uiSwitch(id, checked, labelText) {
  const wrap = uiEl("div", "field");
  wrap.dataset.orientation = "horizontal";
  const input = document.createElement("input");
  input.type = "checkbox";
  input.role = "switch";
  input.className = "input";
  input.id = id;
  input.checked = Boolean(checked);
  const label = uiEl("label", null, labelText || "");
  label.htmlFor = id;
  wrap.append(input, label);
  return { wrap, input };
}

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

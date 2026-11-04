# UI 规范 · qq-bot-lite 设置面板

这份是前端唯一的视觉契约。新窗口接手时先读 `AGENTS.md`，再读这里；改界面前先改这里，
或者改完把这里同步改掉。**口头约定不算数，代码里的令牌和这份文档对不上，以这份文档为准并修代码。**

历史原型已经落到 `app/web/`，不再维护单独的 mock；真源是代码 + 本文。

---

## 1. 定位

本地单人用的运维控制台，不是 landing page，也不是仪表盘。

- **安静**：低饱和、浅蓝白底、白卡，只有一个强调色。
- **可扫读**：一屏能看清“这个分组有哪些项、哪些要重启、哪些是进阶”。
- **轻**：没有构建链，没有外部字体/图标请求，断网可用。
- **动效是调料**：只做状态切换的因果，不做装饰动画。

三个旋钮（参考 `design-taste` 的读法，非 landing 基线）：
`DESIGN_VARIANCE 3 / MOTION_INTENSITY 4 / VISUAL_DENSITY 5`。

---

## 2. 文件分工

| 文件 | 职责 | 不许做的事 |
|---|---|---|
| `app/web/page.html` | 外壳：顶栏、页头、页签容器、面板容器、页脚、toast | 不写任何业务字段 |
| `app/web/static/theme.css` | **唯一的令牌源**：颜色、圆角、阴影、字体、动效曲线 | 不写布局和组件 |
| `app/web/static/style.css` | 骨架 + 组件外观（顶栏/页头/页签/卡/行/弹窗） | 不定义颜色字面量，只用令牌 |
| `app/web/static/app.js` | 渲染通用分组、页签、脏状态、保存、主题 | 不拼业务面板 |
| `app/web/static/panels.js` | 模型、人设两个特殊面板 | 不碰页面骨架 |
| `app/web/static/plugins.js` | 插件卡片、设置/文档/删除弹窗 | 不碰页面骨架 |
| `app/web/static/ui.js` | `uiButton` / `uiInput` / `uiSwitch` 小工具 | 不写样式 |

没有构建链。**不许**引 React/Vue/Tailwind 运行时、CDN、Google Fonts、图标库。
组件一律用本地 `basecoat.css` 的类名和 `data-variant`；默认态**不要**写 `data-variant="default"`。

---

## 3. 骨架

```
┌────────────────────────────────────────────────────────────┐
│ topbar 56px  brand / 设置          已连接   ◐  ⛶           │
├────────────────────────────────────────────────────────────┤
│ page  max-width 1200, padding 0 24                         │
│                                                            │
│  服务与访问                                      保存 放弃  │  head: 30px 标题 + 13.5 说明
│  反向 WebSocket 监听地址……                                  │
│                                                            │
│  服务与访问  OneBot 连接  模型  …  插件 1                  │  tabbar: 38px，底部 1px
│  ─────                                                     │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ 服务                              改动即时写回……    │  │  card: 白底 16px 圆角
│  │                                                      │  │
│  │ 监听地址  server.host  [需重启]         [127.0.0.1]  │  │  row: 说明左 / 控件右
│  │ 一般只在本机用，保持 127.0.0.1 就行。                 │  │
│  ├──────────────────────────────────────────────────────┤  │  1px 发丝线
│  │ 监听端口  server.port  [需重启]         [8790]       │  │
│  └──────────────────────────────────────────────────────┘  │
│                                                            │
│  配置来源 config/config.toml                    已同步      │  foot: 11.5px
└────────────────────────────────────────────────────────────┘
```

硬规则：

- **没有侧栏，也没有顶部导航条。** 9 个分组就是页内页签。
- 顶栏只放全局状态：品牌、连接、主题、全屏。**不放分组导航。**
- 内容最大宽 1200，内边距 24 → 实宽 1152；桌面左右留白对称。
- 卡片不嵌套。一页 = 页头 + 若干张平级卡；卡内用行和发丝线分组。
- 页脚永远在最后一张卡下面，不浮在视口底。
- **`html` 和 `body` 只准 `overflow-x: clip`，不准 `hidden`。** `body` 一旦有
  `overflow-x: hidden`，就变成没有滚动范围的滚动容器，滚轮事件被它吃掉，整页滚不动
  （进阶设置展开后尤其明显）。

---

## 4. 令牌

全部定义在 `theme.css` 的 `:root` 和 `:root[data-theme="dark"]`。
`style.css` 里出现 `#hex` / `oklch(` 视为 bug。

### 4.1 颜色（浅色 / 深色）

| 令牌 | 浅色 | 深色 | 用途 |
|---|---|---|---|
| `--canvas` | `#f9fcfe` | `oklch(16% .016 250)` | 页面底、顶栏底 |
| `--card` | `#ffffff` | `oklch(20% .018 250)` | 卡片、输入框 |
| `--text` | `#26313c` | `oklch(94% .008 240)` | 正文 |
| `--muted` | `#5e6e7c` | `oklch(70% .014 240)` | 说明、次要信息 |
| `--key` | `#6f7f8c` | `oklch(66% .014 240)` | 字段 key（等宽小字） |
| `--border` | `#e9eff3` | `oklch(28% .016 250)` | 卡边、页签底线 |
| `--hairline` | `#f1f5f7` | `oklch(26% .014 250)` | 行分隔线 |
| `--primary` | `#00a9e6` | `oklch(72% .13 232)` | 主按钮底、页签下划线 |
| `--primary-ink` | `#006f9e` | `oklch(82% .1 232)` | 浅蓝底上的文字/图标 |
| `--primary-soft` | `#ebf7fd` | `oklch(26% .045 235)` | 选中页签底、代码 chip |
| `--success` / `--success-ink` / `--success-soft` | `#2cbc82` / `#1d7a52` / `#e9f8f1` | `oklch(70% .13 162)` / `oklch(82% .1 162)` / `oklch(26% .04 162)` | 已连接、探测成功 |
| `--warning-ink` / `--warning-soft` | `#9a5a12` / `#fdf4e5` | `oklch(85% .1 80)` / `oklch(28% .04 80)` | 重启提示条 |
| `--danger` | `#d64545` | `oklch(62% .19 27)` | 加载失败、删除 |

规则：

- **一个强调色。** 浅蓝只出现在：主按钮、选中页签、焦点环、链接、需重启提示。
- 成功/警告/危险是语义色，不参与“装饰”。
- 深色不是浅色反转：底更黑、卡略亮、主色反而更亮（`72%`），按钮用深色字。
- 完整 `--*` 与 Basecoat 的 `--color-*` 桥接必须**全给**（包括 `--card-foreground`、
  `--muted-foreground`、`--primary-foreground`、`--secondary`、`--popover`、`--ring`）。
  漏一个，Basecoat 就退回它自己的浅色默认值，深色下直接翻车。

### 4.2 圆角 / 间距 / 阴影 / 字体

| 项 | 值 |
|---|---|
| 卡片圆角 | 16px（`--radius`） |
| 控件圆角 | 10px（`--radius-ctl`） |
| 页签/小件 | 8px（`--radius-sm`） |
| 页签底 | `8px 8px 0 0` |
| 行内间距 | 上下 15px，左右列间距 32px |
| 卡片内边距 | 卡头 `16px 22px`；行区 `8px 22px 12px` |
| 卡间距 | 18px |
| 阴影 | 卡片 `0 1px 2px / .04` + `0 12px 32px -26px / .3`；弹层再重一档 |
| 字体 | `"Segoe UI", system-ui, "PingFang SC", "Microsoft YaHei"` |
| 等宽 | `"JetBrains Mono", "Cascadia Mono", Consolas`（只用于 key、版本号、计数） |
| 字号 | 标题 30 / 卡头 14.5 / 正文 13.5 / 说明 12.5 / key 10.5 / 页脚 11.5 |

圆角只有三档。**不要**出现 999px 的胶囊（连接状态徽标除外）。

---

## 5. 组件

### 5.1 顶栏 `.topbar`

- 高度 56，`position: sticky; top: 0`，底色 `color-mix(canvas 88%, transparent)` + 12px 模糊，
  1px 底边 `--border`。
- 左：品牌（等宽 14/600）+ 版本（11） + 1px 竖线 + “设置”（13，muted）。
- 右：计数器（等宽 11.5，muted，窄屏隐藏）+ 连接徽标（`--success-soft` 底、
  `--success-ink` 字、6px 圆点）+ 白色胶囊 `.tb-tools`（内边距 3，1px 边，内含 30px 圆形图标按钮）。
- 图标按钮只能是文字符号（`◐` `⛶`），不引图标库；`aria-label` 必填。
- 滚动超过 8px 加 `.is-scrolled`：阴影加深，其它不变。**不做隐藏/变形顶栏。**

### 5.2 页头 `.head`

- `padding-top: 28`；标题 30/600，行高 1.25；说明 13.5，muted，`text-wrap: pretty`。
- 右侧动作：`.dirty-label`（12.5，muted，无改动时 `hidden`）+ 放弃改动（outline）+ 保存（primary）。
- **保存按钮常亮**，不置灰；没有改动时点击只提示“没有改动”，不发请求。
- 插件页隐藏整组动作（插件卡片自带启停）；**人设页保留**，因为兜底昵称属于配置，
  正文的「保存人设」在卡片里，两者范围不同。

### 5.3 页签 `.tabbar` / `.tabbtn`

- 容器：flex，gap 4，`margin: 20px 0 22px`，底部 1px 边，窄屏横向滚动（隐藏滚动条）。
- 页签：高 38，左右 12，圆角 `8px 8px 0 0`，13.5px，muted。
- 选中：`--primary-soft` 底 + `--primary-ink` 字 + 500 字重 +
  **2px 下滑块**（`.tab-ink`，绝对定位，随切换滑动）。
- 计数徽标 `.count`：10.5px，圆角 999，浅灰底；选中时浅蓝底。
- 脏状态：页签文字后一个 5px 圆点（`--primary`）。
- 语义：这是导航不是 tab 组件，用 `aria-current="page"`，**不要**加 `role="tab"`/`tablist`；
  面板用 `role="region"` + `aria-labelledby`。
- 键盘：左右方向键在页签间移动（roving tabindex），Enter/Space 切换。

### 5.4 卡片 `.card`

- 白底、1px `--border`、16px 圆角、软阴影；`padding: 0`（Basecoat 的 `.card` 自带
  24px 纵向 padding 和 gap，必须显式清零）。
- 卡头 `.card-head`：标题 14.5/600 + 右侧 `.aside`（12.5，muted，单行省略）。
- **普通配置卡不带卡头**：页头已经说明这一组，卡头只会重复；只有模型、人设、插件这种
  一页多块的内容才用卡头区分。
- 卡脚 `.card-foot`：1px 发丝线，放工具按钮和探测结果。
- 多张卡纵向 18px；卡与卡之间不套娃。

### 5.5 设置行 `.row`

- 两列：`minmax(0,1fr) 320px`，区域 `"head ctl" / "hint ctl"`，列距 32，行内边距 15。
- 左：`.row-head`（label 13.5/500 + `.key` 10.5 + `.tag`），下面 `.hint`。
- 右：`.row-ctl`，控件宽 100%，垂直居中。
- 开关行：`.row-ctl.tight`，右对齐，`input[role=switch]` + “开/关”小字。
- 长文本（人设正文）：`.row.wide`，单列，控件在说明下方。
- 第一行无顶边；其余行 1px `--hairline`。
- **一屏最多 7 行**；超过就把非核心项收进“进阶设置”折叠卡。

### 5.6 控件

- `.input` / `.select`：高 40，左右 12，10px 圆角，1px `#e2ebf1`，13.5px。
- `.textarea`：同圆角，内边距 10/12，行高 1.65，可纵向拉伸。
- 焦点：`border-color: --primary` + `box-shadow: 0 0 0 3px primary/15%`。**不许去掉轮廓。**
- 开关：Basecoat 的 `input[role=switch]`，44×24 轨道；不改尺寸。
- 校验失败：`.invalid` 红边 + `.err` 红字（12.5），错误在控件下方。

### 5.7 按钮

- 高 40，左右 16，10px 圆角，13.5/500。
- primary：`--primary` 底 + 白字（深色模式深色字）；outline：白底 + 1px 边。
- 图标按钮：30–32 圆形，透明底，hover 浅蓝。
- hover 140ms；`:active` `scale(.98)` 90ms；禁用 `opacity .55`，不改变色相。
- 主按钮一屏最多一个。

### 5.8 实体卡（模型 / 人设）`.entity`

- 最小宽 220，内边距 11/14，12px 圆角，白底。
- 名称 13/600，副行等宽 11，muted；“使用中”角标 `--primary-soft`。
- 选中：1px `--primary` 55% 混色 + 4% 浅蓝底。
- 悬停：边色加深；按下 `translateY(1px)`。
- 新建行：输入框 38 + 新建/排序 outline 小按钮。

### 5.9 插件卡 `.plugin`

- 三列自适应（min 300），内边距 16/18，16px 圆角。
- 头：36px 色块（首字，HSL 由 id 哈希）+ 标题/版本/作者 + 右侧开关。
- 正文：12.5，最多 3 行。
- 底部动作：设置 / 文档 / 打开面板 / 重载 / 删除（删除用危险色小按钮）。
- 列表区右上角：`安装 ZIP` outline 按钮，和「刷新」并排；点击打开拖放弹窗，
  拖入或点击拖放区（再开系统文件选择）后显示文件名/大小，确认才上传；上传中按钮文字
  变「安装中…」，不铺骨架。安装后默认不启用，卡片开关负责启用。
- 加载失败：1px `--danger` 32% 边、`--danger` 正文，动作区显示“加载失败”，
  保留“文档 / 重载 / 删除”（重载可以拿来重试）。
- **文档**：弹窗把插件根目录 README（大小写不敏感，超过 256KB 截断）按 Markdown 渲染，
  再用白名单清洗（不加载图片、不执行脚本/事件属性）；文件不存在时 toast “这个插件没有 README”。
- **删除**：弹窗内二次确认，两个单选项：
  `只删插件（保留 data/plugin_data/<id>/）` / `连持久化数据一起删`；
  默认选保留，选删数据时按钮文案改成“删除插件和数据”。必须勾选“我知道删除后不能恢复”
  才能点删除；先卸载插件，再把目录改名成隐藏临时目录清理。
- **重载**：只重载这一个插件（`registry.reload()`：先 `on_stop`、取消后台任务、清子模块
  缓存，再按启用状态重新加载），提示“钩子已重载；router 变更需重启进程”。请求期间禁用。
- **安装 ZIP**：支持两种结构：`<插件id>/plugin.toml`（所有文件都在这个顶层目录里），
  或 ZIP 根目录直接放 `plugin.toml`（ZIP 文件名 / `?name=` 作为插件 id，非法字符转下划线）。
  拒绝绝对路径、`..`、反斜杠、符号链接、加密包、超过 8MB 的压缩包和解压后超过 64MB 的包；
  `__MACOSX/`、`.DS_Store` 自动忽略；根目录和子目录都出现 `plugin.toml` 时报“结构不明确”。
  同名插件已存在时 409。先解压到隐藏临时目录、清单校验通过后才 rename 落位。
- **刷新**：列表已有卡片时不要抽掉重插骨架（会闪一条 3 列 × 2 行的黑条）。
  保留现有卡片，整块淡化到 50%，计数改成“刷新中…”，回来再淡入。

### 5.10 弹窗 `.modal`（原生 `<dialog>`）

- 类名用 `.modal`，**不要用 `.dialog`**：Basecoat 自带同名组件，会给子元素套
  `position: fixed` / `max-width: 28rem`，面板会被压成 448px。
- 原生 `<dialog>`；默认宽 `min(560px, 100%)`，README 用 `min(1080px, 100vw - 16px)`
  （≤720px 时全屏，Markdown 需要宽度），圆角 16，白底，软阴影。
- 结构：`header`（标题 15/600 + 说明 12.5）→ `section`（可滚，最大 86vh）→ `footer`
  （左：次要动作；右：取消 + 主操作）。**右上角不再放 X**，关闭走 footer、遮罩或 Esc。
- 遮罩 `oklch(0 0 0 / .35)`；打开 240ms，关闭 160ms。
- 危险操作二次确认：在同一弹窗内切换内容，不叠第二个弹窗。

### 5.11 提示 / 空态 / 骨架

- `.notice`：警告条，`--warning-soft` 底、`--warning-ink` 字、12px 圆角、12.5px。
- toast：底部居中，白卡 + 阴影，9/16，10px 圆角，2.2s 自动消失。
- 空态：虚线卡或居中说明，必须给下一步动作。
- 骨架：Basecoat `.skeleton` + 容器固定行高，避免布局跳动。

---

## 6. 密度

- **默认行式。** 说明在左、控件在右，扫读成本最低，也是参照站的写法。
- 一组 ≥8 个字段，或字段说明很短（<12 字）时，允许 `.rows[data-layout=grid]` 两列栅格；
  切栅格时 label 在上、控件在下、说明在最后。**同一页不许混用。**
  当前实现只有行式，栅格规则是预留；真要用时再按本节切。
- 非核心项进 `details.card` 折叠，summary 写“进阶设置 + N 项 + 一句人话说明”。
- 一页超过 2 屏必须折叠；折叠后首屏必须能看到主操作。

---

## 7. 动效

原则：只做状态切换的因果，不做入场表演。所有动画只碰 `transform` / `opacity` / `clip-path`。

### 7.1 曲线与时值

| 令牌 | 值 | 用途 |
|---|---|---|
| `--ease-out-quart` | `cubic-bezier(.25, 1, .5, 1)` | 通用退出/位移 |
| `--ease-out-expo` | `cubic-bezier(.16, 1, .3, 1)` | 面板、主题、折叠 |
| `--ease-spring-soft` | `cubic-bezier(.22, 1.15, .36, 1)` | 页签滑块、弹窗、toast |
| `--ease-spring` | `cubic-bezier(.34, 1.56, .64, 1)` | 开关、按下回弹 |
| `--dur-1..5` | 120 / 180 / 240 / 320 / 420 ms | 小件 / 状态 / 面板 / 页签 / 主题 |

### 7.2 交互清单

| 交互 | 效果 | 时值 |
|---|---|---|
| 页签切换 | `.tab-ink` 横向滑动（transform+width），选中底交叉淡入 | 320ms spring-soft |
| 模型/人设选中框 | `.entity-ink` 在卡片之间滑动（transform + 宽高），卡片本身透明底 | 320ms spring-soft |
| 面板切换 | 新面板 `translateX(±10px)` + 淡入，方向跟随页签顺序 | 220ms out-expo |
| 模型/人设切换 | **不整页重渲染**：字段原地同步，相关卡片 200ms 淡入 | 200ms out-expo |
| 页头文字 | 标题/说明换字后 200ms 淡入 | 200ms out-expo |
| 主题切换 | View Transition 圆形揭示，圆心在按钮 | 420ms out-expo |
| 主题图标 | `rotate(180deg)` + 交叉淡入 | 240ms spring-soft |
| 顶栏滚动 | `.is-scrolled` 阴影/底色加深 | 200ms out-quart |
| 按钮 | hover 底色 140ms；active `scale(.98)` | 90ms |
| 开关 | 滑块 220ms spring，轨道色 160ms | — |
| 卡片/实体 | hover 边色与阴影；按下 `translateY(1px)` | 160ms |
| 弹窗 | 遮罩淡入 160ms；面板 `scale(.97)+Y(8px)` → 1；关闭即时 | 240ms spring-soft |
| toast | 上浮 10px + 轻微缩放回弹 | 260ms spring |
| 折叠 | 箭头 240ms 旋转；内容用原生 details 展开（不做高度动画） | 240ms out-expo |

### 7.3 底线

- `prefers-reduced-motion: reduce` 时全部时长降到 1ms，去掉位移。
- 不用 `window.addEventListener('scroll')` 做动画；顶栏只用 IntersectionObserver 或
  scroll 只切一个 class（本规范允许后者，因为只改 shadow）。
- 不引 GSAP / Motion / Lottie。所有动效用 CSS transition/animation + 少量 JS 测量。
- 不做视差、不做无限循环动画、不做鼠标跟随。

---

## 8. 文案

- 中文，短句，说人话；技术名词保留英文（`token`、`prompt`、`WS`）。
- 标题不加句号；说明可以一句，不超过 40 字。
- 不用 emoji；不用长破折号（`——`）在 UI 里；分隔用 `·`。
- 按钮 2–4 个字：保存 / 放弃改动 / 获取模型 / 测试连接 / 新建 / 排序。
- 危险动作写清后果：“删除插件”“连数据一起删”，不要只写“确定”。
- 空态给下一步：“还没有插件。放一个目录到 plugins/，或安装 ZIP。”

---

## 9. 可访问性

- 正文对比度 ≥ 4.5:1；大字/图标 ≥ 3:1。**主按钮白字只有 2.7:1，是参照站的选择，
  属已知偏差；改色需同时改 `--primary` 和 `--primary-ink`，并重跑对比度检查。**
- 焦点环永远可见；不许 `outline: none` 而不给替代。
- 所有图标按钮有 `aria-label`；页签用 `aria-current="page"`，面板 `role="region"`。
- 开关用 `role="switch"` + `aria-checked`；表单一律 label 关联，不用 placeholder 当 label。
- 键盘可达：Tab 顺序 = 顶栏 → 页头动作 → 页签 → 卡片；Ctrl+S 保存；Esc 关弹窗。
- 点击热区 ≥ 32×32；行内小按钮 ≥ 28。

---

## 10. 工程约束

1. 没有构建链；前端是原生 JS + Basecoat 1.1.0（`static/basecoat.css`，平铺，不进子目录）。
2. 静态资源走 `/static/<name>`，`routes.py` 自动加 `?v=<mtime>`；新增文件放在 `static/` 根。
3. Basecoat 的按钮默认态 = `.btn` 不带 `data-variant`；**不要**写 `data-variant="default"`。
4. 颜色/圆角/阴影只准在 `theme.css` 定义；`style.css` 只引用。
5. JS 渲染的节点必须用 `uiButton` / `uiInput` / `uiSwitch`，不要各处手拼 class。
6. 页面不加载任何外部资源；不新增依赖（除非同时更新本文和 `AGENTS.md`）。
7. 每次改完跑：`pytest tests -q`、`ruff check .`、`python -m app.pluginkit.check plugins`。

---

## 11. 验收清单

- [ ] 顶栏只有 brand / 连接 / 主题 / 全屏，没有分组导航
- [ ] 没有侧栏；9 个分组在页内页签里
- [ ] 页面最大宽 1200、内边距 24；卡片不嵌套
- [ ] `html`/`body` 是 `overflow-x: clip`，滚轮能滚整页（展开进阶设置后再试一次）
- [ ] 主色只出现在主按钮、选中页签、焦点环、链接
- [ ] 保存按钮常亮，无改动时点击有提示
- [ ] 页签有滑动 ink 和脏点；宽屏一行排得下，窄屏可横滚
- [ ] 模型/人设的选中框在卡片之间滑动，不跳位、不叠框
- [ ] 模型/人设切换不整页重渲染、不跳滚动位置
- [ ] 插件刷新不抽掉现有卡片、不闪骨架
- [ ] 深色模式全部令牌齐全（含 Basecoat 的 `--card-foreground` 等）
- [ ] 动效只碰 transform/opacity；reduced-motion 下静止
- [ ] 键盘能走完：页签 → 卡片 → 弹窗；焦点可见
- [ ] 空态/加载/错误三种状态都有
- [ ] 无 emoji、无外部请求、无 `data-variant="default"`

---

## 12. 变更流程

1. 先改本文（令牌、组件、动效），写清改哪一条、为什么。
2. 改 `theme.css` → `style.css` → 页面/JS，顺序别倒。
3. 起面板（`start.cmd`），1440 和 512 各截一张，浅色深色各一张。
4. 跑三条校验；提交信息写 `feat(web):` / `style(web):`。
5. 如果改了令牌或骨架，**必须**同步更新本文和 `AGENTS.md` 的 UI 条目；否则下个窗口会漂。

---

## 13. 出处与授权

- 视觉语言参考 SnowLuma 控制台（顶栏/页头/页内页签/白卡/设置行），只参考结构与比例，
  **没有复制它的代码、CSS、图标、Logo 或文案**。SnowLuma 是 source-available 非商业许可，
  与本项目（MIT）相互独立。
- 组件底座是 [Basecoat](https://basecoatui.com)（MIT），本地内置。
- 不把本项目描述成 SnowLuma 的衍生版，不使用其名称/标识做推广；如果将来商业化或与它形成
  直接竞争，先重新评估这一节。

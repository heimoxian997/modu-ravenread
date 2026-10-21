# RavenRead 前端风格

本文件是后续前端任务的入口文档。新页面、组件和弹层先读这里，不要重新发明颜色或层级。

## 品牌

- 中文名：**墨读**
- 英文名：**RavenRead**
- 侧栏 logo：`frontend/src/assets/brand/logo.svg`
- favicon：`frontend/public/favicon.svg`
- 品牌 SVG 使用暗绿/米金原色，不改内部配色；功能图标仍用 Element Plus，待后续统一提供。

## 暗色 tokens

来源：`frontend/src/assets/css/global.css` 的 `:root`。

```css
--bg: #13171C;
--card: #1B2128;
--card-soft: #171C22;
--green: #2E5D4B;
--green-hi: #40755E;
--green-soft: #5F8D77;
--teal: #4A7A6E;
--violet: #6E5E93;
--violet-hi: #9183BC;
--gold: #C4A35A;
--gold-dim: rgba(196, 163, 90, .55);
--text: #EAE3CF;
--muted: #9DA29D;
--line: rgba(196, 163, 90, .22);
--line-soft: rgba(196, 163, 90, .12);
--danger: #7A2530;
--danger-hi: #B05560;
```

派生变量：

```css
--surface-hover: rgba(196, 163, 90, .06);
--green-soft-bg: rgba(46, 93, 75, .12);
--green-active-bg: rgba(46, 93, 75, .36);
--violet-soft-bg: rgba(110, 94, 147, .16);
--teal-soft-bg: rgba(74, 122, 110, .16);
--gold-soft-bg: rgba(196, 163, 90, .08);
--topbar: rgba(19, 23, 28, .94);
--sidebar: #151A20;
--toast: #141A1E;
--shadow-soft: 0 8px 30px rgba(0, 0, 0, .32);
```

兼容变量：`--ink`、`--ink-soft`、`--paper`、`--paper-deep`、`--amber`、`--amber-deep`。新代码优先用主 tokens。

## Element Plus 暗色覆盖

`main.js` 引入 `element-plus/theme-chalk/dark/css-vars.css`，并在 `<html>` 加 `dark`。

`global.css` 的 `html.dark` 覆盖：

- `--el-bg-color` / `--el-bg-color-page` / `--el-bg-color-overlay`
- `--el-text-color-*` / `--el-border-color-*` / `--el-fill-color-*`
- `--el-color-primary` 墨绿及 light/dark 阶梯
- success / warning / danger / info
- `--el-mask-color` / `--el-box-shadow-light`

组件覆盖：

- `.el-dialog`、`.el-message-box`：`--card` 底 + `--line` 边框
- `.el-table`：表头 `#171C22`、行 hover 绿 18%、边框 `--line-soft`
- Input / Textarea / Select wrapper：`--card-soft`
- Tag success / warning / danger / info / primary
- model-tag：紫；embedding-tag：teal
- Radio / Checkbox / Progress / Pagination：绿/金
- Message / Notification / Loading mask / Dropdown / Alert / Empty / Tabs / Collapse

## 组件语义

- 页面外壳：`body` 使用 `--bg` + 轻微 green/gold 径向光晕
- 卡片：`.panel` = `--card` + `--line` + `--shadow-soft`
- 页面标题：`.page-header` / `.page-title` / `.page-subtitle`
- 统计卡：`--card-soft` 或 `--card`，数字用 text，说明用 muted
- 主按钮：绿色 `--green`，hover `--green-hi`
- 详情/查看按钮：`.detail-gold`，金色文字/边框/淡金底
- 删除/危险：`--danger` 底、`--danger-hi` 文字，暗红透明底
- 输入/预览/次级区域：`--card-soft`
- 顶栏：`--topbar` + blur；侧栏：`--sidebar`
- 导航激活：`--green-active-bg`，文字 gold/text
- 书脊：teal/green、violet、green-soft 三组交替

## 硬性规则

1. 禁止新增浅色硬编码：`#fff`、`#fffdf8`、`#f6f3ec`、`#faf6ed` 等一律不用。
2. 新颜色必须从 tokens 派生；不要在页面里复制 HEX。
3. 新页面/弹层必须复用 `.panel`、`.page-header` 和现有 Element Plus 组件。
4. 不改整体信息架构和路由；主题任务只换视觉。
5. 功能图标暂保留 Element Plus，等用户提供后再替换；本步只替换品牌 logo/favicon。
6. 不把摘要正文、密钥或用户书稿写进前端日志。
7. 修改后跑 `cd frontend && npm run build`，并检查 1440px / 800px。

## 当前视觉基线

- 文字：`--text`，次级：`--muted`
- 边框：`--line` 主 / `--line-soft` 次
- 选中文字：`#F4EDDA` 底 + `#2b2114` 字
- 滚动条：thumb `--scroll-thumb`，track `--scroll-track`，hover `--green`
- 浏览器标题：`墨读 · RavenRead`

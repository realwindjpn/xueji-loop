# 学记 · 灯下书卷  v6 — 设计系统

> 单层重写。深墨底 + 暖金灯晕 + 衬线标题。
> 相对 v1→v5：放弃叠层注入器，**一次性**写完视觉层；
> 保留 PREVIEW_MODE 登录绕过（开发与演示基础）。

---

## 1. Design Token

### 1.1 色彩

| 层级 | 名称 | 值 | 用途 |
|---|---|---|---|
| 底 | `--ink-0` | `#0a0805` | 页面最深处 |
| 底 | `--ink-1` | `#0e0c08` | body 背景 |
| 底 | `--ink-2` | `#16130d` | 容器底 |
| 底 | `--ink-3` | `#1f1a12` | 卡片底 |
| 底 | `--ink-4` | `#2a2418` | hover 浮起 |
| 玻璃 | `--ink-glass` | `rgba(31,26,18,.72)` | 顶栏 / 浮层 |
| 玻璃 | `--ink-glass-2` | `rgba(20,16,10,.55)` | 次级浮层 |
| 金 | `--gold-1` | `#f3d089` | 高亮（数字 / 关键） |
| 金 | `--gold-2` | `#e8b95c` | 主金（按钮 / 强调） |
| 金 | `--gold-3` | `#d4a44c` | hover / 描边 |
| 金 | `--gold-4` | `#b8862a` | disable 边 |
| 光 | `--gold-glow` | `rgba(232,185,92,.35)` | 灯晕外溢 |
| 弱化 | `--ink-soft` | `#7d6f57` | 副文字 |
| 强 | `--ink-strong` | `#f5ecdb` | 主文字 |
| 危险 | `--ink-danger` | `#e07a5f` | 错误 / 异常 |
| 成功 | `--ink-ok` | `#8fb98a` | 完成 / 成功 |

### 1.2 字号

| 名称 | 值 | 场景 |
|---|---|---|
| `--fs-xs` | `12px` | 元信息、徽章 |
| `--fs-sm` | `13px` | 副标题、辅助 |
| `--fs-md` | `15px` | 正文 |
| `--fs-lg` | `clamp(18px, 2vw, 22px)` | 卡片标题 |
| `--fs-xl` | `clamp(22px, 3vw, 28px)` | 区段标题 |
| `--fs-2xl` | `clamp(28px, 4vw, 40px)` | 页面 hero |
| `--fs-3xl` | `clamp(40px, 6vw, 64px)` | 数字大字 / 仪式感 |

### 1.3 间距（4 / 8 栅格）

`--space-1..12` = 4 / 8 / 12 / 16 / 20 / 24 / 32 / 40 / 48 / 64 / 80 / 96 px

### 1.4 圆角

`--radius-sm: 6px` / `--radius-md: 10px` / `--radius-lg: 16px` / `--radius-pill: 999px`

### 1.5 阴影

`--shadow-1: 0 1px 2px rgba(0,0,0,.4)` / `--shadow-2: 0 4px 12px rgba(0,0,0,.5)` /
`--shadow-3: 0 12px 32px rgba(0,0,0,.6)` / `--shadow-amber: 0 0 24px var(--gold-glow)`

### 1.6 缓动（单曲线）

`--ease: cubic-bezier(.22,.61,.36,1)` — 唯一一条，节奏统一。
`--dur-fast: 180ms` / `--dur-base: 280ms` / `--dur-slow: 480ms`

---

## 2. 字体

- **衬线（标题）**：`"Source Han Serif SC", "Noto Serif SC", "Songti SC", serif`
- **无衬线（正文）**：`"PingFang SC", "Hiragino Sans GB", -apple-system, system-ui, sans-serif`
- **数字**：`"SF Mono", "JetBrains Mono", ui-monospace`（进度 / 计数器 / 大字）

---

## 3. 装饰层

单一 source of truth：`<div class="fx-bg" aria-hidden="true">` 内放：

1. 灯晕（顶部中心 radial）：`radial-gradient(ellipse at 50% -10%, rgba(232,185,92,.22), transparent 60%)`
2. 远光（右下角 secondary）：`radial-gradient(ellipse at 90% 110%, rgba(212,164,76,.10), transparent 55%)`
3. 极细网格：`linear-gradient` 0.04 不透明度
4. 噪点（`<svg>` turbulence，0.04 不透明度）

**层级**：`z-index: 0`、内容 `z-index: 1+`、`pointer-events: none`。
**不**用 `filter: blur`、**不**用 `background: fixed`（iOS 椭圆 bug 源头）。

---

## 4. 组件库（每个含全状态：default / hover / active / focus / disabled / loading）

| 组件 | 说明 |
|---|---|
| `.btn` | 主金填充 / 次金描边 / ghost 三态；按下涟漪（`@keyframes ripple-out`） |
| `.card` | ink-3 底 + 1px gold-4 边 + hover 浮起 + `lamp-breath` 微呼吸 |
| `.input` | ink-2 底 + focus 金边 + 焦点环 `--gold-glow` |
| `.tab` | 底部下划线（激活 gold-2），避免边框切割 |
| `.step` | 5 步流程条（学生端今日任务）；已完成步金色实心 + 当前步呼吸 + 未做步虚 |
| `.toast` | 右下角浮条；`toast-in` 进入 / `toast-out` 退出 |
| `.drawer` | 右侧抽屉；`backdrop-filter` + `transform: translateX` 滑入 |
| `.progress` | 进度条生长（`@keyframes meter-sheen`） |
| `.fab` | 浮动主行动；`@keyframes fab-pulse` |
| `.rail` | **横向轮盘**（scroll-snap-type: x mandatory）用于降低首屏密度 |
| `.flip` | 知识卡翻面（`@keyframes flip`） |

---

## 5. 生动感三层

### 5.1 打开即有（on-load）
- 入场错落浮现：`@keyframes rise`（透明度 + translateY 16→0，stagger 60ms）
- 数字滚动：`@keyframes counter`（CSS 计数 + JS 补帧）→ `data-num` 选择器
- 进度条生长：`@keyframes meter-sheen`（从 0% 到目标值）
- 灯晕呼吸：`@keyframes lamp-breath`（5.5s 慢呼吸）

### 5.2 触得到（touch）
- 按钮按下涟漪：`@keyframes ripple-out`（260ms 圆环扩散）
- 卡片微倾（仅 hover 触发，不在触摸端启用）
- 打卡成功彩屑：`@keyframes confetti-fall`（仅关键动作触发，频次克制）
- FAB 呼吸：`@keyframes fab-pulse`（2.4s，提醒但不打扰）

### 5.3 有节奏（rhythm）
- 全站统一 200–400ms + 单条 `--ease` 曲线
- 入场用 320ms、退场用 240ms、悬浮用 200ms、慢仪式用 480ms

---

## 6. 渐进披露（降低首屏信息密度）

首屏只留 **核心状态 + 主行动**；次要信息用以下模式承载：

| 模式 | 实现 | 适用 |
|---|---|---|
| **横向轮盘** | `.rail` + `scroll-snap-type: x mandatory` | 学生端 5 步学习流程（卡片左右滑） |
| **分步揭示** | `.step` 流程条 + 锚点滚动 | 学生端今日任务（点 2/5 → 跳到第 2 步） |
| **折叠收纳** | `<details>` + 动效高度过渡 | 家长端「使用流程」「补充说明」等次要段落 |
| **抽屉承载** | `.drawer` | 全部次要面板（设置、详情、历史） |
| **可关提示** | `.toast` + `prefers-reduced-motion` 立即显隐 | 全局提示 |

---

## 7. 降级

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.001ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.001ms !important;
  }
  .fx-bg > *:not(.fx-grid) { display: none; }
  .fab, .lamp-breath { animation: none; }
}
```

- 装饰层只留 `.fx-grid`（无动画）
- 计数器直接显终值
- 彩屑 / 噪点 / 灯晕呼吸全部停止

---

## 8. 工程红线（v1→v5 翻车教训的固化）

1. **不**用 `filter: blur`（iOS 降级硬边）
2. **不**用 `background: fixed` + 大半径径向（椭圆 bug 源头）
3. 装饰层永远 `aria-hidden` + `pointer-events: none` + `z-index: 0`
4. 内容层 `z-index: 1+`、`<main>` 显式 `position: relative`
5. 窄屏 fixed 元素（FAB / 徽章 / toast）必须 `env(safe-area-inset-*)` + 显式 max-width
6. 所有色彩走 token，**不**留裸 hex
7. 主线程不跑大计算（粒子 / shader 之类），用 IntersectionObserver + `will-change` 控制
8. 三页共用同一套 token + 装饰层 + 动效曲线，**不**各画各的
9. 单层重写，**不**做 v1→v6 那种叠层注入
10. 单文件 HTML 形态不变，零构建链

---

## 9. 验收对照

| 验收项 | 落地情况 |
|---|---|
| 桌面 Chrome 1440 | 三页布局完整，无溢出 / 错位（`screenshots/*__1440.png`） |
| 桌面 Chrome 1024 | 三页在桌面最小宽度仍可用（`*__1024.png`） |
| iPhone 390 | 家长 / 学生两页无横向滚动（`*__390.png`） |
| 微信 UA | fixed 背景 / filter 不出硬边（`*__wechat.png`） |
| `prefers-reduced-motion` | 装饰全降级，计数器显终值（`*__reduced.png`） |
| PREVIEW_MODE | 默认 ON，`?preview=0` 关闭；fetch 拦截 `/api/*` 返回 503 |
| 三页 design language 一致 | 同一套 token / 字体 / 圆角 / 阴影 / 动效 |
| `/api` 契约兼容 | UI 不依赖 main 之外的新字段 |

---

## 10. 文件结构

```
xueji-loop/
├── xueji_parent_h5.html         # 家长端（移动优先）
├── xueji_student_h5.html        # 学生端（移动优先）
├── xueji_loop_tool_api.html     # 后台 / API 调试（桌面优先）
├── server.py                    # 不动
├── build/
│   ├── theme-xueji.css          # 全部 design token + 组件 + 动效
│   ├── fx-engine.js             # PREVIEW_MODE + 注入装饰 + 计数器补帧
│   ├── inject.py                # 注入器（单层重写，幂等）
│   ├── xueji-shoot.js           # 截图脚本
│   └── dbg.js                   # 调试钩子
├── screenshots/                 # 真机验收截图
│   ├── parent__{1440,1024,390,wechat,reduced}.png
│   ├── student__{1440,1024,390,wechat,reduced}.png
│   └── admin__{1440,1024,390,wechat,reduced}.png
└── docs/
    └── v6-design-system.md      # 本文档
```

**入口三件套**：
- `xueji_parent_h5.html` — 家长端 H5
- `xueji_student_h5.html` — 学生端 H5
- `xueji_loop_tool_api.html` — 后台 / API 调试

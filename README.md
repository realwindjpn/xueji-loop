# 学记 · 灯下书卷 — 前端 MVP Demo

> 本仓库仅包含**前端工作分支**（`mvpdemo`），是基于原项目 [realwindjpn/xueji-loop](https://github.com/realwindjpn/xueji-loop) 的前端贡献成果。

## 贡献者声明

**前端贡献者：朱曦策**

本仓库的所有前端工作由朱曦策完成。原项目 `realwindjpn/xueji-loop` 是一个学记教育平台（家长端 / 学生端 / 管理端 H5 服务），包含后端逻辑与数据层。本仓库**仅提取前端部分**，不涉及后端、数据库或服务端逻辑的修改。

---

## 项目概述

学记·灯下书卷是一个三端教育 H5 平台：

| 页面 | 文件 | 说明 |
|------|------|------|
| 着陆页 | `web/index.html` | 双主题入口，展示三端入口卡 |
| 学生端 | `web/student.html` | 像素 RPG 风格学习界面 |
| 家长端 | `web/parent.html` | 极简现代·卡片式家长门户 |
| 管理端 | `web/admin.html` | 极简现代·卡片式管理后台 |


---

## 技术架构

### 双主题分派

```
build/inject.py（注入器）
├── theme-xueji.css    → 学生端（像素 RPG · 深紫 + Press Start 2P）
├── theme-minimal.css   → 家长端 / 管理端（极简卡片 · 浅灰 + 朱砂橙）
├── fx-engine.js        → 学生端完整特效引擎
└── fx-engine-minimal.js → 家长端 / 管理端精简特效引擎
```

注入器读取源 HTML，按终端类型替换 `<style>` 块并注入对应的 fx-engine，实现单次构建、三端分派。

### 布局审计门

`build/layout-audit.js` 是 v8 系列的硬验收标准：

- 3 个页面（student / parent / admin）× 4 个视口（375px / 768px / 1024px / 1280px）
- 共 12 项布局检查，全部 PASS 方可合入
- 使用 Puppeteer 自动化截图 + 像素级溢出检测

### 工具链

| 工具 | 用途 |
|------|------|
| `build/inject.py` | 主题 / 引擎注入器（Python） |
| `build/layout-audit.js` | 布局审计门（Node.js + Puppeteer） |
| `build/shoot-v84.js` | v8.4 系列截图脚本 |
| `build/shoot-interactive.js` | 交互流程截图（14 张） |
| `build/shoot-landing.js` | 着陆页截图 |
| `build/wide-test.js` | 宽屏平铺测试 |

---

## 页面预览

### 着陆页（双主题入口）

![着陆页](screenshots/landing.png)

### 学生端（像素 RPG · 登录后今日学习）

![学生端](screenshots/student.png)

### 家长端（极简卡片 · 登录后门户）

![家长端](screenshots/parent.png)

### 管理端（极简卡片 · 后台仪表盘）

![管理端](screenshots/admin.png)

---

## 分支说明

本仓库仅保留一个工作分支：

- **`mvpdemo`** — 当前工作分支，包含 v6 → v8.5 全部前端成果

原始项目地址：[realwindjpn/xueji-loop](https://github.com/realwindjpn/xueji-loop)

## 致谢

感谢原项目 [realwindjpn/xueji-loop](https://github.com/realwindjpn/xueji-loop) 提供的学记教育平台基础。本仓库仅对其前端部分进行了设计、布局和交互层面的工作。

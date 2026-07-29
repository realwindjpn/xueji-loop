#!/usr/bin/env python3
"""
学记 · 灯下书卷 v8 — 注入器（像素 RPG 主题）
从干净基线出发，单层重写视觉层：
  1) 替换 <style>...</style> 为新主题 CSS（内联）
  2) 注入 fx-engine.js 到 <head> 最前面（必须早于页面内联 script，让 fetch 拦截先行）
  3) 自动备份 .before-v<N>.bak（N 跟脚本内的 VERSION）
  4) 重复注入时按版本号剥离旧的 fx-engine 块，不依赖具体版本字符串
"""
import os
import re
from pathlib import Path

VERSION = "8.3"  # v8.3 学生端宽屏平铺（>=1280px 真实填满 / 1600px 三列分布）

ROOT = Path(__file__).resolve().parent.parent  # xueji-loop/
BUILD = ROOT / "build"

THEME = (BUILD / "theme-xueji.css").read_text(encoding="utf-8")
FX = (BUILD / "fx-engine.js").read_text(encoding="utf-8")

# 注入到 <head> 最前面 —— 这样可以早于页面 body 内的内联 script 把 fetch 装好
# 否则页面里的 refreshMe()、api("/health") 等会在 fx-engine 加载前就飞出去打 404
FX_INLINE_HEAD = (
    f"\n<!-- xueji-fx-engine v{VERSION} · 像素 RPG · 标题屏 + 任务日志 + 对话框 -->\n"
    "<script>\n" + FX + "\n</script>\n"
)

FILES = [
    "xueji_parent_h5.html",
    "xueji_student_h5.html",
    "xueji_loop_tool_api.html",
]

STYLE_RE = re.compile(r"<style>.*?</style>", re.DOTALL)
HEAD_OPEN_RE = re.compile(r"<head[^>]*>", re.IGNORECASE)
# 旧版 fx-engine 块：版本号无关，能扫到任何 xueji-fx-engine vN
OLD_FX_BLOCK_RE = re.compile(
    r"<!--\s*xueji-fx-engine\s+v\d+.*?</script>\s*",
    re.DOTALL | re.IGNORECASE,
)


def inject(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")

    # 备份：按当前 VERSION 命名；如已存在就跳过，避免反复覆盖历史
    bak = path.with_suffix(path.suffix + f".before-v{VERSION}.bak")
    if not bak.exists():
        bak.write_text(raw, encoding="utf-8")
        print(f"  ↳ backup: {bak.name}")
    else:
        print(f"  ↳ backup exists, skip: {bak.name}")

    # 1) 替换 <style>
    new_style = "<style>\n" + THEME + "\n</style>"
    if STYLE_RE.search(raw):
        new_raw = STYLE_RE.sub(new_style, raw, count=1)
    else:
        # 没有 <style>，在 <head> 末尾插入
        new_raw = re.sub(r"</head>", new_style + "\n</head>", raw, count=1, flags=re.IGNORECASE)

    # 2) 先剥掉任何旧版本 fx-engine 块
    new_raw = OLD_FX_BLOCK_RE.sub("", new_raw, count=1)

    # 3) 把新版 fx-engine 插到 <head> 最前面 —— 早于 body 内联 script
    if HEAD_OPEN_RE.search(new_raw):
        new_raw = HEAD_OPEN_RE.sub(
            lambda _m: _m.group(0) + FX_INLINE_HEAD,
            new_raw,
            count=1,
        )
    else:
        # 没有 <head>，兜底塞到 body 前面
        new_raw = re.sub(
            r"<body[^>]*>",
            lambda _m: _m.group(0) + FX_INLINE_HEAD,
            new_raw,
            count=1,
            flags=re.IGNORECASE,
        )

    path.write_text(new_raw, encoding="utf-8")
    size = path.stat().st_size
    print(f"  ✓ {path.name}: {size:,} bytes")


def main() -> None:
    os.chdir(ROOT)
    for name in FILES:
        p = ROOT / name
        if not p.exists():
            print(f"  ✗ missing: {name}")
            continue
        print(f"[inject] {name}")
        inject(p)
    print("[inject] done")


if __name__ == "__main__":
    main()

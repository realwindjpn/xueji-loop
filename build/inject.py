#!/usr/bin/env python3
"""
学记 · 灯下书卷 v6 — 注入器
从 main 干净基线出发，单层重写视觉层：
  1) 替换 <style>...</style> 为新主题 CSS（内联）
  2) 在 </body> 前注入 fx-engine.js（PREVIEW_MODE + 装饰层 + 动效）
  3) 自动备份 .before-v6.bak
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # xueji-loop/
BUILD = ROOT / "build"

THEME = (BUILD / "theme-xueji.css").read_text(encoding="utf-8")
FX = (BUILD / "fx-engine.js").read_text(encoding="utf-8")

# 注入 script 时包成 IIFE + 显式注释（便于在 DOM 中辨认）
FX_INLINE = (
    "\n<!-- xueji-fx-engine v6 · 灯下书卷 预览模式 + 装饰 + 动效 -->\n"
    "<script>\n" + FX + "\n</script>\n"
)

FILES = [
    "xueji_parent_h5.html",
    "xueji_student_h5.html",
    "xueji_loop_tool_api.html",
]

STYLE_RE = re.compile(r"<style>.*?</style>", re.DOTALL)
BODY_END_RE = re.compile(r"</body>", re.IGNORECASE)


def inject(path: Path) -> None:
    raw = path.read_text(encoding="utf-8")

    # 备份
    bak = path.with_suffix(path.suffix + ".before-v6.bak")
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

    # 2) 注入 fx-engine.js 到 </body> 前
    if "xueji-fx-engine v6" in new_raw:
        new_raw = re.sub(r"<!-- xueji-fx-engine v6.*?</script>\s*", "", new_raw, count=1, flags=re.DOTALL)
    new_raw = re.sub(r"</body>", lambda _m: FX_INLINE + "</body>", new_raw, count=1, flags=re.IGNORECASE)

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

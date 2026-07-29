#!/usr/bin/env python3
"""
学记 · 灯下书卷 v8.4 — 注入器（双主题）
- 学生端：像素 RPG（v8 主题 + 完整 fx-engine）
- 家长端 / 管理端：极简现代·卡片式（新主题 + 极简 fx-engine）
"""
import os
import re
from pathlib import Path

VERSION = "8.4"  # v8.4 家长端/管理端切换为极简现代·卡片式主题

ROOT = Path(__file__).resolve().parent.parent  # xueji-loop/
BUILD = ROOT / "build"

# === 双主题 ===
THEME_PIXEL = (BUILD / "theme-xueji.css").read_text(encoding="utf-8")
THEME_MINIMAL = (BUILD / "theme-minimal.css").read_text(encoding="utf-8")
FX_PIXEL = (BUILD / "fx-engine.js").read_text(encoding="utf-8")
FX_MINIMAL = (BUILD / "fx-engine-minimal.js").read_text(encoding="utf-8")

# 文件 → (主题 css, fx-engine, 终端名)
FILES = [
    ("xueji_parent_h5.html", THEME_MINIMAL, FX_MINIMAL, "PARENT"),
    ("xueji_student_h5.html", THEME_PIXEL, FX_PIXEL, "STUDENT"),
    ("xueji_loop_tool_api.html", THEME_MINIMAL, FX_MINIMAL, "ADMIN"),
]


def make_fx_block(fx: str, terminal: str) -> str:
    return (
        f"\n<!-- xueji-fx-engine v{VERSION} · {terminal} -->\n"
        "<script>\n" + fx + "\n</script>\n"
    )


STYLE_RE = re.compile(r"<style>.*?</style>", re.DOTALL)
HEAD_OPEN_RE = re.compile(r"<head[^>]*>", re.IGNORECASE)
# 旧版 fx-engine 块（任一版本）
OLD_FX_BLOCK_RE = re.compile(
    r"<!--\s*xueji-fx-engine\s+v\d+.*?</script>\s*",
    re.DOTALL | re.IGNORECASE,
)


def inject(path: Path, theme: str, fx: str, terminal: str) -> None:
    raw = path.read_text(encoding="utf-8")

    bak = path.with_suffix(path.suffix + f".before-v{VERSION}.bak")
    if not bak.exists():
        bak.write_text(raw, encoding="utf-8")
        print(f"  ↳ backup: {bak.name}")
    else:
        print(f"  ↳ backup exists, skip: {bak.name}")

    new_style = "<style>\n" + theme + "\n</style>"
    if STYLE_RE.search(raw):
        new_raw = STYLE_RE.sub(new_style, raw, count=1)
    else:
        new_raw = re.sub(r"</head>", new_style + "\n</head>", raw, count=1, flags=re.IGNORECASE)

    new_raw = OLD_FX_BLOCK_RE.sub("", new_raw, count=1)

    fx_block = make_fx_block(fx, terminal)
    if HEAD_OPEN_RE.search(new_raw):
        new_raw = HEAD_OPEN_RE.sub(
            lambda _m: _m.group(0) + fx_block,
            new_raw,
            count=1,
        )
    else:
        new_raw = re.sub(
            r"<body[^>]*>",
            lambda _m: _m.group(0) + fx_block,
            new_raw,
            count=1,
            flags=re.IGNORECASE,
        )

    path.write_text(new_raw, encoding="utf-8")
    size = path.stat().st_size
    print(f"  ✓ {path.name} [{terminal}]: {size:,} bytes")


def main() -> None:
    os.chdir(ROOT)
    for name, theme, fx, terminal in FILES:
        p = ROOT / name
        if not p.exists():
            print(f"  ✗ missing: {name}")
            continue
        print(f"[inject] {name} → {terminal}")
        inject(p, theme, fx, terminal)
    print("[inject] done")


if __name__ == "__main__":
    main()

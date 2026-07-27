"""Generate A4 portrait DOCX cards with no card text below 10.5 pt.

This is the standard 学记教育 printable-card template. The current master
style is the V30 card style accepted for 夏一 final-week cards:

- A4 portrait, margins top/bottom 2.0cm and left/right 1.5cm.
- One master table per day, 4 base columns x 7 rows.
- Four knowledge-card rows are fixed at 5.0cm. Future generators must not
  silently increase them; content should be shortened instead.
- Card practice text is split into two lines so the question/answer area has
  breathing room: "练：遮住答，说采分词。" and "1□会□卡□不  2□会□卡□不".
- Student-facing cover text must not expose internal version labels.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_ROW_HEIGHT_RULE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt


MIN_FONT = 10.5

SUBJECT_FILLS = {
    "语文": "FCEAEA",
    "历史": "FFF4D6",
    "地理": "E7F4EC",
    "生物": "EEE7F6",
    "道法": "F3F0E8",
    "英语": "EAF2FF",
    "数学": "F1F5F9",
    "物理": "EEF6F8",
    "化学": "F2F6EA",
}

SUBJECT_ORDER = ["语文", "历史", "地理", "生物", "道法", "英语", "数学", "物理", "化学"]


def cm_twips(value):
    return int(Cm(value).twips)


def set_run(run, size=MIN_FONT, bold=False):
    run.font.name = "Microsoft YaHei"
    run.font.size = Pt(max(size, MIN_FONT))
    run.bold = bold
    r_pr = run._element.get_or_add_rPr()
    r_fonts = r_pr.rFonts
    if r_fonts is None:
        r_fonts = OxmlElement("w:rFonts")
        r_pr.append(r_fonts)
    for key in ("eastAsia", "ascii", "hAnsi"):
        r_fonts.set(qn(f"w:{key}"), "Microsoft YaHei")


def para(p, line=13, after=0, before=0, align=None):
    p.paragraph_format.space_before = Pt(before)
    p.paragraph_format.space_after = Pt(after)
    p.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
    p.paragraph_format.line_spacing = Pt(line)
    if align is not None:
        p.alignment = align


def add(p, text, size=MIN_FONT, bold=False):
    r = p.add_run(str(text))
    set_run(r, size=size, bold=bold)
    return r


def margins(cell, top=50, start=70, bottom=45, end=70):
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_mar = tc_pr.first_child_found_in("w:tcMar")
    if tc_mar is None:
        tc_mar = OxmlElement("w:tcMar")
        tc_pr.append(tc_mar)
    for name, value in (("top", top), ("start", start), ("bottom", bottom), ("end", end)):
        node = tc_mar.find(qn(f"w:{name}"))
        if node is None:
            node = OxmlElement(f"w:{name}")
            tc_mar.append(node)
        node.set(qn("w:w"), str(value))
        node.set(qn("w:type"), "dxa")


def shade(cell, fill):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def set_cell_width(cell, twips):
    tc_pr = cell._tc.get_or_add_tcPr()
    tc_w = tc_pr.find(qn("w:tcW"))
    if tc_w is None:
        tc_w = OxmlElement("w:tcW")
        tc_pr.append(tc_w)
    tc_w.set(qn("w:w"), str(twips))
    tc_w.set(qn("w:type"), "dxa")


def fixed_table(table, col_widths_cm):
    table.autofit = False
    tbl_pr = table._tbl.tblPr
    tbl_w = tbl_pr.find(qn("w:tblW"))
    if tbl_w is None:
        tbl_w = OxmlElement("w:tblW")
        tbl_pr.append(tbl_w)
    tbl_w.set(qn("w:w"), str(sum(cm_twips(w) for w in col_widths_cm)))
    tbl_w.set(qn("w:type"), "dxa")

    tbl_layout = tbl_pr.find(qn("w:tblLayout"))
    if tbl_layout is None:
        tbl_layout = OxmlElement("w:tblLayout")
        tbl_pr.append(tbl_layout)
    tbl_layout.set(qn("w:type"), "fixed")

    tbl_ind = tbl_pr.find(qn("w:tblInd"))
    if tbl_ind is None:
        tbl_ind = OxmlElement("w:tblInd")
        tbl_pr.append(tbl_ind)
    tbl_ind.set(qn("w:w"), "0")
    tbl_ind.set(qn("w:type"), "dxa")

    grid = table._tbl.tblGrid
    if grid is None:
        grid = OxmlElement("w:tblGrid")
        table._tbl.insert(1, grid)
    for child in list(grid):
        grid.remove(child)
    for width_cm in col_widths_cm:
        col = OxmlElement("w:gridCol")
        col.set(qn("w:w"), str(cm_twips(width_cm)))
        grid.append(col)

    for row in table.rows:
        for idx, cell in enumerate(row.cells):
            if idx < len(col_widths_cm):
                set_cell_width(cell, cm_twips(col_widths_cm[idx]))


def clear(cell):
    for p in cell.paragraphs:
        p.clear()
        para(p)


def prep_cell(cell, fill=None, valign=WD_CELL_VERTICAL_ALIGNMENT.CENTER):
    if fill:
        shade(cell, fill)
    cell.vertical_alignment = valign
    clear(cell)


def set_row_height(row, height_cm):
    row.height = Cm(height_cm)
    row.height_rule = WD_ROW_HEIGHT_RULE.AT_LEAST


def setup_doc():
    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.PORTRAIT
    sec.page_width = Cm(21)
    sec.page_height = Cm(29.7)
    sec.top_margin = Cm(2.0)
    sec.bottom_margin = Cm(2.0)
    sec.left_margin = Cm(1.5)
    sec.right_margin = Cm(1.5)
    doc.styles["Normal"].font.name = "Microsoft YaHei"
    doc.styles["Normal"].font.size = Pt(MIN_FONT)
    return doc


def compact_text(value):
    return "" if value is None else str(value).strip()


def prefixed(value, prefixes, default_prefix):
    text = compact_text(value)
    if not text:
        return default_prefix
    normalized = text.replace(":", "：", 1)
    if any(normalized.startswith(prefix) for prefix in prefixes):
        return text
    return f"{default_prefix}{text}"


def subject_from_card(card):
    if not isinstance(card, dict):
        return ""
    text = " ".join(compact_text(card.get(key)) for key in ("tag", "q", "a"))
    for subject in SUBJECT_ORDER:
        if subject in text:
            return subject
    if any(key in text for key in ["古诗", "文言", "阅读", "作文", "名著", "翻译"]):
        return "语文"
    if any(key in text for key in ["隋", "唐", "宋", "元", "明", "清", "科举", "大运河"]):
        return "历史"
    if any(key in text for key in ["经纬", "地形", "气候", "河流", "工业", "农业", "交通"]):
        return "地理"
    if any(key in text for key in ["细胞", "血液", "呼吸", "消化", "神经", "激素"]):
        return "生物"
    if any(key in text for key in ["法律", "青春", "情绪", "集体", "未成年人", "规则", "品格"]):
        return "道法"
    return ""


def routine_text_for_cards(cards):
    counts = {}
    order = []
    for card in cards[:8]:
        subject = subject_from_card(card)
        if not subject:
            continue
        if subject not in counts:
            order.append(subject)
            counts[subject] = 0
        counts[subject] += 1
    if not order:
        return "顺序：先做题，再背采分词；5分钟后只复问★卡。"
    parts = [f"{subject}{counts[subject]}格" for subject in order]
    return "顺序：" + " → ".join(parts) + "；5分钟后只复问★卡。"


def add_card(cell, card):
    subject = subject_from_card(card)
    prep_cell(cell, SUBJECT_FILLS.get(subject, "FFFFFF"))
    margins(cell, 96, 92, 72, 92)

    p = cell.paragraphs[0]
    para(p, line=13.4, after=1.8)
    add(p, card.get("tag", "复习卡"), size=10.5, bold=True)

    p = cell.add_paragraph()
    para(p, line=13.6, after=1.8)
    add(p, prefixed(card.get("q", ""), ("题：", "问："), "题："), size=MIN_FONT, bold=True)

    p = cell.add_paragraph()
    para(p, line=13.6, after=1.8)
    add(p, prefixed(card.get("a", ""), ("答：",), "答："), size=MIN_FONT)

    p = cell.add_paragraph()
    para(p, line=13.0, after=1.0)
    add(p, "练：遮住答，说采分词。", size=MIN_FONT)

    p = cell.add_paragraph()
    para(p, line=13.0, after=0)
    add(p, "1□会□卡□不  2□会□卡□不", size=MIN_FONT)


def add_blank_card(cell, idx):
    prep_cell(cell)
    margins(cell, 36, 62, 32, 62)
    p = cell.paragraphs[0]
    para(p, line=13, after=4, align=WD_ALIGN_PARAGRAPH.CENTER)
    add(p, f"复习区 {idx}", size=10.8, bold=True)
    p = cell.add_paragraph()
    para(p, line=13, after=4)
    add(p, "错卡号/模板默写：________________", size=MIN_FONT)
    p = cell.add_paragraph()
    para(p, line=13, after=0)
    add(p, "今天最卡的一点：________________", size=MIN_FONT)


def merge_row(table, row_idx, start_col, end_col):
    return table.cell(row_idx, start_col).merge(table.cell(row_idx, end_col))


def add_cover(doc, title):
    p = doc.add_paragraph()
    para(p, line=30, after=4, align=WD_ALIGN_PARAGRAPH.CENTER)
    add(p, title, size=24, bold=True)

    p = doc.add_paragraph()
    para(p, line=21, after=12, align=WD_ALIGN_PARAGRAPH.CENTER)
    add(p, "阶段常考题抢分卡", size=15, bold=True)

    t = doc.add_table(rows=6, cols=1)
    t.style = "Table Grid"
    fixed_table(t, [18.0])
    blocks = [
        ("本周目标", "避开已经掌握的知识点，用常考样题补漏。每天先做题，再背采分词，最后把不会的★卡第二天回炉。"),
        ("每天8格", "每格都是“题 + 答 + 法”，不再空背方法。有明确科目分配时按分配走；没有明确分配时按薄弱点自动安排。"),
        ("怎么使用", "先遮住答案口头答题；答不出只看采分词；第二遍只说关键词，不背长段。不会的写★。"),
        ("语文重点", "阅读练概括、词语效果、句子含义、插叙、材料链接、结尾、换标题；文言练常考句子翻译。"),
        ("综合重点", "历史补事件和制度；地理补区域原因与措施；生物补功能型小题；道法补品格、集体和保护类材料题。"),
        ("过关标准", "能独立说出题目的2-3个采分词，就先算过。目标是能答题，不是把整页长段背死。"),
    ]
    for idx, (heading, body) in enumerate(blocks):
        cell = t.cell(idx, 0)
        prep_cell(cell, "FAFAFA")
        margins(cell, 140, 160, 128, 160)
        p = cell.paragraphs[0]
        para(p, line=18.5, after=3)
        add(p, heading, size=13.5, bold=True)
        p = cell.add_paragraph()
        para(p, line=18.0, after=0)
        add(p, body, size=12.5)


def add_day_page(doc, day, day_index):
    table = doc.add_table(rows=7, cols=4)
    table.style = "Table Grid"
    fixed_table(table, [4.5, 4.5, 4.5, 4.5])

    set_row_height(table.rows[0], 1.10)
    set_row_height(table.rows[1], 0.62)
    for row_idx in range(2, 6):
        set_row_height(table.rows[row_idx], 5.0)
    set_row_height(table.rows[6], 0.72)

    header_left = merge_row(table, 0, 0, 2)
    header_right = table.cell(0, 3)
    routine = merge_row(table, 1, 0, 3)

    card_cells = []
    for row_idx in range(2, 6):
        card_cells.append(merge_row(table, row_idx, 0, 1))
        card_cells.append(merge_row(table, row_idx, 2, 3))

    for cell in (header_left, header_right):
        prep_cell(cell, "F1F5F9")
        margins(cell, 55, 85, 45, 85)

    p = header_left.paragraphs[0]
    para(p, line=15, after=1)
    add(p, day.get("title", f"Day {day_index + 1:02d}"), size=11.5, bold=True)
    p = header_left.add_paragraph()
    para(p, line=12, after=0)
    add(p, day.get("module", ""), size=MIN_FONT)

    p = header_right.paragraphs[0]
    para(p, line=12, after=0)
    add(p, "姓名：______\n日期：______\n用时：____分钟", size=MIN_FONT)

    prep_cell(routine, "FAFAFA")
    margins(routine, 32, 80, 28, 80)
    p = routine.paragraphs[0]
    para(p, line=12, after=0)
    add(p, routine_text_for_cards(day.get("cards", [])), size=MIN_FONT, bold=True)

    cards = day.get("cards", [])[:8]
    for idx, cell in enumerate(card_cells):
        if idx < len(cards):
            add_card(cell, cards[idx])
        else:
            add_blank_card(cell, idx + 1)

    footer_texts = [
        "独立答出：____ / 8",
        "提示后答出：____",
        "★卡号：________",
        "情绪：□轻松 □能接受 □累",
    ]
    for idx, cell in enumerate(table.rows[6].cells):
        prep_cell(cell)
        margins(cell, 36, 42, 30, 42)
        p = cell.paragraphs[0]
        para(p, line=12, after=0, align=WD_ALIGN_PARAGRAPH.CENTER)
        add(p, footer_texts[idx], size=MIN_FONT, bold=True)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: generate_cards_docx_largefont.py input.json output.docx")
    plan = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    doc = setup_doc()
    add_cover(doc, plan.get("title", "学记教育基础卡"))
    for idx, day in enumerate(plan.get("days", [])):
        doc.add_page_break()
        add_day_page(doc, day, idx)
    doc.save(sys.argv[2])


if __name__ == "__main__":
    main()

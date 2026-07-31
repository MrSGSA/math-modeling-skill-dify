#!/usr/bin/env python3
"""Tiny python-docx helpers for the default math-modeling paper format."""

import os
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Inches, Pt
from lxml import etree


SKILL_ROOT = Path(__file__).resolve().parents[3]


def set_run_font(run, font="宋体", size=12, bold=False):
    run.font.name = font
    run.font.size = Pt(size)
    run.font.bold = bold
    r_fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    r_fonts.set(qn("w:ascii"), "Times New Roman")
    r_fonts.set(qn("w:hAnsi"), "Times New Roman")
    r_fonts.set(qn("w:eastAsia"), font)
    return run


@dataclass(frozen=True)
class ContestProfile:
    name: str
    paper: str
    margins: tuple[float, float, float, float]
    required_markers: tuple[str, ...]
    figure_labels: tuple[str, ...]
    table_labels: tuple[str, ...]
    reference_headings: tuple[str, ...]
    rules_source: str


CONTEST_PROFILES = {
    "cumcm": ContestProfile(
        name="全国大学生数学建模竞赛",
        paper="A4",
        margins=(2.54, 2.54, 3.18, 3.18),
        required_markers=("摘 要", "关键词："),
        figure_labels=("图",),
        table_labels=("表",),
        reference_headings=("参考文献",),
        rules_source="http://www.mcm.edu.cn/",
    ),
    "mcm-icm": ContestProfile(
        name="MCM/ICM",
        paper="LETTER",
        margins=(2.54, 2.54, 2.54, 2.54),
        required_markers=("Summary",),
        figure_labels=("Figure", "Fig."),
        table_labels=("Table",),
        reference_headings=("References",),
        rules_source="https://www.comap.com/contests/mcm-icm",
    ),
}


def get_profile(contest="cumcm"):
    try:
        return CONTEST_PROFILES[contest.lower()]
    except KeyError as exc:
        raise ValueError(f"未知竞赛配置: {contest}") from exc


def setup_page(doc, contest="cumcm"):
    profile = get_profile(contest)
    section = doc.sections[0]
    if profile.paper == "LETTER":
        section.page_width = Inches(8.5)
        section.page_height = Inches(11)
    else:
        section.page_width = Cm(21)
        section.page_height = Cm(29.7)
    top, bottom, left, right = profile.margins
    section.top_margin = Cm(top)
    section.bottom_margin = Cm(bottom)
    section.left_margin = Cm(left)
    section.right_margin = Cm(right)


def paragraph(doc, text="", align=None, first_line=False, line_spacing=1.25):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = line_spacing
    if first_line:
        p.paragraph_format.first_line_indent = Pt(24)
    if align is not None:
        p.alignment = align
    if text:
        set_run_font(p.add_run(text))
    return p


def title(doc, text):
    p = paragraph(doc, align=WD_ALIGN_PARAGRAPH.CENTER)
    set_run_font(p.add_run(text), "黑体", 14, False)
    return p


def abstract_title(doc):
    return title(doc, "摘 要")


def body(doc, text):
    return paragraph(doc, text, first_line=True)


def _latex2omml(latex):
    try:
        from .equations import latex2omml
    except ImportError:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from equations import latex2omml
    return latex2omml(latex)


def equation(doc, latex):
    p = paragraph(doc, align=WD_ALIGN_PARAGRAPH.CENTER)
    math_para = OxmlElement("m:oMathPara")
    math = OxmlElement("m:oMath")
    for child in etree.fromstring(_latex2omml(latex)):
        math.append(child)
    math_para.append(math)
    p._element.append(math_para)
    return p


def equation_placeholder(doc, latex, prefix="EQ"):
    placeholder = f"{prefix}_{uuid.uuid4().hex[:8].upper()}"
    body(doc, placeholder)
    return placeholder, latex


def keywords(doc, text):
    paragraph(doc)
    p = paragraph(doc)
    set_run_font(p.add_run("关键词："), bold=True)
    set_run_font(p.add_run(text))
    return p


def heading1(doc, text, page_break_before=False):
    p = paragraph(doc, align=WD_ALIGN_PARAGRAPH.CENTER)
    p.paragraph_format.page_break_before = page_break_before
    set_run_font(p.add_run(text), size=16, bold=True)
    return p


def heading2(doc, text):
    p = paragraph(doc)
    set_run_font(p.add_run(text), size=14, bold=False)
    return p


def heading3(doc, text):
    p = paragraph(doc, align=WD_ALIGN_PARAGRAPH.JUSTIFY)
    set_run_font(p.add_run(text), size=12, bold=True)
    return p


def page_break(doc):
    doc.add_page_break()


def section_break(doc):
    doc.add_section(WD_SECTION.NEW_PAGE)


def image(doc, path, width_cm=12):
    p = paragraph(doc, align=WD_ALIGN_PARAGRAPH.CENTER)
    with open(path, "rb") as image_file:
        p.add_run().add_picture(image_file, width=Cm(width_cm))
    return p


def figure_caption(doc, text):
    p = paragraph(doc, align=WD_ALIGN_PARAGRAPH.CENTER)
    set_run_font(p.add_run(text), size=10)
    return p


def count_chinese_chars(doc):
    text = "\n".join(_document_texts(doc))
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def _border(val="nil", size="0"):
    elem = OxmlElement("w:bottom")
    elem.set(qn("w:val"), val)
    elem.set(qn("w:sz"), size)
    elem.set(qn("w:space"), "0")
    elem.set(qn("w:color"), "000000" if val != "nil" else "auto")
    return elem


def _set_cell_bottom(cell, val="nil", size="0"):
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    for old in list(borders):
        if old.tag == qn("w:bottom"):
            borders.remove(old)
    borders.append(_border(val, size))


def _set_table_borders(table):
    tbl_pr = table._tbl.tblPr
    borders = tbl_pr.find(qn("w:tblBorders"))
    if borders is not None:
        tbl_pr.remove(borders)
    borders = OxmlElement("w:tblBorders")
    for name, val, size in [
        ("top", "single", "12"),
        ("start", "nil", "0"),
        ("left", "nil", "0"),
        ("bottom", "single", "12"),
        ("end", "nil", "0"),
        ("right", "nil", "0"),
        ("insideH", "nil", "0"),
        ("insideV", "nil", "0"),
    ]:
        elem = OxmlElement(f"w:{name}")
        elem.set(qn("w:val"), val)
        elem.set(qn("w:sz"), size)
        elem.set(qn("w:space"), "0")
        elem.set(qn("w:color"), "000000" if val != "nil" else "auto")
        borders.append(elem)
    tbl_look = tbl_pr.find(qn("w:tblLook"))
    if tbl_look is None:
        tbl_pr.append(borders)
    else:
        tbl_pr.insert(tbl_pr.index(tbl_look), borders)


def three_line_table(doc, rows):
    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    _set_table_borders(table)
    for row_i, row in enumerate(rows):
        for col_i, text in enumerate(row):
            cell = table.cell(row_i, col_i)
            cell.text = ""
            p = cell.paragraphs[0]
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            set_run_font(p.add_run(str(text)), size=12, bold=(row_i == 0))
            if row_i == 0:
                _set_cell_bottom(cell, "single", "4")
    return table


def _clear_template_body(doc):
    body_element = doc._element.body
    for child in list(body_element):
        if child.tag != qn("w:sectPr"):
            body_element.remove(child)


def new_document(contest="cumcm", template_path=None, preserve_template_content=False):
    """从空白文档或参考模板创建论文，可保留官方模板的固定正文。"""
    doc = Document(str(template_path)) if template_path else Document()
    if template_path and not preserve_template_content:
        _clear_template_body(doc)
    zoom = doc.settings.element.find(qn("w:zoom"))
    if zoom is not None and zoom.get(qn("w:percent")) is None:
        zoom.set(qn("w:percent"), "100")
    if not template_path:
        setup_page(doc, contest)
    return doc


def _document_texts(doc):
    """按正文 XML 顺序读取段落文字，包括表格单元格和文本框。"""
    texts = []
    for paragraph in doc._element.body.iter(qn("w:p")):
        text = "".join(node.text or "" for node in paragraph.iter(qn("w:t"))).strip()
        if text:
            texts.append(text)
    return texts


def _body_document_texts(doc, appendix_heading_patterns=None):
    """返回正文及其前置内容，遇到首个附录标题即停止。"""
    patterns = appendix_heading_patterns or (
        r"^\s*附录(?:\s|[A-ZＡ-Ｚ一二三四五六七八九十0-9]|$)",
        r"^\s*appendix(?:\s|[A-Z0-9]|$)",
        r"^\s*appendices(?:\s|$)",
    )
    texts = []
    for child in doc._element.body.iterchildren():
        text = "".join(node.text or "" for node in child.iter(qn("w:t"))).strip()
        compact = re.sub(r"\s+", "", text)
        if len(compact) <= 80 and any(
            re.match(pattern, text, re.IGNORECASE) for pattern in patterns
        ):
            break
        if text:
            texts.append(text)
    return texts


def _content_units(text):
    """按中文字符和连续拉丁字母/数字词计数，用于中英文混排篇幅预警。"""
    return len(re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9]+", text))


def _numbered_object_issues(doc, labels, object_count, display_kind):
    labels = (labels,) if isinstance(labels, str) else tuple(labels)
    label_pattern = "(?:" + "|".join(re.escape(label) for label in labels) + ")"
    caption_pattern = re.compile(rf"^\s*{label_pattern}\s*(\d+)(?!\d)", re.IGNORECASE)
    reference_pattern = re.compile(rf"{label_pattern}\s*(\d+)(?!\d)", re.IGNORECASE)
    captions = {}
    body_references = set()
    for text in _document_texts(doc):
        caption = caption_pattern.match(text)
        if caption:
            captions[int(caption.group(1))] = text
        else:
            body_references.update(int(number) for number in reference_pattern.findall(text))

    issues = []
    expected = set(range(1, object_count + 1))
    missing_captions = sorted(expected - set(captions))
    if missing_captions:
        issues.append(f"{display_kind}编号不完整，缺少题注: {missing_captions}")
    extra_captions = sorted(set(captions) - expected)
    if extra_captions:
        issues.append(f"{display_kind}题注没有对应对象或编号跳跃: {extra_captions}")
    for number in sorted(set(captions) - body_references):
        issues.append(f"{display_kind}{number} 已插入但未在正文引用")
    return issues


def _reference_issues(
    paragraphs_or_texts,
    headings=("参考文献", "references"),
    require_section=False,
    check_numeric_bijection=True,
):
    texts = [
        item.text.strip() if hasattr(item, "text") else str(item).strip()
        for item in paragraphs_or_texts
    ]
    normalized_headings = {str(item).strip().casefold() for item in headings}
    split_at = next(
        (index for index, text in enumerate(texts) if text.casefold() in normalized_headings),
        None,
    )
    if split_at is None:
        full_text = "\n".join(texts)
        has_numeric_citation = bool(re.search(r"\[[0-9,，\-–—\s]+\]", full_text))
        if require_section or has_numeric_citation:
            return ["正文存在引用或项目要求参考文献，但未找到参考文献章节"]
        return []
    if not check_numeric_bijection:
        return []
    body = "\n".join(texts[:split_at])
    bibliography = [text for text in texts[split_at + 1:] if text]
    cited = set()
    for group in re.findall(r"\[([0-9,，\-–—\s]+)\]", body):
        for item in re.split(r"[,，]", group):
            item = item.strip()
            if not item:
                continue
            bounds = re.split(r"[\-–—]", item)
            if len(bounds) == 2 and all(bound.strip().isdigit() for bound in bounds):
                start, end = (int(bound.strip()) for bound in bounds)
                if start <= end:
                    cited.update(range(start, end + 1))
            elif item.isdigit():
                cited.add(int(item))
    listed = {
        int(match.group(1))
        for text in bibliography
        if (match := re.match(r"^\[(\d+)\]", text))
    }
    issues = [f"正文引用 [{number}] 未出现在参考文献表" for number in sorted(cited - listed)]
    issues.extend(f"参考文献 [{number}] 未在正文引用" for number in sorted(listed - cited))
    return issues


def validate_paper_structure(
    doc,
    contest="cumcm",
    *,
    quality_checks=True,
    min_content_units=None,
    min_equations=None,
    min_figures=None,
    min_tables=None,
    rendered_pages=None,
    rendered_body_pages=None,
    abstract_fill_ratio=None,
    target_pages=None,
    target_abstract_fill_ratio=None,
    official_max_pages=None,
    electronic_file_size_bytes=None,
    official_max_file_size_mb=None,
    require_rendered_pages=True,
    require_abstract_fill_ratio=None,
    require_electronic_file_size=False,
    hard_constraints=None,
    quality_targets=None,
    required_markers=None,
    figure_labels=None,
    table_labels=None,
    reference_headings=None,
    require_reference_section=None,
    appendix_heading_patterns=None,
    excluded_figure_objects=0,
    excluded_table_objects=0,
    check_figure_numbering=True,
    check_table_numbering=True,
    check_numeric_reference_bijection=True,
):
    """按显式规则检查论文结构、渲染指标、图表和引用。

    ``hard_constraints`` 保存当届官方硬约束，``quality_targets`` 保存项目预先
    声明的非官方目标。二者均不从竞赛名称推断数值。旧的单项参数仍保留以兼容
    既有调用，并优先于映射中的同名值。
    """
    profile = CONTEST_PROFILES.get(str(contest).lower())
    hard = dict(hard_constraints or {})
    quality = dict(quality_targets or {})
    markers = tuple(required_markers) if required_markers is not None else (
        profile.required_markers if profile else ()
    )
    figure_labels = tuple(figure_labels) if figure_labels is not None else (
        profile.figure_labels if profile else ("图", "Figure", "Fig.")
    )
    table_labels = tuple(table_labels) if table_labels is not None else (
        profile.table_labels if profile else ("表", "Table")
    )
    reference_headings = tuple(reference_headings) if reference_headings is not None else (
        profile.reference_headings if profile else ("参考文献", "References")
    )
    texts = _document_texts(doc)
    errors = []
    if not texts:
        errors.append("缺少论文标题")
    full_text = "\n".join(texts)
    for marker in markers:
        if marker == "Summary":
            present = any(text.lower() in {"summary", "summary sheet"} for text in texts)
        elif marker.endswith("："):
            present = any(text.startswith(marker) for text in texts)
        else:
            present = marker in texts
        if not present:
            label = "摘要" if marker == "摘 要" else "关键词" if marker == "关键词：" else marker
            errors.append(f"缺少官方结构项: {label}")
    if "[待补充" in full_text:
        errors.append("论文仍含 [待补充] 占位符")
    if not quality_checks:
        return errors

    min_content_units = quality.get("min_content_units", 0) if min_content_units is None else min_content_units
    min_equations = quality.get("min_equations", 0) if min_equations is None else min_equations
    min_figures = quality.get("min_figures", 0) if min_figures is None else min_figures
    min_tables = quality.get("min_tables", 0) if min_tables is None else min_tables
    target_pages = quality.get("body_pages") if target_pages is None else target_pages
    target_abstract_fill_ratio = (
        quality.get("abstract_fill_min")
        if target_abstract_fill_ratio is None
        else target_abstract_fill_ratio
    )
    max_abstract_fill_ratio = quality.get("abstract_fill_max")
    official_max_pages = hard.get("max_body_pages") if official_max_pages is None else official_max_pages
    official_max_file_size_mb = (
        hard.get("max_file_size_mb")
        if official_max_file_size_mb is None
        else official_max_file_size_mb
    )
    quality_page_tolerance = int(quality.get("body_page_tolerance", 0))
    if require_abstract_fill_ratio is None:
        require_abstract_fill_ratio = any(
            value is not None
            for value in (target_abstract_fill_ratio, max_abstract_fill_ratio)
        )
    if require_reference_section is None:
        require_reference_section = bool(
            hard.get("require_reference_section", quality.get("require_reference_section", False))
        )

    body_text = "\n".join(_body_document_texts(doc, appendix_heading_patterns))
    units = _content_units(body_text)
    equations = len(doc._element.findall(f".//{qn('m:oMath')}"))
    total_figures = len(doc._element.findall(f".//{qn('a:blip')}"))
    total_tables = len(doc.tables)
    for label, excluded, total in (
        ("excluded_figure_objects", excluded_figure_objects, total_figures),
        ("excluded_table_objects", excluded_table_objects, total_tables),
    ):
        if isinstance(excluded, bool) or not isinstance(excluded, int) or not 0 <= excluded <= total:
            errors.append(f"{label} 必须是 0 到实测对象数 {total} 之间的整数")
    valid_figure_exclusion = (
        not isinstance(excluded_figure_objects, bool)
        and isinstance(excluded_figure_objects, int)
        and 0 <= excluded_figure_objects <= total_figures
    )
    valid_table_exclusion = (
        not isinstance(excluded_table_objects, bool)
        and isinstance(excluded_table_objects, int)
        and 0 <= excluded_table_objects <= total_tables
    )
    figures = total_figures - excluded_figure_objects if valid_figure_exclusion else total_figures
    tables = total_tables - excluded_table_objects if valid_table_exclusion else total_tables
    for label, enabled in (
        ("check_figure_numbering", check_figure_numbering),
        ("check_table_numbering", check_table_numbering),
        ("check_numeric_reference_bijection", check_numeric_reference_bijection),
    ):
        if not isinstance(enabled, bool):
            errors.append(f"{label} 必须为布尔值")
    count_metrics = (
        ("正文约", units, "min_content_units", min_content_units, "字词单位"),
        ("可编辑公式", equations, "min_equations", min_equations, "个"),
        ("图", figures, "min_figures", min_figures, "幅"),
        ("表", tables, "min_tables", min_tables, "个"),
    )
    for label, actual, key, quality_minimum, unit in count_metrics:
        hard_minimum = hard.get(key)
        if hard_minimum is not None and actual < hard_minimum:
            errors.append(f"{label} {actual}{unit}，低于当届官方硬约束 {hard_minimum}{unit}")
        elif quality_minimum is not None and actual < quality_minimum:
            errors.append(f"预警：{label} {actual}{unit}，低于项目质量目标 {quality_minimum}{unit}")

    if check_figure_numbering is True:
        errors.extend(_numbered_object_issues(doc, figure_labels, figures, "图"))
    if check_table_numbering is True:
        errors.extend(_numbered_object_issues(doc, table_labels, tables, "表"))
    errors.extend(
        _reference_issues(
            texts,
            reference_headings,
            require_reference_section,
            check_numeric_reference_bijection is True,
        )
    )

    body_metric_required = any(
        hard.get(key) is not None for key in ("min_body_pages", "max_body_pages")
    )
    quality_body_configured = any(
        value is not None
        for value in (target_pages, quality.get("min_body_pages"), quality.get("max_body_pages"))
    )
    if rendered_body_pages is None and (body_metric_required or (require_rendered_pages and quality_body_configured)):
        prefix = "" if body_metric_required else "预警："
        errors.append(f"{prefix}未提供正文渲染页数，无法核验已配置的正文页数约束")
    elif rendered_body_pages is not None:
        if target_pages is not None and abs(rendered_body_pages - target_pages) > quality_page_tolerance:
            errors.append(
                f"预警：渲染后正文共 {rendered_body_pages} 页，未达到项目质量目标 "
                f"{target_pages}±{quality_page_tolerance} 页"
            )
        quality_min_pages = quality.get("min_body_pages")
        quality_max_pages = quality.get("max_body_pages")
        if quality_min_pages is not None and rendered_body_pages < quality_min_pages:
            errors.append(f"预警：渲染后正文共 {rendered_body_pages} 页，低于项目质量下限 {quality_min_pages} 页")
        if quality_max_pages is not None and rendered_body_pages > quality_max_pages:
            errors.append(f"预警：渲染后正文共 {rendered_body_pages} 页，超过项目质量上限 {quality_max_pages} 页")
        official_min_pages = hard.get("min_body_pages")
        if official_min_pages is not None and rendered_body_pages < official_min_pages:
            errors.append(
                f"渲染后正文共 {rendered_body_pages} 页，低于当届官方正文下限 {official_min_pages} 页"
            )
        if official_max_pages is not None and rendered_body_pages > official_max_pages:
            errors.append(
                f"渲染后正文共 {rendered_body_pages} 页，超过当届正文官方上限 "
                f"{official_max_pages} 页"
            )

    total_page_constraints = {
        "min_total_pages": hard.get("min_total_pages"),
        "max_total_pages": hard.get("max_total_pages"),
    }
    if rendered_pages is None and any(value is not None for value in total_page_constraints.values()):
        errors.append("未提供 PDF 总页数，无法核验当届官方总页数约束")
    elif rendered_pages is not None:
        if total_page_constraints["min_total_pages"] is not None and rendered_pages < total_page_constraints["min_total_pages"]:
            errors.append(f"渲染 PDF 共 {rendered_pages} 页，低于当届官方总页数下限 {total_page_constraints['min_total_pages']} 页")
        if total_page_constraints["max_total_pages"] is not None and rendered_pages > total_page_constraints["max_total_pages"]:
            errors.append(f"渲染 PDF 共 {rendered_pages} 页，超过当届官方总页数上限 {total_page_constraints['max_total_pages']} 页")

    if electronic_file_size_bytes is None:
        if official_max_file_size_mb is not None:
            errors.append("未提供电子版论文文件大小，无法核验当届官方文件大小约束")
        elif require_electronic_file_size:
            errors.append("预警：项目要求检查电子版论文文件大小，但未提供实测字节数")
    elif official_max_file_size_mb is not None:
        limit = official_max_file_size_mb * 1024 * 1024
        if electronic_file_size_bytes > limit:
            size_mb = electronic_file_size_bytes / (1024 * 1024)
            errors.append(
                f"电子版论文文件为 {size_mb:.2f}MB，超过当届官方上限 "
                f"{official_max_file_size_mb}MB"
            )

    hard_abstract_min = hard.get("abstract_fill_min")
    hard_abstract_max = hard.get("abstract_fill_max")
    if abstract_fill_ratio is None:
        if hard_abstract_min is not None or hard_abstract_max is not None:
            errors.append("未提供摘要页渲染填充率，无法核验当届官方摘要版面约束")
        elif require_abstract_fill_ratio:
            errors.append("预警：未提供摘要页渲染填充率，无法核验项目摘要版面目标")
    else:
        if abstract_fill_ratio > 1:
            errors.append("摘要渲染内容超过一页可用区域")
        if hard_abstract_min is not None and abstract_fill_ratio < hard_abstract_min:
            errors.append(f"摘要页有效区域填充率约 {abstract_fill_ratio:.0%}，低于当届官方下限 {hard_abstract_min:.0%}")
        if hard_abstract_max is not None and abstract_fill_ratio > hard_abstract_max:
            errors.append(f"摘要页有效区域填充率约 {abstract_fill_ratio:.0%}，超过当届官方上限 {hard_abstract_max:.0%}")
        if target_abstract_fill_ratio is not None and abstract_fill_ratio < target_abstract_fill_ratio:
            errors.append(
                f"预警：摘要页有效区域填充率约 {abstract_fill_ratio:.0%}，低于 "
                f"{target_abstract_fill_ratio:.0%} 的项目质量目标"
            )
        if max_abstract_fill_ratio is not None and abstract_fill_ratio > max_abstract_fill_ratio:
            errors.append(
                f"预警：摘要页有效区域填充率约 {abstract_fill_ratio:.0%}，超过 "
                f"{max_abstract_fill_ratio:.0%} 的项目质量上限"
            )
    return errors


def _is_within(path, parent):
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def save_document(doc, project_root, filename="论文候选稿.docx", contest="cumcm", overwrite=False,
                  release_approved=False):
    """校验后原子保存候选稿；最终论文只能由渲染后的发布门禁生成。"""
    project = Path(project_root).resolve()
    if _is_within(project, SKILL_ROOT):
        raise ValueError("PROJECT_ROOT 不能位于 SKILL_ROOT 内部")
    output = (project / filename).resolve()
    if not _is_within(output, project):
        raise ValueError("论文输出必须位于 PROJECT_ROOT 内部")
    if _is_within(output, SKILL_ROOT):
        raise ValueError("论文输出不能位于 SKILL_ROOT 内部")
    if output.name == "完整论文.docx" and not release_approved:
        raise ValueError(
            "完整论文.docx 只能在 Word/PDF 渲染后由 paper_release_gate.py 发布；"
            "请先保存为论文候选稿.docx"
        )
    issues = validate_paper_structure(doc, contest)
    errors = [issue for issue in issues if not issue.startswith("预警：")]
    if errors:
        raise ValueError("论文结构校验失败: " + "；".join(errors))
    if output.exists() and not overwrite:
        raise FileExistsError(f"输出已存在，未覆盖: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    doc.save(temporary)
    os.replace(temporary, output)
    return output


if __name__ == "__main__":
    print("请在 PROJECT_ROOT 的论文构建脚本中导入 paper_format；工具自检请运行 self_check.py。")

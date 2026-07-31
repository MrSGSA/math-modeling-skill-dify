#!/usr/bin/env python3
"""One-command sanity check for DOCX paper helpers."""

import importlib.util
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from lxml import etree

ROOT = Path(os.environ.get("MATH_MODELING_SKILL_ROOT", Path(__file__).resolve().parents[3]))
SCRIPTS = ROOT / "tools" / "docx" / "scripts"
TEMPLATE = ROOT / "references" / "roles" / "paper-writer" / "references" / "论文模板.docx"
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check_env():
    result = subprocess.run([sys.executable, str(SCRIPTS / "check_env.py")], check=False)
    require(result.returncode == 0, "check_env.py failed")


def check_formula():
    equations = load("equations")
    root = etree.fromstring(equations.latex2omml(r"\frac{1}{n}\sum_{i=1}^{n}x_i^2"))
    require(root.xpath(".//m:f", namespaces={"m": M_NS}), "fraction OMML missing")
    require(root.xpath(".//m:sSubSup", namespaces={"m": M_NS}), "sub/sup OMML missing")


def check_three_line_table():
    fmt = load("paper_format")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "table.docx"
        doc = fmt.new_document()
        fmt.title(doc, "论文题目")
        fmt.abstract_title(doc)
        fmt.body(doc, "摘要正文。")
        fmt.keywords(doc, "优化；预测")
        fmt.heading1(doc, "一、问题重述")
        fmt.heading2(doc, "1.1 问题背景")
        fmt.body(doc, "这是正文。")
        require(fmt.count_chinese_chars(doc) >= 6, "Chinese character count failed")
        fmt.equation(doc, r"x_i^2")
        placeholder, latex = fmt.equation_placeholder(doc, r"x_i^2")
        require(placeholder.startswith("EQ_"), "equation placeholder prefix failed")
        require(latex == r"x_i^2", "equation placeholder payload failed")
        fmt.three_line_table(doc, [["符号", "说明", "单位"], ["x", "变量", "-"]])
        fmt.figure_caption(doc, "图1 测试图")
        fmt.page_break(doc)
        doc.save(path)
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "office" / "validate.py"), str(path)],
            check=False,
        )
        require(result.returncode == 0, "generated DOCX validation failed")
        with zipfile.ZipFile(path) as zf:
            document = etree.fromstring(zf.read("word/document.xml"))
        keyword_paras = document.xpath("//w:p[.//w:t='关键词：']", namespaces={"w": W_NS})
        require(
            keyword_paras
            and not "".join(
                keyword_paras[0].getprevious().xpath(
                    ".//w:t/text()", namespaces={"w": W_NS}
                )
            ),
            "keyword layout check failed",
        )
        chapter_break = document.xpath(
            "//w:p[.//w:t='一、问题重述']/w:pPr/w:pageBreakBefore",
            namespaces={"w": W_NS},
        )
        require(chapter_break, "chapter page-break check failed")
        heading2_size = document.xpath(
            "//w:p[.//w:t='1.1 问题背景']//w:sz/@w:val",
            namespaces={"w": W_NS},
        )
        require(heading2_size == ["28"], "heading level-2 size check failed")
        tbl_borders = document.xpath("//w:tbl[1]/w:tblPr/w:tblBorders/*", namespaces={"w": W_NS})
        vals = {node.tag.rsplit("}", 1)[1]: node.get(f"{{{W_NS}}}val") for node in tbl_borders}
        require(vals.get("top") == "single", "three-line table top border failed")
        require(vals.get("bottom") == "single", "three-line table bottom border failed")
        require(vals.get("insideV") == "nil", "three-line table vertical border failed")
        header_bottom = document.xpath(
            "//w:tbl[1]/w:tr[1]/w:tc[1]/w:tcPr/w:tcBorders/w:bottom/@w:val",
            namespaces={"w": W_NS},
        )
        require(header_bottom == ["single"], "three-line table header border failed")


def check_template_validate():
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "office" / "validate.py"), str(TEMPLATE)],
        check=False,
    )
    require(result.returncode == 0, "template validation failed")


def main():
    check_env()
    check_formula()
    check_three_line_table()
    check_template_validate()
    print("self_check OK")


if __name__ == "__main__":
    main()

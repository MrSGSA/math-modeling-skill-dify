import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter
from pypdf.annotations import FreeText

SKILL_ROOT = Path(__file__).resolve().parents[3]

def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False

def _validated_paths(input_pdf_path, fields_json_path, output_pdf_path, overwrite):
    input_path = Path(input_pdf_path).resolve(strict=True)
    fields_path = Path(fields_json_path).resolve(strict=True)
    output_path = Path(output_pdf_path).resolve(strict=False)
    if not input_path.is_file() or not fields_path.is_file():
        raise ValueError("输入 PDF 和字段 JSON 必须是文件")
    if output_path.suffix.lower() != ".pdf":
        raise ValueError("输出文件必须使用 .pdf 扩展名")
    if output_path in {input_path, fields_path}:
        raise ValueError("输出路径不能与任何输入文件相同")
    if _is_within(output_path, SKILL_ROOT):
        raise ValueError("输出路径不能位于 SKILL_ROOT 内")
    if output_path.exists() and not overwrite:
        raise FileExistsError(f"输出已存在；如确需覆盖请显式传入 --overwrite: {output_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    return input_path, fields_path, output_path

def _publish_writer(writer, output_path: Path, expected_pages: int):
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{output_path.stem}.", suffix=".tmp.pdf", dir=output_path.parent
    )
    os.close(handle)
    temporary_path = Path(temporary_name)
    try:
        with temporary_path.open("wb") as stream:
            writer.write(stream)
        if len(PdfReader(str(temporary_path)).pages) != expected_pages:
            raise ValueError("输出 PDF 页数与输入不一致，拒绝发布")
        os.replace(temporary_path, output_path)
    finally:
        temporary_path.unlink(missing_ok=True)

def transform_from_image_coords(bbox, image_width, image_height, pdf_width, pdf_height):
    x_scale = pdf_width / image_width
    y_scale = pdf_height / image_height

    left = bbox[0] * x_scale
    right = bbox[2] * x_scale

    top = pdf_height - (bbox[1] * y_scale)
    bottom = pdf_height - (bbox[3] * y_scale)

    return left, bottom, right, top


def transform_from_pdf_coords(bbox, pdf_height):
    left = bbox[0]
    right = bbox[2]

    pypdf_top = pdf_height - bbox[1]      
    pypdf_bottom = pdf_height - bbox[3]   

    return left, pypdf_bottom, right, pypdf_top


def fill_pdf_form(input_pdf_path, fields_json_path, output_pdf_path,
                  overwrite=False):
    input_path, fields_path, output_path = _validated_paths(
        input_pdf_path, fields_json_path, output_pdf_path, overwrite
    )
    with fields_path.open(encoding="utf-8") as f:
        fields_data = json.load(f)
    if not isinstance(fields_data, dict):
        raise ValueError("字段 JSON 顶层必须是对象")
    if not isinstance(fields_data.get("form_fields"), list) or not isinstance(fields_data.get("pages"), list):
        raise ValueError("字段 JSON 必须包含 form_fields 与 pages 数组")

    reader = PdfReader(str(input_path))
    writer = PdfWriter()
    
    writer.append(reader)
    
    pdf_dimensions = {}
    for i, page in enumerate(reader.pages):
        mediabox = page.mediabox
        pdf_dimensions[i + 1] = [mediabox.width, mediabox.height]
    
    annotations = []
    for field in fields_data["form_fields"]:
        page_num = field["page_number"]

        page_info = next(p for p in fields_data["pages"] if p["page_number"] == page_num)
        pdf_width, pdf_height = pdf_dimensions[page_num]

        if "pdf_width" in page_info:
            transformed_entry_box = transform_from_pdf_coords(
                field["entry_bounding_box"],
                float(pdf_height)
            )
        else:
            image_width = page_info["image_width"]
            image_height = page_info["image_height"]
            transformed_entry_box = transform_from_image_coords(
                field["entry_bounding_box"],
                image_width, image_height,
                float(pdf_width), float(pdf_height)
            )
        
        if "entry_text" not in field or "text" not in field["entry_text"]:
            continue
        entry_text = field["entry_text"]
        text = entry_text["text"]
        if not text:
            continue
        
        font_name = entry_text.get("font", "Arial")
        font_size = str(entry_text.get("font_size", 14)) + "pt"
        font_color = entry_text.get("font_color", "000000")

        annotation = FreeText(
            text=text,
            rect=transformed_entry_box,
            font=font_name,
            font_size=font_size,
            font_color=font_color,
            border_color=None,
            background_color=None,
        )
        annotations.append(annotation)
        writer.add_annotation(page_number=page_num - 1, annotation=annotation)
        
    _publish_writer(writer, output_path, len(reader.pages))
    
    print(f"Successfully filled PDF form and saved to {output_path}")
    print(f"Added {len(annotations)} text annotations")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="向不可编辑 PDF 安全添加文本批注")
    parser.add_argument("input_pdf")
    parser.add_argument("fields_json")
    parser.add_argument("output_pdf")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        fill_pdf_form(
            args.input_pdf,
            args.fields_json,
            args.output_pdf,
            overwrite=args.overwrite,
        )
    except (OSError, ValueError, StopIteration, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

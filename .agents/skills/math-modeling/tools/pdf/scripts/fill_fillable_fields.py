import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from pypdf import PdfReader, PdfWriter

from extract_form_field_info import get_field_info

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

def fill_pdf_fields(input_pdf_path: str, fields_json_path: str,
                    output_pdf_path: str, overwrite: bool = False):
    input_path, fields_path, output_path = _validated_paths(
        input_pdf_path, fields_json_path, output_pdf_path, overwrite
    )
    with fields_path.open(encoding="utf-8") as f:
        fields = json.load(f)
    if not isinstance(fields, list):
        raise ValueError("字段 JSON 顶层必须是数组")
    fields_by_page = {}
    for field in fields:
        if "value" in field:
            field_id = field["field_id"]
            page = field["page"]
            if page not in fields_by_page:
                fields_by_page[page] = {}
            fields_by_page[page][field_id] = field["value"]
    
    reader = PdfReader(str(input_path))

    has_error = False
    field_info = get_field_info(reader)
    fields_by_ids = {f["field_id"]: f for f in field_info}
    for field in fields:
        existing_field = fields_by_ids.get(field["field_id"])
        if not existing_field:
            has_error = True
            print(f"ERROR: `{field['field_id']}` is not a valid field ID")
        elif field["page"] != existing_field["page"]:
            has_error = True
            print(f"ERROR: Incorrect page number for `{field['field_id']}` (got {field['page']}, expected {existing_field['page']})")
        else:
            if "value" in field:
                err = validation_error_for_field_value(existing_field, field["value"])
                if err:
                    print(err)
                    has_error = True
    if has_error:
        raise ValueError("字段值校验失败；未生成输出 PDF")

    writer = PdfWriter(clone_from=reader)
    for page, field_values in fields_by_page.items():
        writer.update_page_form_field_values(writer.pages[page - 1], field_values, auto_regenerate=False)

    writer.set_need_appearances_writer(True)
    
    _publish_writer(writer, output_path, len(reader.pages))


def validation_error_for_field_value(field_info, field_value):
    field_type = field_info["type"]
    field_id = field_info["field_id"]
    if field_type == "checkbox":
        checked_val = field_info["checked_value"]
        unchecked_val = field_info["unchecked_value"]
        if field_value != checked_val and field_value != unchecked_val:
            return f'ERROR: Invalid value "{field_value}" for checkbox field "{field_id}". The checked value is "{checked_val}" and the unchecked value is "{unchecked_val}"'
    elif field_type == "radio_group":
        option_values = [opt["value"] for opt in field_info["radio_options"]]
        if field_value not in option_values:
            return f'ERROR: Invalid value "{field_value}" for radio group field "{field_id}". Valid values are: {option_values}' 
    elif field_type == "choice":
        choice_values = [opt["value"] for opt in field_info["choice_options"]]
        if field_value not in choice_values:
            return f'ERROR: Invalid value "{field_value}" for choice field "{field_id}". Valid values are: {choice_values}'
    return None


def monkeypatch_pydpf_method():
    from pypdf.generic import DictionaryObject
    from pypdf.constants import FieldDictionaryAttributes

    original_get_inherited = DictionaryObject.get_inherited

    def patched_get_inherited(self, key: str, default = None):
        result = original_get_inherited(self, key, default)
        if key == FieldDictionaryAttributes.Opt:
            if isinstance(result, list) and all(isinstance(v, list) and len(v) == 2 for v in result):
                result = [r[0] for r in result]
        return result

    DictionaryObject.get_inherited = patched_get_inherited


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="填写可编辑 PDF 表单并安全写入新文件")
    parser.add_argument("input_pdf")
    parser.add_argument("field_values_json")
    parser.add_argument("output_pdf")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    monkeypatch_pydpf_method()
    try:
        fill_pdf_fields(
            args.input_pdf,
            args.field_values_json,
            args.output_pdf,
            overwrite=args.overwrite,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

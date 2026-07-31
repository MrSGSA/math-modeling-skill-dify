import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw
from safe_paths import input_file, output_file




def create_validation_image(page_number, fields_json_path, input_path,
                            output_path, overwrite=False):
    fields_path = input_file(fields_json_path)
    input_path = input_file(input_path)
    output_path = output_file(
        output_path,
        inputs=[fields_path, input_path],
        overwrite=overwrite,
        suffixes={".png", ".jpg", ".jpeg"},
    )
    with fields_path.open(encoding="utf-8") as f:
        data = json.load(f)
        if not isinstance(data, dict) or not isinstance(data.get("form_fields"), list):
            raise ValueError("字段 JSON 必须包含 form_fields 数组")

        with Image.open(input_path) as source:
            img = source.copy()
        draw = ImageDraw.Draw(img)
        num_boxes = 0
        
        for field in data["form_fields"]:
            if field["page_number"] == page_number:
                entry_box = field['entry_bounding_box']
                label_box = field['label_bounding_box']
                draw.rectangle(entry_box, outline='red', width=2)
                draw.rectangle(label_box, outline='blue', width=2)
                num_boxes += 2
        
        handle, temporary_name = tempfile.mkstemp(
            prefix=f".{output_path.stem}.", suffix=f".tmp{output_path.suffix}",
            dir=output_path.parent,
        )
        os.close(handle)
        temporary_path = Path(temporary_name)
        try:
            img.save(temporary_path)
            os.replace(temporary_path, output_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        print(f"Created validation image at {output_path} with {num_boxes} bounding boxes")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="在页面图片上绘制表单框以供人工核验")
    parser.add_argument("page_number", type=int)
    parser.add_argument("fields_json")
    parser.add_argument("input_image")
    parser.add_argument("output_image")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        create_validation_image(
            args.page_number,
            args.fields_json,
            args.input_image,
            args.output_image,
            overwrite=args.overwrite,
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

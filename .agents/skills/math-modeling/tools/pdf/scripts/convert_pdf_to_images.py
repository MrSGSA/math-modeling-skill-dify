import argparse
import os
import sys
import tempfile
from pathlib import Path

from pdf2image import convert_from_path
from PIL import Image
from safe_paths import input_file, output_directory




def convert(pdf_path, output_dir, max_dim=1000, overwrite=False):
    pdf_path = input_file(pdf_path)
    output_dir = output_directory(
        output_dir,
        input_paths=[pdf_path],
        overwrite=overwrite,
    )
    if not isinstance(max_dim, int) or max_dim <= 0:
        raise ValueError("max_dim 必须为正整数")
    images = convert_from_path(str(pdf_path), dpi=200)

    with tempfile.TemporaryDirectory(prefix="pdf-pages-", dir=output_dir.parent) as tmp:
        staging = Path(tmp)
        published_names = []
        for i, image in enumerate(images):
            width, height = image.size
            if width > max_dim or height > max_dim:
                scale_factor = min(max_dim / width, max_dim / height)
                new_width = int(width * scale_factor)
                new_height = int(height * scale_factor)
                image = image.resize((new_width, new_height))

            image_name = f"page_{i+1}.png"
            image_path = staging / image_name
            image.save(image_path)
            published_names.append(image_name)

        output_dir.mkdir(parents=True, exist_ok=True)
        if overwrite:
            for stale in output_dir.glob("page_*.png"):
                if stale.name not in published_names:
                    stale.unlink()
        for image_name in published_names:
            os.replace(staging / image_name, output_dir / image_name)
            with Image.open(output_dir / image_name) as check:
                size = check.size
            print(f"Saved {image_name} (size: {size})")

    print(f"Converted {len(images)} pages to PNG images")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="把 PDF 分页渲染为 PNG")
    parser.add_argument("input_pdf")
    parser.add_argument("output_directory")
    parser.add_argument("--max-dim", type=int, default=1000)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    try:
        convert(
            args.input_pdf,
            args.output_directory,
            max_dim=args.max_dim,
            overwrite=args.overwrite,
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

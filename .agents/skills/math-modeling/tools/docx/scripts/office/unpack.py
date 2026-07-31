"""Unpack Office files (DOCX, PPTX, XLSX) for editing.

Extracts the ZIP archive, pretty-prints XML files, and optionally:
- Merges adjacent runs with identical formatting (DOCX only)
- Simplifies adjacent tracked changes from same author (DOCX only)

Usage:
    python unpack.py <office_file> <output_dir> [options]

Examples:
    python unpack.py document.docx unpacked/
    python unpack.py presentation.pptx unpacked/
    python unpack.py document.docx unpacked/ --merge-runs false
"""

import argparse
import sys
import tempfile
import zipfile
from pathlib import Path

import defusedxml.minidom

from helpers.merge_runs import merge_runs as do_merge_runs
from helpers.safe_zip import UnsafeZipError, safe_extract_zip
from helpers.simplify_redlines import simplify_redlines as do_simplify_redlines

SMART_QUOTE_REPLACEMENTS = {
    "\u201c": "&#x201C;",  
    "\u201d": "&#x201D;",  
    "\u2018": "&#x2018;",  
    "\u2019": "&#x2019;",  
}


def unpack(
    input_file: str,
    output_directory: str,
    merge_runs: bool = True,
    simplify_redlines: bool = True,
) -> tuple[None, str]:
    input_path = Path(input_file)
    output_path = Path(output_directory)
    suffix = input_path.suffix.lower()

    if not input_path.is_file():
        return None, f"Error: {input_file} does not exist or is not a file"

    if suffix not in {".docx", ".pptx", ".xlsx"}:
        return None, f"Error: {input_file} must be a .docx, .pptx, or .xlsx file"
    if output_path.exists():
        if not output_path.is_dir():
            return None, f"Error: {output_directory} exists and is not a directory"
        if any(output_path.iterdir()):
            return None, f"Error: {output_directory} must be empty; refusing to overwrite"

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="office-unpack-", dir=str(output_path.parent)
        ) as temp_dir:
            stage = Path(temp_dir) / "payload"
            stage.mkdir()
            with zipfile.ZipFile(input_path, "r") as zf:
                safe_extract_zip(zf, stage)

            xml_files = list(stage.rglob("*.xml")) + list(stage.rglob("*.rels"))
            for xml_file in xml_files:
                _pretty_print_xml(xml_file)

            message = f"Unpacked {input_file} ({len(xml_files)} XML files)"
            if suffix == ".docx":
                if simplify_redlines:
                    simplify_count, _ = do_simplify_redlines(str(stage))
                    message += f", simplified {simplify_count} tracked changes"
                if merge_runs:
                    merge_count, _ = do_merge_runs(str(stage))
                    message += f", merged {merge_count} runs"

            for xml_file in xml_files:
                _escape_smart_quotes(xml_file)

            if output_path.exists():
                output_path.rmdir()
            stage.replace(output_path)
            return None, message

    except zipfile.BadZipFile:
        return None, f"Error: {input_file} is not a valid Office file"
    except UnsafeZipError as exc:
        return None, f"Error: unsafe Office ZIP: {exc}"
    except Exception as e:
        return None, f"Error unpacking: {e}"


def _pretty_print_xml(xml_file: Path) -> None:
    content = xml_file.read_text(encoding="utf-8")
    dom = defusedxml.minidom.parseString(content)
    xml_file.write_bytes(dom.toprettyxml(indent="  ", encoding="utf-8"))


def _escape_smart_quotes(xml_file: Path) -> None:
    content = xml_file.read_text(encoding="utf-8")
    for char, entity in SMART_QUOTE_REPLACEMENTS.items():
        content = content.replace(char, entity)
    xml_file.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Unpack an Office file (DOCX, PPTX, XLSX) for editing"
    )
    parser.add_argument("input_file", help="Office file to unpack")
    parser.add_argument("output_directory", help="Output directory")
    parser.add_argument(
        "--merge-runs",
        type=lambda x: x.lower() == "true",
        default=True,
        metavar="true|false",
        help="Merge adjacent runs with identical formatting (DOCX only, default: true)",
    )
    parser.add_argument(
        "--simplify-redlines",
        type=lambda x: x.lower() == "true",
        default=True,
        metavar="true|false",
        help="Merge adjacent tracked changes from same author (DOCX only, default: true)",
    )
    args = parser.parse_args()

    _, message = unpack(
        args.input_file,
        args.output_directory,
        merge_runs=args.merge_runs,
        simplify_redlines=args.simplify_redlines,
    )
    print(message)

    if "Error" in message:
        sys.exit(1)

#!/usr/bin/env python3
"""Batch-convert documents to MinerU Markdown without modifying source files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence
from urllib.parse import unquote

REPORT_NAME = "batch_report.json"
UPLOAD_LIST_NAME = "dify_upload_list.txt"
DIFY_BUNDLE_SUFFIX = ".dify-mm.zip"
SCRIPT_PATH = Path(__file__).resolve()
SKILL_ROOT = SCRIPT_PATH.parent
IS_INSTALLED_SKILL = False
for candidate in SCRIPT_PATH.parents:
    if candidate.name == "math-modeling" and candidate.parent.name == "skills":
        SKILL_ROOT = candidate
        IS_INSTALLED_SKILL = True
        break
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
SUPPORTED_SUFFIXES = {
    ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff",
    ".docx", ".pptx", ".xlsx",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_stem(name: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return re.sub(r"\s+", "_", value or "document")[:72]


def output_stems(documents: list[Path]) -> dict[Path, str]:
    """Create stable output names, including hashes only for real collisions."""
    base_names = [safe_stem(document.stem) for document in documents]
    base_counts: dict[str, int] = {}
    for name in base_names:
        key = name.casefold()
        base_counts[key] = base_counts.get(key, 0) + 1

    provisional: list[str] = []
    for document, base in zip(documents, base_names):
        if base_counts[base.casefold()] > 1:
            base += f"_{document.suffix.lower().lstrip('.')}"
        provisional.append(base)
    provisional_counts: dict[str, int] = {}
    for name in provisional:
        key = name.casefold()
        provisional_counts[key] = provisional_counts.get(key, 0) + 1

    result: dict[Path, str] = {}
    for document, name in zip(documents, provisional):
        if provisional_counts[name.casefold()] > 1:
            name += f"_{sha256_file(document)[:8]}"
        result[document] = name
    return result


def discover_documents(input_dir: Path, recursive: bool = True) -> list[Path]:
    paths = input_dir.rglob("*") if recursive else input_dir.glob("*")
    return sorted(
        (p.resolve() for p in paths if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES),
        key=lambda p: str(p).casefold(),
    )


def discover_pdfs(input_dir: Path, recursive: bool = True) -> list[Path]:
    """Backward-compatible PDF-only discovery helper."""
    return [p for p in discover_documents(input_dir, recursive) if p.suffix.lower() == ".pdf"]


def resolve_executable(command: str) -> str | None:
    candidate = Path(command).expanduser()
    if candidate.parent != Path(".") and candidate.exists():
        return str(candidate.resolve())
    return shutil.which(command)


def build_mineru_command(
    executable: str, document: Path, output: Path, backend: str,
    effort: str = "medium", image_analysis: bool = False,
) -> list[str]:
    command = [executable, "-p", str(document), "-o", str(output)]
    if backend != "auto":
        command += ["-b", backend]
    if backend in {"auto", "hybrid-engine"}:
        command += ["--effort", effort]
    command += ["--formula", "true", "--table", "true"]
    if image_analysis:
        command += ["--image-analysis", "true"]
    return command


IMAGE_MARKDOWN_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<target>[^\n)]*)\)",
    flags=re.IGNORECASE,
)
VISUAL_DETAILS_RE = re.compile(
    r"<details>\s*<summary>(.*?)</summary>\s*(.*?)\s*</details>",
    flags=re.IGNORECASE | re.DOTALL,
)


def normalize_visual_details(markdown: str) -> str:
    """Turn MinerU image-analysis details blocks into ordinary Markdown text."""
    def replace_details(match: re.Match[str]) -> str:
        summary = match.group(1).strip().casefold()
        body = match.group(2).strip()
        label = "图表内容" if "chart" in summary else "图片内容"
        return f"\n\n### {label}\n\n{body}\n\n" if body else "\n"

    return VISUAL_DETAILS_RE.sub(replace_details, markdown)


def textualize_images(markdown: str) -> str:
    """Keep MinerU visual descriptions as text and remove dead image links."""

    def replace_image(match: re.Match[str]) -> str:
        alt = match.group("alt").strip()
        return f"\n\n> 图片说明：{alt}\n\n" if alt else "\n"

    markdown = normalize_visual_details(markdown)
    markdown = IMAGE_MARKDOWN_RE.sub(replace_image, markdown)
    return re.sub(r"\n{4,}", "\n\n\n", markdown).strip() + "\n"


def markdown_image_target(match: re.Match[str]) -> str:
    """Return a MinerU image target without optional Markdown title syntax."""
    target = match.group("target").strip()
    if target.startswith("<") and target.endswith(">"):
        target = target[1:-1].strip()
    else:
        title = re.match(r'^(.*?)(?:\s+["\'].*["\'])$', target)
        if title:
            target = title.group(1).strip()
    return unquote(target)


def resolve_local_image(markdown_path: Path, mineru_root: Path, reference: str) -> Path | None:
    """Resolve a safe local Markdown image reference inside MinerU output."""
    if not reference or re.match(r"^[a-z][a-z0-9+.-]*:", reference, re.IGNORECASE):
        return None
    normalized = reference.replace("\\", "/").split("#", 1)[0].split("?", 1)[0]
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or ".." in pure.parts:
        return None
    candidate = (markdown_path.parent / Path(*pure.parts)).resolve()
    root = mineru_root.resolve()
    if candidate != root and root not in candidate.parents:
        return None
    if candidate.is_file() and candidate.suffix.lower() in IMAGE_SUFFIXES:
        return candidate

    # MinerU normally writes paths relative to the Markdown file. A basename
    # fallback keeps bundles usable across small output-layout changes.
    matches = [
        path.resolve() for path in mineru_root.rglob(pure.name)
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    return matches[0] if len(matches) == 1 else None


def split_text(text: str, max_chars: int) -> list[str]:
    """Split text without dropping formulas or punctuation."""
    text = text.strip()
    if not text:
        return []
    pieces = re.split(r"(?<=[。！？!?；;])\s+|\n+", text)
    chunks: list[str] = []
    current = ""
    for piece in (item.strip() for item in pieces if item.strip()):
        if len(piece) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(piece[i:i + max_chars] for i in range(0, len(piece), max_chars))
        elif not current:
            current = piece
        elif len(current) + 1 + len(piece) <= max_chars:
            current += "\n" + piece
        else:
            chunks.append(current)
            current = piece
    if current:
        chunks.append(current)
    return chunks


def build_parent_child_chunks(
    markdown: str,
    image_by_reference: dict[str, dict[str, Any]],
    source_name: str,
    parent_max_chars: int = 1800,
    child_max_chars: int = 512,
    max_images: int = 6,
) -> list[dict[str, Any]]:
    """Associate extracted images with nearby Markdown and make Dify chunks."""
    markdown = normalize_visual_details(markdown)
    blocks = re.split(r"\n\s*\n", markdown)
    groups: list[dict[str, Any]] = []
    current_text: list[str] = []
    current_images: list[dict[str, Any]] = []
    heading = ""

    def flush() -> None:
        nonlocal current_text, current_images
        content = "\n\n".join(item for item in current_text if item).strip()
        if not content and current_images:
            content = f"{source_name} 中的视觉内容"
        if not content and not current_images:
            return
        image_sets = [
            current_images[i:i + max_images]
            for i in range(0, len(current_images), max_images)
        ] or [[]]
        children = split_text(content, child_max_chars) or [content]
        for images in image_sets:
            groups.append({
                "parent_content": content,
                "child_contents": children,
                "images": images,
            })
        current_text, current_images = [], []

    for raw_block in blocks:
        raw_block = raw_block.strip()
        if not raw_block:
            continue
        headings = re.findall(r"(?m)^#{1,6}\s+.+$", raw_block)
        if headings:
            heading = headings[-1].strip()
        images: list[dict[str, Any]] = []

        def replace_image(match: re.Match[str]) -> str:
            reference = markdown_image_target(match)
            asset = image_by_reference.get(reference)
            alt = match.group("alt").strip()
            if asset:
                occurrence = dict(asset)
                occurrence["alt"] = alt
                occurrence["source_reference"] = reference
                if occurrence["path"] not in {item["path"] for item in images}:
                    images.append(occurrence)
            return f"[图片说明：{alt}]" if alt else ""

        clean = IMAGE_MARKDOWN_RE.sub(replace_image, raw_block).strip()
        clean_parts = split_text(clean, parent_max_chars) or ([""] if images else [])
        for part_index, part in enumerate(clean_parts):
            part_images = images if part_index == 0 else []
            context = part
            if context and heading and not context.lstrip().startswith("#") and not current_text:
                context = heading + "\n\n" + context
            projected = len("\n\n".join(current_text + ([context] if context else [])))
            distinct = {item["path"] for item in current_images + part_images}
            if current_text and (projected > parent_max_chars or len(distinct) > max_images):
                flush()
                if context and heading and not context.lstrip().startswith("#"):
                    context = heading + "\n\n" + context
            if context:
                current_text.append(context)
            for image in part_images:
                if image["path"] not in {item["path"] for item in current_images}:
                    current_images.append(image)
    flush()
    return groups


def build_multimodal_bundle(
    markdown_path: Path,
    source_document: Path,
    destination: Path,
    mineru_root: Path | None = None,
) -> dict[str, Any]:
    """Create one safe, self-contained MinerU-to-Dify multimodal bundle."""
    mineru_root = (mineru_root or markdown_path.parent).resolve()
    markdown = markdown_path.read_text(encoding="utf-8")
    source_digest = sha256_file(source_document)
    source_label = safe_stem(source_document.stem)[:32]
    references: list[str] = []
    for match in IMAGE_MARKDOWN_RE.finditer(markdown):
        reference = markdown_image_target(match)
        if reference not in references:
            references.append(reference)

    resolved: dict[str, Path] = {}
    missing: list[str] = []
    for reference in references:
        path = resolve_local_image(markdown_path, mineru_root, reference)
        if path:
            resolved[reference] = path
        elif not re.match(r"^[a-z][a-z0-9+.-]*:", reference, re.IGNORECASE):
            missing.append(reference)

    # Preserve extracted figures that MinerU placed beside the Markdown even
    # if a particular release omitted a Markdown reference for one of them.
    image_dir = markdown_path.parent / "images"
    unreferenced: list[Path] = []
    if image_dir.is_dir():
        known = set(resolved.values())
        unreferenced = sorted(
            (p.resolve() for p in image_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES and p.resolve() not in known),
            key=lambda path: str(path).casefold(),
        )

    unique_paths: list[Path] = []
    for path in list(resolved.values()) + unreferenced:
        if path not in unique_paths:
            unique_paths.append(path)
    asset_by_path: dict[Path, dict[str, Any]] = {}
    for index, path in enumerate(unique_paths, 1):
        suffix = path.suffix.lower() or ".png"
        image_digest = sha256_file(path)
        image_id = f"{source_digest[:8]}-img-{index:04d}-{image_digest[:10]}"
        archive_path = (
            f"images/{source_label}_{source_digest[:8]}"
            f"__img_{index:04d}_{image_digest[:10]}{suffix}"
        )
        asset_by_path[path] = {
            "image_id": image_id,
            "path": archive_path,
            "sha256": image_digest,
            "size_bytes": path.stat().st_size,
        }
    image_by_reference = {
        reference: asset_by_path[path] for reference, path in resolved.items()
    }
    all_chunks = build_parent_child_chunks(markdown, image_by_reference, source_document.name)
    # This bundle feeds a dedicated image index. Text-only chunks belong in the
    # ordinary text knowledge base and are deliberately omitted here.
    chunks = [chunk for chunk in all_chunks if chunk["images"]]
    if unreferenced:
        assets = [
            {**asset_by_path[path], "alt": "MinerU 提取的未引用图片", "source_reference": ""}
            for path in unreferenced
        ]
        for index in range(0, len(assets), 6):
            content = f"{source_document.name} 中 MinerU 提取的其他视觉内容"
            chunks.append({
                "parent_content": content,
                "child_contents": [content],
                "images": assets[index:index + 6],
            })

    def rewrite_image(match: re.Match[str]) -> str:
        reference = markdown_image_target(match)
        asset = image_by_reference.get(reference)
        return f"![{match.group('alt')}]({asset['path']})" if asset else match.group(0)

    rewritten = IMAGE_MARKDOWN_RE.sub(rewrite_image, markdown)
    manifest = {
        "schema": "mineru-dify-multimodal-bundle",
        "schema_version": "1.0",
        "purpose": "image-retrieval-only",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "name": source_document.name,
            "sha256": source_digest,
            "size_bytes": source_document.stat().st_size,
        },
        "document_markdown": "document.md",
        "image_count": len(unique_paths),
        "chunk_count": len(chunks),
        "text_only_chunks_omitted": len(all_chunks) - len(chunks),
        "missing_image_references": missing,
        "chunks": [
            {"id": f"chunk-{index:04d}", **chunk}
            for index, chunk in enumerate(chunks, 1)
        ],
    }

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("document.md", rewritten.encode("utf-8"))
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
            )
            for path, asset in asset_by_path.items():
                archive.write(path, asset["path"])
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return manifest


def markdown_files(directory: Path) -> list[Path]:
    return sorted(p.resolve() for p in directory.rglob("*.md") if p.is_file())


def previous_records(output_dir: Path) -> dict[str, dict[str, Any]]:
    try:
        data = json.loads((output_dir / REPORT_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {r["source"]: r for r in data.get("records", []) if r.get("source")}


def write_report(output: Path, source: Path, args: argparse.Namespace, records: list[dict[str, Any]]) -> None:
    counts: dict[str, int] = {}
    for record in records:
        status = record["status"]
        counts[status] = counts.get(status, 0) + 1
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "input_directory": str(source),
        "output_directory": str(output),
        "engine": "MinerU",
        "mineru_command": args.mineru_command,
        "backend": args.backend,
        "summary": {"total": len(records), "by_status": counts},
        "records": records,
    }
    (output / REPORT_NAME).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    upload = sorted({
        item
        for record in records if record["status"] in {"success", "skipped"}
        for item in record.get("markdown_files", [])
    }, key=str.casefold)
    (output / UPLOAD_LIST_NAME).write_text(
        "\n".join(upload) + ("\n" if upload else ""), encoding="utf-8"
    )


def convert_one(document: Path, target: Path, executable: str, args: argparse.Namespace) -> dict[str, Any]:
    target.mkdir(parents=True, exist_ok=True)
    command = build_mineru_command(
        executable, document, target, args.backend, args.effort,
        getattr(args, "image_analysis", False),
    )
    log = target / "conversion.log"
    started = time.monotonic()
    timeout = args.timeout_minutes * 60 if args.timeout_minutes else None
    try:
        done = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, check=False
        )
        log.write_text(
            "COMMAND\n" + subprocess.list2cmdline(command) + "\n\nSTDOUT\n"
            + done.stdout + "\n\nSTDERR\n" + done.stderr,
            encoding="utf-8",
        )
        files = markdown_files(target)
        if done.returncode:
            diagnostic = done.stderr.strip() or done.stdout.strip()
            diagnostic_tail = "\n".join(diagnostic.splitlines()[-40:])
            error = f"MinerU exited with code {done.returncode}"
            if diagnostic_tail:
                error += "\n" + diagnostic_tail
            status = "failed"
        elif not files:
            status, error = "warning_no_markdown", "MinerU completed without Markdown"
        else:
            status, error = "success", None
    except subprocess.TimeoutExpired:
        files, status, error = [], "timeout", f"Exceeded {args.timeout_minutes} minutes"
        log.write_text(error, encoding="utf-8")
    except OSError as exc:
        files, status, error = [], "failed", str(exc)
        log.write_text(error, encoding="utf-8")
    return {
        "status": status,
        "command": subprocess.list2cmdline(command),
        "duration_seconds": round(time.monotonic() - started, 3),
        "output_directory": str(target),
        "markdown_files": [str(p) for p in files],
        "log": str(log),
        "error": error,
    }


def run_md_only(
    documents: list[Path], output: Path, executable: str, args: argparse.Namespace
) -> int:
    """Publish only one Markdown file per document; discard MinerU intermediates."""
    names = output_stems(documents)

    failures = 0
    for index, document in enumerate(documents, 1):
        stem = names[document]
        destination = output / f"{stem}.md"
        if (
            not args.force
            and destination.exists()
            and destination.stat().st_mtime >= document.stat().st_mtime
        ):
            print(f"[{index}/{len(documents)}] 跳过已有结果：{document.name}")
            continue
        if args.dry_run:
            print(f"[{index}/{len(documents)}] 计划生成：{destination.name}")
            continue

        print(f"[{index}/{len(documents)}] 转换：{document.name}")
        with tempfile.TemporaryDirectory(prefix="math-modeling-ocr-") as temp:
            result = convert_one(document, Path(temp), executable, args)
            candidates = markdown_files(Path(temp))
            if result["status"] == "success" and candidates:
                chosen = max(candidates, key=lambda path: path.stat().st_size)
                if getattr(args, "textualize_images", False):
                    destination.write_text(
                        textualize_images(chosen.read_text(encoding="utf-8")), encoding="utf-8"
                    )
                    shutil.copystat(chosen, destination)
                else:
                    shutil.copy2(chosen, destination)
                print(f"    已生成：{destination.name}")
            else:
                failures += 1
                print(f"    失败：{result.get('error') or result['status']}", file=sys.stderr)
    return 1 if failures else 0


def run_dify_multimodal(
    documents: list[Path], output: Path, executable: str, args: argparse.Namespace
) -> int:
    """Run MinerU once and publish a text Markdown plus a multimodal bundle."""
    markdown_output = (
        Path(args.markdown_output).expanduser().resolve()
        if getattr(args, "markdown_output", None) else None
    )
    if markdown_output:
        markdown_output.mkdir(parents=True, exist_ok=True)
    diagnostic_log_dir = (
        Path(args.log_dir).expanduser().resolve()
        if getattr(args, "log_dir", None)
        else (output / "_logs")
    )
    diagnostic_log_dir.mkdir(parents=True, exist_ok=True)
    names = output_stems(documents)

    failures = 0
    for index, document in enumerate(documents, 1):
        stem = names[document]
        destination = output / f"{stem}{DIFY_BUNDLE_SUFFIX}"
        markdown_destination = markdown_output / f"{stem}.md" if markdown_output else None
        bundle_ready = (
            destination.exists()
            and destination.stat().st_mtime >= document.stat().st_mtime
        )
        markdown_ready = (
            markdown_destination is None
            or (
                markdown_destination.exists()
                and markdown_destination.stat().st_mtime >= document.stat().st_mtime
            )
        )
        if (
            not args.force
            and bundle_ready
            and markdown_ready
        ):
            print(f"[{index}/{len(documents)}] 跳过已有两份结果：{document.name}")
            continue
        if args.dry_run:
            targets = destination.name
            if markdown_destination:
                targets += f" + {markdown_destination.name}"
            print(f"[{index}/{len(documents)}] 计划生成：{targets}")
            continue

        print(f"[{index}/{len(documents)}] 多模态转换：{document.name}", flush=True)
        with tempfile.TemporaryDirectory(prefix="math-modeling-mm-") as temp:
            temp_root = Path(temp)
            result = convert_one(document, temp_root, executable, args)
            document_log = diagnostic_log_dir / f"{stem}-mineru.log"
            temporary_log = Path(result["log"]) if result.get("log") else None
            if temporary_log and temporary_log.is_file():
                shutil.copy2(temporary_log, document_log)
                result["log"] = str(document_log)
            candidates = markdown_files(temp_root)
            if result["status"] == "success" and candidates:
                chosen = max(candidates, key=lambda path: path.stat().st_size)
                try:
                    manifest = build_multimodal_bundle(
                        chosen, document, destination, mineru_root=temp_root
                    )
                    if markdown_destination:
                        markdown_destination.write_text(
                            textualize_images(chosen.read_text(encoding="utf-8")),
                            encoding="utf-8",
                        )
                except (OSError, ValueError, zipfile.BadZipFile) as exc:
                    failures += 1
                    print(f"    封装失败：{exc}", file=sys.stderr)
                    continue
                print(
                    f"    已生成：{destination.name} "
                    f"({manifest['image_count']} 张图片，{manifest['chunk_count']} 个父块)"
                )
                if markdown_destination:
                    print(f"    已生成：{markdown_destination.name}")
                if manifest["missing_image_references"]:
                    print(
                        f"    警告：{len(manifest['missing_image_references'])} 个图片引用未找到",
                        file=sys.stderr,
                    )
            else:
                failures += 1
                print(f"    失败：{result.get('error') or result['status']}")
                print(f"    完整日志：{result.get('log')}")
    return 1 if failures else 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Batch-convert documents to Markdown with MinerU.")
    parser.add_argument("--input", default="knowledge_inbox/pdf")
    parser.add_argument("--output", default="knowledge_ready")
    parser.add_argument(
        "--markdown-output", default=None,
        help="With --dify-multimodal, also publish textualized Markdown here",
    )
    parser.add_argument(
        "--backend", choices=("pipeline", "hybrid-engine", "vlm-engine", "auto"),
        default="pipeline",
    )
    parser.add_argument("--effort", choices=("medium", "high"), default="medium")
    parser.add_argument("--image-analysis", action="store_true")
    parser.add_argument(
        "--textualize-images", action="store_true",
        help="Convert MinerU image-analysis details to text and remove dead image links",
    )
    parser.add_argument("--mineru-command", default="mineru")
    parser.add_argument("--log-dir", default=None)
    parser.add_argument("--timeout-minutes", type=int, default=0)
    parser.add_argument("--no-recursive", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    output_mode = parser.add_mutually_exclusive_group()
    output_mode.add_argument(
        "--md-only", action="store_true",
        help="Keep only one Markdown file per document and discard intermediates",
    )
    output_mode.add_argument(
        "--dify-multimodal", action="store_true",
        help="Create one .dify-mm.zip with Markdown and extracted images per document",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    source = Path(args.input).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    markdown_output = (
        Path(args.markdown_output).expanduser().resolve()
        if args.markdown_output else None
    )
    if IS_INSTALLED_SKILL and (output == SKILL_ROOT or SKILL_ROOT in output.parents):
        print("输出目录不能位于 SKILL_ROOT 内。", file=sys.stderr)
        return 3
    if output == source:
        print("输入目录和输出目录不能相同。", file=sys.stderr)
        return 3
    if markdown_output and not args.dify_multimodal:
        print("--markdown-output 只能与 --dify-multimodal 一起使用。", file=sys.stderr)
        return 3
    if markdown_output and markdown_output in {source, output}:
        print("Markdown 输出目录必须与输入目录和多模态输出目录不同。", file=sys.stderr)
        return 3
    if (
        markdown_output
        and IS_INSTALLED_SKILL
        and (markdown_output == SKILL_ROOT or SKILL_ROOT in markdown_output.parents)
    ):
        print("Markdown 输出目录不能位于 SKILL_ROOT 内。", file=sys.stderr)
        return 3
    source.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    documents = discover_documents(source, not args.no_recursive)
    old = {} if (args.md_only or args.dify_multimodal) else previous_records(output)
    executable = resolve_executable(args.mineru_command)

    if not documents:
        if not (args.md_only or args.dify_multimodal):
            write_report(output, source, args, [])
        print(f"未找到支持的文档。请把 PDF、DOCX、PPTX、XLSX 或图片放入：{source}")
        return 0
    if executable is None and not args.dry_run:
        if args.md_only or args.dify_multimodal:
            print(
                '未找到 MinerU。请先运行：uv tool install --python 3.12 "mineru[all]"',
                file=sys.stderr,
            )
            return 2
        records = [{
            "source": str(document), "sha256": sha256_file(document),
            "size_bytes": document.stat().st_size, "status": "blocked_missing_mineru",
            "error": "MinerU executable was not found", "markdown_files": []
        } for document in documents]
        write_report(output, source, args, records)
        print(
            '未找到 MinerU。请先运行：uv tool install --python 3.12 "mineru[all]"',
            file=sys.stderr,
        )
        return 2

    executable = executable or args.mineru_command
    if args.md_only:
        return run_md_only(documents, output, executable, args)
    if args.dify_multimodal:
        return run_dify_multimodal(documents, output, executable, args)

    records: list[dict[str, Any]] = []
    for index, document in enumerate(documents, 1):
        digest = sha256_file(document)
        target = output / f"{safe_stem(document.stem)}_{digest[:8]}"
        base = {"source": str(document), "sha256": digest, "size_bytes": document.stat().st_size}
        prior = old.get(str(document))
        existing = markdown_files(target)
        if (
            not args.force
            and prior
            and prior.get("sha256") == digest
            and prior.get("status") in {"success", "skipped"}
            and existing
        ):
            result = {
                "status": "skipped", "output_directory": str(target),
                "markdown_files": [str(p) for p in existing], "error": None
            }
            print(f"[{index}/{len(documents)}] 跳过：{document.name}")
        elif args.dry_run:
            command = build_mineru_command(
                executable, document, target, args.backend, args.effort,
                getattr(args, "image_analysis", False),
            )
            result = {
                "status": "planned", "command": subprocess.list2cmdline(command),
                "output_directory": str(target), "markdown_files": [], "error": None
            }
            print(f"[{index}/{len(documents)}] 计划：{document.name}")
        else:
            print(f"[{index}/{len(documents)}] 转换：{document.name}")
            result = convert_one(document, target, executable, args)
            print(f"    状态：{result['status']}")
        records.append({**base, **result})
        write_report(output, source, args, records)

    bad = {"failed", "timeout", "warning_no_markdown", "blocked_missing_mineru"}
    return 1 if any(r["status"] in bad for r in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())

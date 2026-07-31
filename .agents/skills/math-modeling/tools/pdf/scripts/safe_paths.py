import os
import tempfile
from pathlib import Path


SKILL_ROOT = Path(__file__).resolve().parents[3]


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def input_file(path) -> Path:
    resolved = Path(path).resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"输入必须是文件: {resolved}")
    return resolved


def output_file(path, *, inputs=(), overwrite=False, suffixes=None) -> Path:
    resolved = Path(path).resolve(strict=False)
    input_paths = {Path(item).resolve(strict=True) for item in inputs}
    if resolved in input_paths:
        raise ValueError("输出路径不能与任何输入文件相同")
    if is_within(resolved, SKILL_ROOT):
        raise ValueError("输出路径不能位于 SKILL_ROOT 内")
    if suffixes and resolved.suffix.lower() not in {suffix.lower() for suffix in suffixes}:
        raise ValueError(f"输出扩展名必须是: {sorted(suffixes)}")
    if resolved.exists() and not overwrite:
        raise FileExistsError(f"输出已存在；如确需覆盖请显式传入 --overwrite: {resolved}")
    if resolved.exists() and not resolved.is_file():
        raise ValueError("文件输出路径已被目录占用")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def atomic_write_text(path: Path, text: str, encoding="utf-8") -> None:
    handle, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=f".tmp{path.suffix}", dir=path.parent
    )
    os.close(handle)
    temporary = Path(temporary_name)
    try:
        temporary.write_text(text, encoding=encoding)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def output_directory(path, *, input_paths=(), overwrite=False) -> Path:
    resolved = Path(path).resolve(strict=False)
    inputs = [Path(item).resolve(strict=True) for item in input_paths]
    if is_within(resolved, SKILL_ROOT):
        raise ValueError("输出目录不能位于 SKILL_ROOT 内")
    if any(resolved == item or resolved == item.parent for item in inputs):
        raise ValueError("输出目录必须与输入文件及其所在目录分离")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("输出目录路径已被文件占用")
    if resolved.exists() and any(resolved.iterdir()) and not overwrite:
        raise FileExistsError("输出目录非空；如确需更新既有分页图请显式传入 --overwrite")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved

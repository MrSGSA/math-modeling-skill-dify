"""受限、拒绝歧义路径的 ZIP 校验与解压。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import stat
import unicodedata
import zipfile


class UnsafeZipError(ValueError):
    """ZIP 成员不满足安全合同。"""


@dataclass(frozen=True)
class ZipSafetyLimits:
    max_entries: int = 10_000
    max_total_uncompressed: int = 512 * 1024 * 1024
    max_member_uncompressed: int = 256 * 1024 * 1024
    max_compression_ratio: float = 2_000.0
    ratio_check_min_size: int = 1024 * 1024

    def __post_init__(self) -> None:
        if (self.max_entries < 1 or self.max_total_uncompressed < 1
                or self.max_member_uncompressed < 1
                or self.max_compression_ratio <= 0
                or self.ratio_check_min_size < 0):
            raise ValueError("ZIP 安全限额必须为正，ratio_check_min_size 可为 0")


DEFAULT_LIMITS = ZipSafetyLimits()

_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def _validated_member_path(info: zipfile.ZipInfo) -> tuple[PurePosixPath, str]:
    raw_name = info.filename
    if not raw_name or "\x00" in raw_name:
        raise UnsafeZipError("ZIP 含空名称或 NUL 字符")
    if "\\" in raw_name:
        raise UnsafeZipError(f"ZIP 成员必须使用正斜杠: {raw_name!r}")
    if raw_name.startswith("/") or raw_name.startswith("//"):
        raise UnsafeZipError(f"ZIP 成员不能是绝对路径: {raw_name!r}")
    trimmed = raw_name[:-1] if raw_name.endswith("/") else raw_name
    raw_parts = trimmed.split("/")
    if (not trimmed or any(part in {"", ".", ".."} for part in raw_parts)
            or any(":" in part for part in raw_parts)
            or any(any(ord(char) < 32 for char in part) for part in raw_parts)):
        raise UnsafeZipError(f"ZIP 成员路径含歧义或越界片段: {raw_name!r}")

    normalized_parts = []
    for part in raw_parts:
        normalized = unicodedata.normalize("NFC", part)
        if normalized.rstrip(" .") != normalized:
            raise UnsafeZipError(f"ZIP 成员含 Windows 尾随点或空格: {raw_name!r}")
        device_stem = normalized.split(".", 1)[0].upper()
        if device_stem in _WINDOWS_RESERVED:
            raise UnsafeZipError(f"ZIP 成员使用 Windows 保留名称: {raw_name!r}")
        normalized_parts.append(normalized)

    path = PurePosixPath(*normalized_parts)
    if path.is_absolute() or ".." in path.parts:
        raise UnsafeZipError(f"ZIP 成员不能逃离目标目录: {raw_name!r}")

    unix_mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(unix_mode)
    if file_type == stat.S_IFLNK:
        raise UnsafeZipError(f"ZIP 不允许符号链接: {raw_name!r}")
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise UnsafeZipError(f"ZIP 不允许特殊文件: {raw_name!r}")
    if info.flag_bits & 0x1:
        raise UnsafeZipError(f"ZIP 成员已加密，安全解包不接受密码回退: {raw_name!r}")

    # Windows 文件系统大小写不敏感；用 casefold 后的 NFC 路径识别覆盖别名。
    canonical_key = "/".join(part.casefold() for part in normalized_parts)
    return path, canonical_key


def validate_zip_members(
    archive: zipfile.ZipFile,
    limits: ZipSafetyLimits = DEFAULT_LIMITS,
) -> list[tuple[zipfile.ZipInfo, PurePosixPath]]:
    """在写入前校验所有成员，并返回安全的相对路径。"""
    members = archive.infolist()
    if len(members) > limits.max_entries:
        raise UnsafeZipError(
            f"ZIP 条目数 {len(members)} 超过限额 {limits.max_entries}"
        )

    validated = []
    seen = set()
    total_size = 0
    for info in members:
        path, canonical_key = _validated_member_path(info)
        if canonical_key in seen:
            raise UnsafeZipError(f"ZIP 含重复或大小写别名路径: {info.filename!r}")
        seen.add(canonical_key)

        if info.file_size < 0 or info.compress_size < 0:
            raise UnsafeZipError(f"ZIP 成员大小非法: {info.filename!r}")
        if info.file_size > limits.max_member_uncompressed:
            raise UnsafeZipError(
                f"ZIP 成员解压大小超过限额: {info.filename!r}"
            )
        total_size += info.file_size
        if total_size > limits.max_total_uncompressed:
            raise UnsafeZipError("ZIP 总解压大小超过限额")
        if info.file_size >= limits.ratio_check_min_size:
            ratio = info.file_size / max(1, info.compress_size)
            if ratio > limits.max_compression_ratio:
                raise UnsafeZipError(
                    f"ZIP 成员压缩比 {ratio:.1f} 超过限额: {info.filename!r}"
                )
        validated.append((info, path))
    return validated


def safe_extract_zip(
    archive: zipfile.ZipFile,
    destination: str | Path,
    limits: ZipSafetyLimits = DEFAULT_LIMITS,
) -> list[Path]:
    """把已校验 ZIP 解压到目标目录；拒绝覆盖任何既有路径。"""
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    validated = validate_zip_members(archive, limits)
    extracted = []

    for info, relative_path in validated:
        target = destination.joinpath(*relative_path.parts)
        resolved_target = target.resolve(strict=False)
        try:
            resolved_target.relative_to(destination)
        except ValueError as exc:
            raise UnsafeZipError(
                f"ZIP 成员解析后逃离目标目录: {info.filename!r}"
            ) from exc

        if info.is_dir():
            if target.exists() and not target.is_dir():
                raise UnsafeZipError(f"目录成员与既有文件冲突: {info.filename!r}")
            target.mkdir(parents=True, exist_ok=True)
            extracted.append(target)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise UnsafeZipError(f"拒绝覆盖既有解压目标: {target}")
        written = 0
        with archive.open(info, "r") as source, target.open("xb") as output:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limits.max_member_uncompressed:
                    raise UnsafeZipError(
                        f"ZIP 成员实际解压大小超过限额: {info.filename!r}"
                    )
                output.write(chunk)
        if written != info.file_size:
            raise UnsafeZipError(
                f"ZIP 成员声明大小与实际解压大小不一致: {info.filename!r}"
            )
        extracted.append(target)
    return extracted

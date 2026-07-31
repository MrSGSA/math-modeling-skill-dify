import subprocess
import stat
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "tools" / "docx" / "scripts"
OFFICE = SCRIPTS / "office"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(OFFICE))

import accept_changes
import comment
import unpack as office_unpack
from helpers.safe_zip import (
    UnsafeZipError,
    ZipSafetyLimits,
    safe_extract_zip,
)
from validators.docx import DOCXSchemaValidator


class AcceptChangesTests(unittest.TestCase):
    def test_detects_tracked_changes_in_docx_parts(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tracked.docx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "word/document.xml",
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:ins/></w:document>',
                )
            self.assertTrue(accept_changes.contains_tracked_changes(path))

    def test_timeout_is_failure_and_does_not_publish_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.docx"
            output = Path(tmp) / "output.docx"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("word/document.xml", "<document/>")
            with patch.object(accept_changes, "_setup_libreoffice_macro", return_value=True), patch.object(
                accept_changes.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["soffice"], 30),
            ):
                _, message = accept_changes.accept_changes(str(source), str(output))

            self.assertIn("Error", message)
            self.assertFalse(output.exists())

    def test_refuses_in_place_or_implicit_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "input.docx"
            output = Path(tmp) / "output.docx"
            source.write_bytes(b"input")
            output.write_bytes(b"existing")
            _, same_message = accept_changes.accept_changes(str(source), str(source))
            _, overwrite_message = accept_changes.accept_changes(str(source), str(output))
            self.assertIn("路径必须不同", same_message)
            self.assertIn("--overwrite", overwrite_message)
            self.assertEqual(output.read_bytes(), b"existing")


class CommentTests(unittest.TestCase):
    def test_missing_parent_does_not_modify_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            word = Path(tmp) / "word"
            word.mkdir()

            _, message = comment.add_comment(tmp, 2, "回复", parent_id=999)

            self.assertIn("Error", message)
            self.assertFalse((word / "comments.xml").exists())

    def test_comment_text_and_author_are_xml_escaped(self):
        with tempfile.TemporaryDirectory() as tmp:
            word = Path(tmp) / "word"
            word.mkdir()

            _, message = comment.add_comment(tmp, 0, "A & B", author='甲 & "乙"')

            self.assertNotIn("Error", message)
            parsed = comment.defusedxml.minidom.parse(str(word / "comments.xml"))
            node = parsed.getElementsByTagName("w:comment")[0]
            self.assertEqual(node.getAttribute("w:author"), '甲 & "乙"')
            self.assertIn("A & B", node.getElementsByTagName("w:t")[0].firstChild.nodeValue)


class SafeOfficeZipTests(unittest.TestCase):
    def test_rejects_path_traversal_backslashes_and_absolute_paths(self):
        names = ["../escape.xml", "..\\escape.xml", "/absolute.xml", "C:/drive.xml"]
        for index, name in enumerate(names):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                archive_path = Path(tmp) / f"malicious-{index}.docx"
                destination = Path(tmp) / "out"
                with zipfile.ZipFile(archive_path, "w") as archive:
                    archive.writestr(name, "bad")
                with zipfile.ZipFile(archive_path, "r") as archive:
                    with self.assertRaises(UnsafeZipError):
                        safe_extract_zip(archive, destination)
                self.assertFalse((Path(tmp) / "escape.xml").exists())

    def test_rejects_symlinks_case_aliases_and_excessive_unpacked_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            symlink_archive = root / "symlink.docx"
            link = zipfile.ZipInfo("word/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            with zipfile.ZipFile(symlink_archive, "w") as archive:
                archive.writestr(link, "../outside")
            with zipfile.ZipFile(symlink_archive, "r") as archive:
                with self.assertRaises(UnsafeZipError):
                    safe_extract_zip(archive, root / "symlink-out")

            alias_archive = root / "alias.docx"
            with zipfile.ZipFile(alias_archive, "w") as archive:
                archive.writestr("word/document.xml", "one")
                archive.writestr("WORD/document.xml", "two")
            with zipfile.ZipFile(alias_archive, "r") as archive:
                with self.assertRaises(UnsafeZipError):
                    safe_extract_zip(archive, root / "alias-out")

            large_archive = root / "large.docx"
            with zipfile.ZipFile(large_archive, "w") as archive:
                archive.writestr("word/document.xml", b"x" * 20)
            limits = ZipSafetyLimits(
                max_entries=10,
                max_total_uncompressed=10,
                max_member_uncompressed=100,
                max_compression_ratio=100,
                ratio_check_min_size=0,
            )
            with zipfile.ZipFile(large_archive, "r") as archive:
                with self.assertRaises(UnsafeZipError):
                    safe_extract_zip(archive, root / "large-out", limits)

    def test_valid_archive_extracts_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive_path = root / "valid.docx"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("word/document.xml", "<document/>")
            destination = root / "out"
            with zipfile.ZipFile(archive_path, "r") as archive:
                safe_extract_zip(archive, destination)
            self.assertEqual(
                (destination / "word" / "document.xml").read_text(encoding="utf-8"),
                "<document/>",
            )
            with zipfile.ZipFile(archive_path, "r") as archive:
                with self.assertRaises(UnsafeZipError):
                    safe_extract_zip(archive, destination)

    def test_unpack_is_transactional_and_refuses_nonempty_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            valid = root / "valid.docx"
            with zipfile.ZipFile(valid, "w") as archive:
                archive.writestr("word/document.xml", "<document/>")
            destination = root / "unpacked"
            destination.mkdir()
            sentinel = destination / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            _, message = office_unpack.unpack(
                str(valid), str(destination),
                merge_runs=False, simplify_redlines=False,
            )
            self.assertIn("must be empty", message)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

            malicious = root / "malicious.docx"
            with zipfile.ZipFile(malicious, "w") as archive:
                archive.writestr("../escape.xml", "bad")
            new_destination = root / "new-unpacked"
            _, unsafe_message = office_unpack.unpack(
                str(malicious), str(new_destination),
                merge_runs=False, simplify_redlines=False,
            )
            self.assertIn("unsafe Office ZIP", unsafe_message)
            self.assertFalse(new_destination.exists())


class OfficeValidatorTests(unittest.TestCase):
    def test_invalid_ooxml_ids_fail_instead_of_being_silently_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            word = Path(tmp) / "word"
            word.mkdir()
            (word / "document.xml").write_text(
                '<w:document '
                'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" '
                'xmlns:w16cid="http://schemas.microsoft.com/office/word/2016/wordml/cid">'
                '<w:p w14:paraId="NOTHEX" w16cid:durableId="ALSO-NOT-HEX"/>'
                '</w:document>',
                encoding="utf-8",
            )

            validator = DOCXSchemaValidator(tmp)

            self.assertFalse(validator.validate_id_constraints())


if __name__ == "__main__":
    unittest.main()

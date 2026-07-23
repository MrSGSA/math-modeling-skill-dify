import importlib.util
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "tools" / "pdf-batch" / "scripts" / "batch_convert.py"
)
SPEC = importlib.util.spec_from_file_location("batch_convert", SCRIPT)
batch_convert = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(batch_convert)


class PdfBatchTests(unittest.TestCase):
    def test_discovers_pdf_case_insensitively_and_recursively(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "nested").mkdir()
            (root / "a.PDF").write_bytes(b"%PDF-1.4")
            (root / "nested" / "b.pdf").write_bytes(b"%PDF-1.4")
            (root / "ignore.txt").write_text("x", encoding="utf-8")
            found = batch_convert.discover_pdfs(root)
        self.assertEqual([path.name for path in found], ["a.PDF", "b.pdf"])

    def test_pipeline_command_preserves_paths_with_spaces(self):
        command = batch_convert.build_mineru_command(
            "mineru", Path("论文 A.pdf"), Path("输出 A"), "pipeline"
        )
        self.assertEqual(
            command,
            [
                "mineru", "-p", "论文 A.pdf", "-o", "输出 A", "-b", "pipeline",
                "--formula", "true", "--table", "true",
            ],
        )

    def test_discovers_office_documents_and_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("论文.docx", "数据.xlsx", "汇报.pptx", "公式.png"):
                (root / name).write_bytes(b"x")
            (root / "旧格式.doc").write_bytes(b"x")
            found = batch_convert.discover_documents(root)
        self.assertEqual(
            sorted(path.name for path in found),
            sorted(["论文.docx", "数据.xlsx", "汇报.pptx", "公式.png"]),
        )

    def test_duplicate_source_names_get_stable_unique_output_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = root / "a" / "论文.pdf"
            second = root / "b" / "论文.pdf"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_bytes(b"first")
            second.write_bytes(b"second")
            names = batch_convert.output_stems([first, second])
        self.assertNotEqual(names[first], names[second])
        self.assertRegex(names[first], r"^论文_pdf_[0-9a-f]{8}$")
        self.assertRegex(names[second], r"^论文_pdf_[0-9a-f]{8}$")

    def test_dry_run_writes_plan_without_mineru(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output = root / "input", root / "output"
            source.mkdir()
            (source / "模型.pdf").write_bytes(b"%PDF-1.4\n%%EOF")
            code = batch_convert.main([
                "--input", str(source), "--output", str(output), "--dry-run"
            ])
            report = json.loads(
                (output / batch_convert.REPORT_NAME).read_text(encoding="utf-8")
            )
        self.assertEqual(code, 0)
        self.assertEqual(report["summary"]["by_status"], {"planned": 1})

    def test_rejects_output_inside_skill_root(self):
        code = batch_convert.main([
            "--input", str(Path(tempfile.gettempdir()) / "pdf-batch-input"),
            "--output", str(batch_convert.SKILL_ROOT / "forbidden-output"),
            "--dry-run",
        ])
        self.assertEqual(code, 3)

    def test_md_only_publishes_markdown_and_discards_intermediates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / "input" / "案例.pdf"
            output = root / "OCR结果"
            pdf.parent.mkdir()
            output.mkdir()
            pdf.write_bytes(b"%PDF-1.4\n%%EOF")

            def fake_convert(_pdf, target, _executable, _args):
                nested = target / "nested"
                nested.mkdir()
                (nested / "案例.md").write_text("公式：$x^2$", encoding="utf-8")
                (nested / "middle.json").write_text("{}", encoding="utf-8")
                return {"status": "success", "error": None}

            args = SimpleNamespace(force=False, dry_run=False, effort="medium")
            with patch.object(batch_convert, "convert_one", side_effect=fake_convert):
                code = batch_convert.run_md_only([pdf], output, "mineru", args)

            files = list(output.iterdir())
        self.assertEqual(code, 0)
        self.assertEqual([path.name for path in files], ["案例.md"])

    def test_textualize_images_keeps_analysis_and_removes_dead_link(self):
        source = (
            "![](images/chart.png)\n\n"
            "<details><summary>chart content</summary>单调上升的折线图</details>"
        )
        result = batch_convert.textualize_images(source)
        self.assertNotIn("images/chart.png", result)
        self.assertIn("### 图表内容", result)
        self.assertIn("单调上升的折线图", result)

    def test_builds_self_contained_multimodal_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mineru = root / "mineru"
            images = mineru / "images"
            images.mkdir(parents=True)
            chart = images / "chart.png"
            chart.write_bytes(b"fake-png")
            markdown = mineru / "paper.md"
            markdown.write_text(
                "# 模型结果\n\n误差随迭代下降。\n\n"
                "![](images/chart.png)\n\n图 1 收敛曲线。",
                encoding="utf-8",
            )
            source = root / "论文.pdf"
            source.write_bytes(b"%PDF-1.4")
            destination = root / "论文.dify-mm.zip"

            manifest = batch_convert.build_multimodal_bundle(
                markdown, source, destination, mineru_root=mineru
            )
            with zipfile.ZipFile(destination) as archive:
                names = set(archive.namelist())
                archived_manifest = json.loads(archive.read("manifest.json"))
                archived_markdown = archive.read("document.md").decode("utf-8")

        self.assertEqual(manifest["image_count"], 1)
        self.assertEqual(archived_manifest["schema_version"], "1.0")
        self.assertIn("manifest.json", names)
        self.assertIn("document.md", names)
        self.assertTrue(any(name.startswith("images/") for name in names))
        self.assertIn("__img_0001_", archived_markdown)
        self.assertEqual(archived_manifest["purpose"], "image-retrieval-only")
        self.assertTrue(all(chunk["images"] for chunk in archived_manifest["chunks"]))
        chunks_with_images = [
            chunk for chunk in archived_manifest["chunks"] if chunk["images"]
        ]
        self.assertEqual(len(chunks_with_images), 1)
        self.assertIn("收敛曲线", chunks_with_images[0]["parent_content"])

    def test_combined_mode_runs_mineru_once_and_publishes_both_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input" / "论文.pdf"
            bundles = root / "multimodal_packages"
            markdown_output = root / "output"
            source.parent.mkdir()
            bundles.mkdir()
            source.write_bytes(b"%PDF-1.4")

            def fake_convert(_document, target, _executable, _args):
                images = target / "images"
                images.mkdir(parents=True)
                (images / "figure.png").write_bytes(b"image")
                (target / "论文.md").write_text(
                    "# 结果\n\n![](images/figure.png)\n\n"
                    "<details><summary>image content</summary>流程结构图</details>",
                    encoding="utf-8",
                )
                return {"status": "success", "error": None}

            args = SimpleNamespace(
                force=False,
                dry_run=False,
                backend="auto",
                effort="high",
                image_analysis=True,
                timeout_minutes=0,
                markdown_output=str(markdown_output),
            )
            with patch.object(batch_convert, "convert_one", side_effect=fake_convert) as mocked:
                first = batch_convert.run_dify_multimodal(
                    [source], bundles, "mineru", args
                )
                second = batch_convert.run_dify_multimodal(
                    [source], bundles, "mineru", args
                )

            markdown_text = (markdown_output / "论文.md").read_text(encoding="utf-8")
            bundle_exists = (bundles / "论文.dify-mm.zip").is_file()

        self.assertEqual(first, 0)
        self.assertEqual(second, 0)
        self.assertEqual(mocked.call_count, 1)
        self.assertTrue(bundle_exists)
        self.assertIn("流程结构图", markdown_text)
        self.assertNotIn("images/figure.png", markdown_text)


if __name__ == "__main__":
    unittest.main()

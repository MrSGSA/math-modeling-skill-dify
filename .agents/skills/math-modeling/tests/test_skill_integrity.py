import json
import re
import unittest
import zipfile
from io import StringIO
from pathlib import Path
from urllib.parse import unquote

import yaml
from pyflakes.api import check
from pyflakes.reporter import Reporter


SKILL_ROOT = Path(__file__).resolve().parents[1]
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MARKDOWN_LINK_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
PYTHON_FENCE_PATTERN = re.compile(r"```python\s*\n(.*?)```", re.DOTALL)


def frontmatter(path: Path):
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}, text
    marker = text.find("\n---\n", 4)
    if marker < 0:
        return {}, text
    fields = {}
    for line in text[4:marker].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip()] = value.strip().strip('"\'')
    return fields, text[marker + 5:]


class SkillIntegrityTests(unittest.TestCase):
    def test_skill_names_match_directories_and_frontmatter_is_minimal(self):
        skill_files = sorted(SKILL_ROOT.rglob("SKILL.md"))
        self.assertTrue(skill_files)
        for skill_file in skill_files:
            with self.subTest(skill=str(skill_file.relative_to(SKILL_ROOT))):
                fields, _ = frontmatter(skill_file)
                self.assertEqual(set(fields), {"name", "description"})
                self.assertRegex(fields["name"], NAME_PATTERN)
                self.assertEqual(fields["name"], skill_file.parent.name)
                self.assertTrue(fields["description"])

    def test_every_skill_has_user_facing_metadata(self):
        for skill_file in SKILL_ROOT.rglob("SKILL.md"):
            with self.subTest(skill=str(skill_file.parent.relative_to(SKILL_ROOT))):
                fields, _ = frontmatter(skill_file)
                metadata = skill_file.parent / "agents" / "openai.yaml"
                self.assertTrue(metadata.is_file(), metadata)
                text = metadata.read_text(encoding="utf-8")
                self.assertIn("display_name:", text)
                self.assertIn("short_description:", text)
                self.assertIn(f"${fields['name']}", text)
                match = re.search(r'^\s*short_description:\s*"([^"]+)"\s*$', text, re.MULTILINE)
                self.assertIsNotNone(match)
                self.assertGreaterEqual(len(match.group(1)), 25)
                self.assertLessEqual(len(match.group(1)), 64)

    def test_local_markdown_links_exist(self):
        missing = []
        for markdown in SKILL_ROOT.rglob("*.md"):
            text = markdown.read_text(encoding="utf-8")
            text = re.sub(r"```.*?```|\$\$.*?\$\$|\$[^\n$]*\$", "", text, flags=re.DOTALL)
            for raw_target in MARKDOWN_LINK_PATTERN.findall(text):
                target = raw_target.strip()
                if target.startswith("<") and target.endswith(">"):
                    target = target[1:-1]
                else:
                    target = target.split(maxsplit=1)[0]
                target = unquote(target.split("#", 1)[0].split("?", 1)[0])
                if not target or target.startswith(("http://", "https://", "mailto:", "data:")):
                    continue
                path = Path(target)
                resolved = path if path.is_absolute() else markdown.parent / path
                if not resolved.exists():
                    missing.append(f"{markdown.relative_to(SKILL_ROOT)} -> {target}")
        self.assertEqual(missing, [])

    def test_all_yaml_and_json_are_parseable(self):
        for path in SKILL_ROOT.rglob("*"):
            if not path.is_file():
                continue
            with self.subTest(path=str(path.relative_to(SKILL_ROOT))):
                if path.suffix.lower() in {".yaml", ".yml"}:
                    self.assertIsInstance(yaml.safe_load(path.read_text(encoding="utf-8")), dict)
                elif path.suffix.lower() == ".json":
                    self.assertIsNotNone(json.loads(path.read_text(encoding="utf-8")))

    def test_python_examples_have_no_undefined_names(self):
        issues = []
        for markdown in SKILL_ROOT.rglob("*.md"):
            text = markdown.read_text(encoding="utf-8")
            for index, match in enumerate(PYTHON_FENCE_PATTERN.finditer(text), start=1):
                stdout = StringIO()
                stderr = StringIO()
                label = f"{markdown.relative_to(SKILL_ROOT)}#{index}"
                check(match.group(1), label, Reporter(stdout, stderr))
                messages = (stdout.getvalue() + stderr.getvalue()).splitlines()
                issues.extend(message for message in messages if "undefined name" in message)
        self.assertEqual(issues, [])

    def test_long_reference_documents_have_contents(self):
        missing = []
        for path in SKILL_ROOT.rglob("*.md"):
            if path.name == "SKILL.md":
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            if len(lines) > 100 and not any(
                line.strip() in {"## 目录", "## Contents"} for line in lines[:60]
            ):
                missing.append(str(path.relative_to(SKILL_ROOT)))
        self.assertEqual(missing, [])

    def test_context_references_are_not_stored_as_assets(self):
        assets = SKILL_ROOT / "assets"
        markdown = list(assets.rglob("*.md")) if assets.exists() else []
        self.assertEqual(markdown, [])
        self.assertFalse(any("assets/" in path.read_text(encoding="utf-8") for path in SKILL_ROOT.rglob("*.md")))

    def test_licensed_tools_surface_their_license(self):
        for license_file in SKILL_ROOT.rglob("LICENSE.txt"):
            with self.subTest(tool=str(license_file.parent.relative_to(SKILL_ROOT))):
                skill = license_file.parent / "SKILL.md"
                self.assertTrue(skill.is_file())
                self.assertIn("LICENSE.txt", skill.read_text(encoding="utf-8"))

    def test_document_metadata_has_no_ai_vendor_default(self):
        pattern = re.compile(
            r"(?:default\s*=|author:\s*str\s*=|author\s*=)\s*[\"'](?:claude|chatgpt|openai|anthropic|codex)[\"']",
            re.IGNORECASE,
        )
        hits = []
        for path in SKILL_ROOT.rglob("*.py"):
            if pattern.search(path.read_text(encoding="utf-8")):
                hits.append(str(path.relative_to(SKILL_ROOT)))
        self.assertEqual(hits, [])

    def test_bundled_docx_has_no_ai_vendor_metadata(self):
        pattern = re.compile(rb"claude|anthropic|openai|chatgpt|codex", re.IGNORECASE)
        hits = []
        for path in SKILL_ROOT.rglob("*.docx"):
            with zipfile.ZipFile(path) as archive:
                matched = [name for name in archive.namelist() if pattern.search(archive.read(name))]
            if matched:
                hits.append(f"{path.relative_to(SKILL_ROOT)}: {matched}")
        self.assertEqual(hits, [])

    def test_ai_disclosure_is_explicitly_user_controlled(self):
        policies = [
            SKILL_ROOT / "SKILL.md",
            SKILL_ROOT / "references" / "roles" / "paper-writer" / "SKILL.md",
            SKILL_ROOT / "tools" / "docx" / "SKILL.md",
        ]
        for path in policies:
            with self.subTest(path=str(path.relative_to(SKILL_ROOT))):
                text = path.read_text(encoding="utf-8")
                self.assertIn("不自动添加、删除或推断 AI 使用披露", text)
                self.assertIn("由用户", text)
                self.assertIn("官方规则", text)

    def test_no_retired_paths_or_competition_specific_pollution(self):
        forbidden = {
            "roles/" + "建模手": "旧建模手路径",
            "roles/" + "编程手": "旧编程手路径",
            "roles/" + "论文手": "旧论文手路径",
            "tools/paper" + "_search": "旧论文搜索路径",
            "74" + "70": "单题数据行数",
            "399." + "6747": "单题首值",
            "REQUIRED_ABSTRACT" + "_ITEMS": "固定摘要项目",
            "每道子问题" + "最多两个": "固定模型数量",
            "每个子问题" + "最多两个": "固定模型数量",
            "正文恰好" + "30页": "固定正文页数",
        }
        hits = []
        for path in SKILL_ROOT.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".md", ".py", ".json", ".yaml", ".yml"}:
                continue
            if path.resolve() == Path(__file__).resolve():
                continue
            text = path.read_text(encoding="utf-8")
            for token, label in forbidden.items():
                if token in text:
                    hits.append(f"{path.relative_to(SKILL_ROOT)}: {label}={token}")
        self.assertEqual(hits, [])

    def test_release_gate_has_no_fixed_problem_or_quality_defaults(self):
        gate = (SKILL_ROOT / "tools" / "docx" / "scripts" / "paper_release_gate.py").read_text(encoding="utf-8")
        self.assertNotIn("problem_3" + "_method_result", gate)
        self.assertNotRegex(gate, r"quality\.get\([^\n]+,\s*(?:30|0\.85|0\.96)\)")
        self.assertIn('audit.get("required_items")', gate)
        self.assertIn('item.get("requirement"', gate)
        formatter = (SKILL_ROOT / "tools" / "docx" / "scripts" / "paper_format.py").read_text(encoding="utf-8")
        self.assertNotIn("paper_format_demo.docx", formatter)

    def test_runtime_only_documentation_is_not_bundled(self):
        for relative in ("README.md", "CHANGELOG.md", "VERSION", "references/README.md", "assets/README.md", "imgs"):
            self.assertFalse((SKILL_ROOT / relative).exists(), relative)

    def test_cache_patterns_are_ignored(self):
        ignore = (SKILL_ROOT / ".gitignore").read_text(encoding="utf-8")
        for pattern in ("__pycache__/", "*.py[cod]", ".pytest_cache/"):
            self.assertIn(pattern, ignore)

    def test_optional_services_have_consistent_degradation_contracts(self):
        root_metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("确有需要", root_metadata)
        self.assertIn("知识库可用", root_metadata)
        paper_writer = (
            SKILL_ROOT / "references" / "roles" / "paper-writer" / "SKILL.md"
        ).read_text(encoding="utf-8")
        designer_workflow = (
            SKILL_ROOT
            / "references"
            / "roles"
            / "model-designer"
            / "references"
            / "工作流程.md"
        ).read_text(encoding="utf-8")
        for text in (paper_writer, designer_workflow):
            self.assertIn("未完成双引擎交叉验证", text)
            self.assertIn("故障", text)

    def test_marginal_contribution_is_objective_direction_aware(self):
        review = (SKILL_ROOT / "references" / "评审门与证据等级.md").read_text(encoding="utf-8")
        self.assertIn("独立效果_i = U({i}) - U(∅)", review)
        self.assertIn("若 J 越小越好", review)
        self.assertIn("J(S\\{i}) - J(S)", review)
        self.assertNotIn("独立效果_i = J({i})", review)

    def test_pdf_reference_does_not_recommend_in_place_repair(self):
        reference = (SKILL_ROOT / "tools" / "pdf" / "reference.md").read_text(encoding="utf-8")
        self.assertNotIn("--replace-input", reference)
        self.assertIn("qpdf corrupted.pdf repaired.pdf", reference)

    def test_office_archives_use_the_shared_safe_extractor(self):
        office = SKILL_ROOT / "tools" / "docx" / "scripts" / "office"
        extractall_hits = []
        for path in office.rglob("*.py"):
            if "extractall(" in path.read_text(encoding="utf-8"):
                extractall_hits.append(str(path.relative_to(SKILL_ROOT)))
        self.assertEqual(extractall_hits, [])
        helper = (office / "helpers" / "safe_zip.py").read_text(encoding="utf-8")
        for token in (
            "UnsafeZipError", "max_total_uncompressed", "stat.S_IFLNK",
            "canonical_key", "target.open(\"xb\")",
        ):
            self.assertIn(token, helper)

    def test_office_skill_examples_do_not_use_optimizable_assert_contracts(self):
        xlsx = (SKILL_ROOT / "tools" / "xlsx" / "SKILL.md").read_text(encoding="utf-8")
        self.assertNotRegex(xlsx, r"(?m)^assert\s")
        self.assertIn("raise ValueError", xlsx)

    def test_pdf_skill_requires_authorized_decryption_and_self_contained_imports(self):
        pdf = (SKILL_ROOT / "tools" / "pdf" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("用户有权访问", pdf)
        self.assertIn("不得猜测、破解、绕过或削弱访问控制", pdf)
        self.assertNotIn("--password=", pdf)
        self.assertIn("from getpass import getpass", pdf)
        self.assertIn("from pypdf import PdfReader, PdfWriter\n\nreader = PdfReader(\"input.pdf\")", pdf)
        fields, _ = frontmatter(SKILL_ROOT / "tools" / "pdf" / "SKILL.md")
        self.assertRegex(fields["description"], r"[\u4e00-\u9fff]")


if __name__ == "__main__":
    unittest.main()

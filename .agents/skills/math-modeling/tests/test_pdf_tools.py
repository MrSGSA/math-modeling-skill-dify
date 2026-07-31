import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from pypdf import PdfReader, PdfWriter


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "tools" / "pdf" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_script(module_name, filename):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


fillable = load_script("safe_fillable_pdf", "fill_fillable_fields.py")
annotations = load_script("safe_annotation_pdf", "fill_pdf_form_with_annotations.py")
field_info = load_script("safe_field_info", "extract_form_field_info.py")
validation_image = load_script("safe_validation_image", "create_validation_image.py")


class PdfOutputSafetyTests(unittest.TestCase):
    @staticmethod
    def _blank_pdf(path):
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        with path.open("wb") as stream:
            writer.write(stream)

    def test_fillable_fields_refuses_in_place_and_implicit_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.pdf"
            fields = root / "fields.json"
            output = root / "output.pdf"
            self._blank_pdf(source)
            fields.write_text("[]", encoding="utf-8")

            with self.assertRaises(ValueError):
                fillable.fill_pdf_fields(source, fields, source)
            with self.assertRaises(ValueError):
                fillable.fill_pdf_fields(source, fields, ROOT / "forbidden.pdf")

            fillable.fill_pdf_fields(source, fields, output)
            self.assertEqual(len(PdfReader(str(output)).pages), 1)
            with self.assertRaises(FileExistsError):
                fillable.fill_pdf_fields(source, fields, output)
            fillable.fill_pdf_fields(source, fields, output, overwrite=True)
            self.assertEqual(len(PdfReader(str(output)).pages), 1)

    def test_annotation_fill_refuses_in_place_and_implicit_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.pdf"
            fields = root / "fields.json"
            output = root / "output.pdf"
            self._blank_pdf(source)
            fields.write_text(
                json.dumps({"pages": [], "form_fields": []}),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                annotations.fill_pdf_form(source, fields, source)
            with self.assertRaises(ValueError):
                annotations.fill_pdf_form(source, fields, ROOT / "forbidden.pdf")

            annotations.fill_pdf_form(source, fields, output)
            self.assertEqual(len(PdfReader(str(output)).pages), 1)
            with self.assertRaises(FileExistsError):
                annotations.fill_pdf_form(source, fields, output)
            annotations.fill_pdf_form(source, fields, output, overwrite=True)
            self.assertEqual(len(PdfReader(str(output)).pages), 1)

    def test_extract_and_validation_outputs_are_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.pdf"
            extracted = root / "fields.json"
            self._blank_pdf(source)

            field_info.write_field_info(source, extracted)
            self.assertEqual(json.loads(extracted.read_text(encoding="utf-8")), [])
            with self.assertRaises(FileExistsError):
                field_info.write_field_info(source, extracted)
            field_info.write_field_info(source, extracted, overwrite=True)

            page_image = root / "page.png"
            Image.new("RGB", (100, 100), "white").save(page_image)
            boxes = root / "boxes.json"
            boxes.write_text(json.dumps({"form_fields": []}), encoding="utf-8")
            with self.assertRaises(ValueError):
                validation_image.create_validation_image(1, boxes, page_image, page_image)
            output_image = root / "checked.png"
            validation_image.create_validation_image(1, boxes, page_image, output_image)
            self.assertTrue(output_image.is_file())
            with self.assertRaises(FileExistsError):
                validation_image.create_validation_image(1, boxes, page_image, output_image)


if __name__ == "__main__":
    unittest.main()

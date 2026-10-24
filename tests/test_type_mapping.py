import json
import tempfile
import unittest
from pathlib import Path

from detretlabel.type_mapping import TypeMapping, category_text


class TypeMappingTests(unittest.TestCase):
    def test_utf8_bom_comments_colons_numeric_codes_and_fallback(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "种类.txt"
            path.write_text("# 注释\n\n typeA : 行人 \n0：汽车\n001:编号001\na:名称:子类\n", encoding="utf-8-sig")
            mapping = TypeMapping.load(path)
            self.assertEqual(mapping.path, path.resolve())
            self.assertEqual(mapping.display("typeA"), "行人")
            self.assertEqual(mapping.display(0), "汽车")
            self.assertEqual(mapping.display("001"), "编号001")
            self.assertEqual(mapping.display(1), "1")
            self.assertEqual(mapping.display("a"), "名称:子类")
            self.assertEqual(mapping.display("TypeA"), "TypeA")
            self.assertIsNone(mapping.display(None))

    def test_invalid_lines_empty_and_duplicate_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "types.txt"
            for value in ("a", ":名称", "a:", "# nothing\n", "a:一\na:二", "a:一\nb"):
                with self.subTest(value=value):
                    path.write_text(value, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        TypeMapping.load(path)
            path.write_text("# first\na:一\nb", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "第 3 行"):
                TypeMapping.load(path)

    def test_preferences_preserve_snapshot_and_validate_types(self):
        mapping = TypeMapping.from_preferences(json.dumps({"0": "行人"}), "gone.txt")
        self.assertEqual(mapping.display(0), "行人")
        for data in ([], {"a": 1}, {"": "name"}, {"a": " "}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                TypeMapping.from_preferences(json.dumps(data))
        self.assertEqual(category_text({"b": 2, "a": 1}), '{"a": 1, "b": 2}')


if __name__ == "__main__":
    unittest.main()

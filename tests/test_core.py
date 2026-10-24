import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from detretlabel.core import (
    Collection, FieldMapping, basename, discover_collections, fields, guess_mapping,
    import_results, load_json, parse_detection, read_pointer, scan_images,
)


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.images = [Path("images/0001.jpg"), Path("images/0002.png")]

    def test_flat_list_matches_linux_and_windows_paths_case_insensitively(self):
        data = [
            {"path": "/tmp/0001.JPG", "xmin": 1, "ymin": 2, "xmax": 30, "ymax": 40, "type": "车", "conf": .9},
            {"path": "C:\\result\\0002.png", "xmin": 5, "ymin": 6, "xmax": 7, "ymax": 8, "type": "人", "conf": .8},
        ]
        mapping = guess_mapping(data, Collection(""))
        result = import_results(data, mapping, self.images)
        self.assertEqual(result.matched_target_count, 2)
        self.assertEqual(result.detections[self.images[0]][0].category, "车")
        self.assertEqual(result.detections[self.images[0]][0].raw, data[0])
        self.assertEqual(result.issues, [])

    def test_grouped_records_with_xywh_and_empty_targets(self):
        data = {"results": [
            {"name": "0001.jpg", "objects": [{"bbox": [10, 20, 30, 40], "label": "person", "score": .8}]},
            {"name": "0002.png", "objects": []},
        ]}
        mapping = replace(guess_mapping(data, Collection("/results")), box_format="xywh")
        result = import_results(data, mapping, self.images)
        self.assertEqual(result.detections[self.images[0]][0].box, (10, 20, 40, 60))
        self.assertIn(self.images[1], result.referenced)
        self.assertEqual(result.detections[self.images[1]], [])

    def test_filename_keyed_dictionary_of_lists(self):
        data = {"0001.jpg": [{"box": [0, 0, 5, 5], "type": 0}], "0002.png": []}
        collection = discover_collections(data)[0]
        self.assertEqual(collection.kind, "keyed")
        result = import_results(data, guess_mapping(data, collection), self.images)
        self.assertEqual(result.matched_target_count, 1)
        self.assertEqual(len(result.referenced), 2)
        self.assertEqual(result.detections[self.images[0]][0].category, 0)

    def test_keyed_objects_and_single_image(self):
        record = {"path": "0001.jpg", "detections": [{"xywh": [1, 2, 3, 4]}]}
        result = import_results(record, guess_mapping(record, Collection("", "single")), self.images)
        self.assertEqual(result.detections[self.images[0]][0].box, (1, 2, 4, 6))
        data = {"0001.jpg": {"detections": record["detections"]}}
        result = import_results(data, guess_mapping(data, Collection("", "keyed")), self.images)
        self.assertEqual(result.matched_target_count, 1)

    def test_nested_custom_fields_and_pointer_escaping(self):
        data = {"payload": {"items": [{"meta": {"weird/name": "0001.jpg"},
                "hits": [{"bounds": {"left": 1, "top": 2, "right": 5, "bottom": 6}, "p~value": ".7"}]}]}}
        mapping = FieldMapping(Collection("/payload/items"), "/meta/weird~1name", "/hits", "xyxy", None,
                               ("/bounds/left", "/bounds/top", "/bounds/right", "/bounds/bottom"), None, "/p~0value")
        result = import_results(data, mapping, self.images)
        self.assertEqual(result.detections[self.images[0]][0].score, .7)
        self.assertEqual(read_pointer({"a/b": {"~x": 1}}, "/a~1b/~0x"), 1)

    def test_single_image_with_custom_filename_key_can_be_mapped(self):
        data = {"custom_image": "0001.jpg", "hits": [{"bbox": [0, 0, 5, 5]}]}
        self.assertIn(Collection("", "single"), discover_collections(data))
        mapping = FieldMapping(Collection("", "single"), "/custom_image", "/hits", box_field="/bbox")
        self.assertEqual(import_results(data, mapping, self.images).matched_target_count, 1)

    def test_unmatched_name_retains_extension(self):
        data = [{"name": "0001.png", "bbox": [0, 0, 10, 10]}]
        result = import_results(data, guess_mapping(data, Collection("")), self.images)
        self.assertEqual(result.matched_target_count, 0)
        self.assertEqual(result.issue_counts["图片未匹配"], 1)

    def test_duplicate_names_are_never_assigned_arbitrarily(self):
        data = [{"name": "0001.jpg", "bbox": [0, 0, 10, 10]}]
        result = import_results(data, guess_mapping(data, Collection("")),
                                [Path("a/0001.jpg"), Path("b/0001.JPG")])
        self.assertEqual(result.referenced, set())
        self.assertEqual(result.issue_counts["同名歧义"], 1)

    def test_invalid_records_do_not_drop_valid_neighbors(self):
        data = [
            {"name": "0001.jpg", "bbox": [0, 0, 5, 5], "score": .7},
            {"name": "0001.jpg", "bbox": [5, 0, 0, 5], "score": .7},
            {"name": "0001.jpg", "bbox": [0, 0, 5, 5]},
            {"name": "0001.jpg", "bbox": [0, 0, float("nan"), 5], "score": .7},
            {"name": None, "bbox": [0, 0, 5, 5], "score": .7},
            42,
        ]
        result = import_results(data, guess_mapping(data, Collection("")), self.images)
        self.assertEqual(result.matched_target_count, 1)
        self.assertEqual(result.issue_counts["目标数据无效"], 3)
        self.assertEqual(result.issue_counts["图片字段无效"], 2)
        self.assertEqual(result.issues[0].record_index, 2)

    def test_missing_or_wrong_target_list_is_reported(self):
        data = [{"path": "0001.jpg", "detections": "invalid"}, {"path": "0002.png"}]
        mapping = FieldMapping(Collection(""), "/path", "/detections", box_field="/bbox")
        result = import_results(data, mapping, self.images)
        self.assertEqual(result.issue_counts["目标列表无效"], 2)
        self.assertFalse(result.referenced)

    def test_box_numbers_and_dimensions_are_validated(self):
        mapping = FieldMapping(Collection(""), "/name", box_field="/bbox", box_format="xywh")
        for box in ([0, 0, -1, 5], [0, 0, 1, 0], [True, 0, 5, 5], [0, 0, "bad", 5], [0, 0, 1e309, 5], [0, 0, 1]):
            with self.subTest(box=box), self.assertRaises(ValueError):
                parse_detection({"bbox": box}, mapping, 1, 1)
        self.assertEqual(parse_detection({"bbox": ["1", "2", "3", "4"]}, mapping, 1, 1).box, (1, 2, 4, 6))

    def test_optional_attributes_and_original_confidence_scale(self):
        data = [{"name": "0001.jpg", "bbox": [0, 0, 2, 3], "conf": 85}]
        mapping = guess_mapping(data, Collection(""))
        self.assertEqual(import_results(data, mapping, self.images).detections[self.images[0]][0].score, 85)
        result = import_results(data, replace(mapping, score_field=None), self.images)
        self.assertIsNone(result.detections[self.images[0]][0].score)

    def test_entirely_empty_results_can_be_imported_without_box_fields(self):
        data = [{"path": "0001.jpg", "detections": []}]
        result = import_results(data, guess_mapping(data, Collection("")), self.images)
        self.assertEqual(result.referenced, {self.images[0]})
        self.assertEqual(result.matched_target_count, 0)
        result = import_results([], FieldMapping(Collection(""), ""), self.images)
        self.assertEqual(result.record_count, 0)

    def test_box_dictionary_coordinates_are_guessed(self):
        data = [{"name": "0001.jpg", "bbox": {"xmin": 1, "ymin": 2, "xmax": 3, "ymax": 4}}]
        mapping = guess_mapping(data, Collection(""))
        self.assertEqual(mapping.coordinates[0], "/bbox/xmin")
        self.assertEqual(import_results(data, mapping, self.images).matched_target_count, 1)

    def test_empty_bbox_does_not_turn_flat_records_into_grouped_records(self):
        data = [{"name": "0001.jpg", "bbox": []}, {"name": "0001.jpg", "bbox": [0, 0, 2, 3]}]
        mapping = guess_mapping(data, Collection(""))
        self.assertIsNone(mapping.detections_field)
        result = import_results(data, mapping, self.images)
        self.assertEqual(result.matched_target_count, 1)
        self.assertEqual(result.issue_counts["目标数据无效"], 1)

    def test_scanning_formats_recursive_and_natural_order(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            for name in ("10.JPG", "2.jpg", "1.png", "not-image.txt"):
                (folder / name).touch()
            (folder / "子目录").mkdir()
            (folder / "子目录" / "nested.JPEG").touch()
            self.assertEqual([p.name for p in scan_images(folder, [".jpg"])], ["2.jpg", "10.JPG"])
            self.assertEqual(len(scan_images(folder, [".jpg", ".jpeg"], True)), 3)
            self.assertEqual(scan_images(folder, []), [])
            with self.assertRaises(ValueError):
                scan_images(folder / "missing", [".jpg"])

    def test_json_bom_and_invalid_json_location(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "results.json"
            path.write_text('[{"name": "测试.jpg"}]', encoding="utf-8-sig")
            self.assertEqual(load_json(path)[0]["name"], "测试.jpg")
            path.write_text('[\n invalid]', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "第 2 行"):
                load_json(path)


if __name__ == "__main__":
    unittest.main()

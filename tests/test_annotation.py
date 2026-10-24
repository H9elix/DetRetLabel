import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from detretlabel.annotation import AnnotationStore, ReviewConfig, ReviewTarget, source_signature
from detretlabel.core import Collection, Detection, FieldMapping


class AnnotationDataTests(unittest.TestCase):
    def test_pending_algorithm_reason_draft_roundtrips_and_confirmation_is_undoable(self):
        with tempfile.TemporaryDirectory() as temp:
            store, target = AnnotationStore(Path(temp)), self.target()
            store.set_annotation(target, "false_positive", note="未完成备注", reason_pending=True, reason_draft="原因草稿")
            store.save()
            restored = AnnotationStore.load(Path(temp))
            self.assertTrue(restored.get(target.id)["reason_pending"])
            self.assertEqual(restored.get(target.id)["reason_draft"], "原因草稿")
            self.assertEqual(restored.get(target.id)["note"], "未完成备注")
            restored.set_annotation(target, "false_positive", "确认原因", "未完成备注")
            self.assertNotIn("reason_pending", restored.get(target.id))
            restored.undo()
            self.assertTrue(restored.get(target.id)["reason_pending"])
            self.assertEqual(restored.get(target.id)["reason_draft"], "原因草稿")

    def target(self):
        return ReviewTarget.from_detection("image.jpg", "algorithm", Path("result.json"), "signature",
            Detection((1, 2, 30, 40), "person", .9, 1, 1, {}))

    def test_identity_separates_sources_and_mapping_versions(self):
        target = self.target()
        self.assertEqual(target.id, self.target().id)
        gt = ReviewTarget.from_detection("image.jpg", "ground_truth", None, "signature",
            Detection((1, 2, 30, 40), "person", .9, 1, 1, {}))
        self.assertNotEqual(target.id, gt.id)
        mapping = FieldMapping(Collection(""), "/name", box_field="/bbox")
        self.assertEqual(source_signature([{"a": 1, "b": 2}], mapping), source_signature([{"b": 2, "a": 1}], mapping))
        self.assertNotEqual(source_signature([{"a": 1}], mapping), source_signature([{"a": 2}], mapping))

    def test_save_reload_reasons_notes_and_manual_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            store = AnnotationStore(folder)
            target = self.target()
            store.set_annotation(target, "false_positive", "自定义原因", "中文备注")
            manual = ReviewTarget.manual("image.jpg", (5, 6, 20, 30), "车")
            store.set_annotation(manual, "false_negative", "未检出")
            store.save()
            loaded = AnnotationStore.load(folder)
            self.assertEqual(loaded.get(target.id)["reason"], "自定义原因")
            self.assertEqual(loaded.get(target.id)["note"], "中文备注")
            self.assertEqual(loaded.manual_targets[manual.id].box, manual.box)
            self.assertTrue(loaded.completed(manual.id))
            self.assertFalse(loaded.dirty)

    def test_state_change_clears_inapplicable_reason_and_clear_is_undoable(self):
        store, target = AnnotationStore(), self.target()
        store.set_annotation(target, "false_positive", "种类检错", "备注")
        store.set_annotation(target, "true_positive", "不应保留", "备注")
        self.assertEqual(store.get(target.id)["reason"], "")
        store.clear(target.id)
        self.assertFalse(store.completed(target.id))
        self.assertEqual(store.undo(), target.id)
        self.assertEqual(store.get(target.id)["status"], "true_positive")
        store.undo()
        self.assertEqual(store.get(target.id)["reason"], "种类检错")

    def test_manual_creation_deletion_and_undo(self):
        store = AnnotationStore()
        target = ReviewTarget.manual("image.jpg", (0, 0, 4, 4))
        store.set_annotation(target, "false_negative")
        store.delete_manual(target.id)
        self.assertNotIn(target.id, store.manual_targets)
        store.undo()
        self.assertIn(target.id, store.manual_targets)
        store.undo()
        self.assertNotIn(target.id, store.manual_targets)

    def test_failed_atomic_replace_preserves_old_file_and_dirty_data(self):
        with tempfile.TemporaryDirectory() as temp:
            store, target = AnnotationStore(Path(temp)), self.target()
            store.set_annotation(target, "true_positive")
            store.save()
            old = store.path.read_bytes()
            store.set_annotation(target, "false_negative", "未检出")
            with patch("detretlabel.annotation.os.replace", side_effect=PermissionError("blocked")):
                with self.assertRaises(PermissionError):
                    store.save()
            self.assertEqual(store.path.read_bytes(), old)
            self.assertTrue(store.dirty)
            self.assertFalse(list(store.path.parent.glob("*.tmp")))

    def test_broken_existing_file_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            file = folder / ".detretlabel/annotations.json"
            file.parent.mkdir()
            file.write_text('{"version": 999}', encoding="utf-8")
            with self.assertRaises(ValueError):
                AnnotationStore.load(folder)
            self.assertEqual(json.loads(file.read_text())["version"], 999)

    def test_reason_config_accepts_import_and_rejects_bad_types_and_conflicts(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "config.json"
            valid = {"reasons": {"false_positive": ["种类检错", "种类检错"], "false_negative": []}}
            path.write_text(json.dumps(valid), encoding="utf-8")
            config = ReviewConfig.load(path)
            self.assertEqual(config.reasons["false_positive"], ("种类检错",))
            self.assertEqual(config.shortcuts["decision_1"], "1")
            self.assertEqual(config.shortcuts["toggle_ground_truth"], "G")
            for invalid in ({"reasons": {"false_positive": "invalid"}},
                            {"shortcuts": {"decision_1": "2"}},
                            {"shortcuts": {"toggle_ground_truth": "B"}}, {"auto_advance": "yes"}):
                path.write_text(json.dumps(invalid), encoding="utf-8")
                with self.assertRaises(ValueError):
                    ReviewConfig.load(path)

    def test_bulk_transaction_restores_all_targets_and_review_state_in_one_undo(self):
        store, first = AnnotationStore(), self.target()
        second = ReviewTarget.manual("image.jpg", (10, 20, 50, 70), "dog")
        store.set_annotation(first, "false_positive", "bad class", "note")
        store.set_image_review("image.jpg", "signature", True)
        history_size = len(store.history)
        with store.transaction(first.id):
            store.set_annotation(first, "true_positive", note="note")
            store.set_annotation(second, "false_negative")
        self.assertEqual(len(store.history), history_size + 1)
        self.assertFalse(store.review_completed("image.jpg", "signature"))
        store.undo()
        self.assertEqual(store.get(first.id)["reason"], "bad class")
        self.assertNotIn(second.id, store.manual_targets)
        self.assertTrue(store.review_completed("image.jpg", "signature"))

    def test_image_review_roundtrip_and_version_one_migration_keep_gt_out_of_decisions(self):
        with tempfile.TemporaryDirectory() as temp:
            store, target = AnnotationStore(Path(temp)), self.target()
            store.set_annotation(target, "true_positive")
            store.set_image_review("image.jpg", "sig", True)
            store.save()
            loaded = AnnotationStore.load(Path(temp))
            self.assertTrue(loaded.review_completed("image.jpg", "sig"))
            self.assertFalse(loaded.review_completed("image.jpg", "changed"))
            data = json.loads(store.path.read_text(encoding="utf-8"))
            data["version"] = 1
            gt = ReviewTarget.from_detection("image.jpg", "ground_truth", None, "gt", Detection((1, 2, 30, 40), "person", None, 1, 1, {}))
            data["annotations"][gt.id] = {"target": gt.to_dict(), "status": "false_negative", "reason": "old", "note": "reference"}
            store.path.write_text(json.dumps(data), encoding="utf-8")
            loaded = AnnotationStore.load(Path(temp))
            self.assertNotIn(gt.id, loaded.records)
            self.assertIn(gt.id, loaded.reference_annotations)
            with self.assertRaises(ValueError):
                loaded.set_annotation(gt, "false_negative")

    def test_transaction_rolls_back_on_failure(self):
        store, target = AnnotationStore(), self.target()
        with self.assertRaises(RuntimeError):
            with store.transaction(target.id):
                store.set_annotation(target, "true_positive")
                store.set_image_review("image.jpg", "sig", True)
                raise RuntimeError("rollback")
        self.assertFalse(store.records)
        self.assertFalse(store.image_reviews)
        self.assertFalse(store.history)
        self.assertFalse(store.dirty)

    def test_clear_image_removes_manual_history_notes_and_review_as_one_undo(self):
        store, target = AnnotationStore(), self.target()
        store.set_annotation(target, "false_positive", "种类检错", "备注")
        old = ReviewTarget("old", "IMAGE.JPG", "algorithm", target.box, "old")
        other = ReviewTarget("other", "another.jpg", "algorithm", target.box, "car")
        manual = ReviewTarget.manual("image.jpg", (0, 0, 5, 5), "行人")
        store.set_annotation(old, "true_positive")
        store.set_annotation(other, "true_positive")
        store.set_annotation(manual, "false_negative", "完全未检出")
        store.reference_annotations["archived-gt"] = {"target": {"image": "image.jpg"}, "note": "历史参考"}
        store.set_image_review("image.jpg", "sig", True)
        store.set_image_review("another.jpg", "sig", True)
        history = len(store.history)
        self.assertTrue(store.clear_annotations("image.jpg"))
        self.assertEqual(len(store.history), history + 1)
        self.assertEqual(set(store.records), {"other"})
        self.assertFalse(store.manual_targets)
        self.assertFalse(store.reference_annotations)
        self.assertFalse(store.has_annotations("image.jpg"))
        self.assertTrue(store.review_completed("another.jpg", "sig"))
        self.assertEqual(store.undo(), "image:image.jpg")
        self.assertEqual(store.get(target.id)["note"], "备注")
        self.assertIn("old", store.records)
        self.assertIn(manual.id, store.manual_targets)
        self.assertIn("archived-gt", store.reference_annotations)
        self.assertTrue(store.review_completed("image.jpg", "sig"))

    def test_clear_all_includes_unloaded_records_and_survives_save_reload(self):
        with tempfile.TemporaryDirectory() as temp:
            store, target = AnnotationStore(Path(temp)), self.target()
            manual = ReviewTarget.manual("hidden/unloaded.jpg", (0, 0, 5, 5))
            store.set_annotation(target, "true_positive")
            store.set_annotation(manual, "false_negative")
            store.set_image_review("empty.jpg", "sig", True)
            store.save()
            history = len(store.history)
            self.assertTrue(store.clear_annotations(focus_image="image.jpg"))
            self.assertEqual(len(store.history), history + 1)
            self.assertFalse(store.has_annotations())
            store.save()
            self.assertFalse(AnnotationStore.load(Path(temp)).has_annotations())
            self.assertFalse(store.clear_annotations())
            self.assertEqual(len(store.history), history + 1)
            store.undo()
            store.save()
            restored = AnnotationStore.load(Path(temp))
            self.assertIn(manual.id, restored.manual_targets)
            self.assertTrue(restored.completed(target.id))
            self.assertTrue(restored.review_completed("empty.jpg", "sig"))


if __name__ == "__main__":
    unittest.main()

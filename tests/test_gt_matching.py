import unittest
from threading import Event

from detretlabel.gt_matching import GTCandidate, box_iou, match_gt


class GTMatchingTests(unittest.TestCase):
    def test_iou_uses_xyxy_without_pixel_padding_or_clipping(self):
        self.assertEqual(box_iou((0, 0, 10, 10), (0, 0, 10, 10)), 1)
        self.assertAlmostEqual(box_iou((0, 0, 10, 10), (5, 0, 15, 10)), 1 / 3)
        self.assertEqual(box_iou((0, 0, 10, 10), (10, 0, 20, 10)), 0)
        self.assertEqual(box_iou((-10, -10, 0, 0), (-10, -10, 0, 0)), 1)

    def test_same_class_and_overall_winner_are_selected_independently(self):
        candidates = (GTCandidate(1, (0, 0, 10, 10), "car"),
                      GTCandidate(2, (1, 1, 9, 9), "person"),
                      GTCandidate(3, (2, 2, 8, 8), "person"))
        result = match_gt((0, 0, 10, 10), "person", candidates)
        self.assertEqual(result.same_index, 2)
        self.assertEqual(result.same_iou, .64)
        self.assertEqual(result.best_iou, 1)
        self.assertEqual(result.mismatch_index, 1)

    def test_global_same_winner_has_no_mismatch_and_ties_follow_gt_order(self):
        candidates = tuple(GTCandidate(i, (0, 0, 10, 10), "person") for i in (1, 2))
        result = match_gt((0, 0, 10, 10), "person", candidates)
        self.assertEqual(result.same_index, 1)
        self.assertEqual(result.best_index, 1)
        self.assertEqual(result.mismatch_index, 0)

    def test_zero_overlap_missing_types_and_empty_gt(self):
        result = match_gt((0, 0, 10, 10), "car", (GTCandidate(1, (50, 50, 60, 60), "car"),))
        self.assertEqual((result.same_index, result.best_index), (0, 0))
        result = match_gt((0, 0, 10, 10), None, (GTCandidate(1, (0, 0, 10, 10), None),))
        self.assertEqual(result.same_index, 0)
        self.assertEqual(result.mismatch_index, 1)
        self.assertEqual(match_gt((0, 0, 10, 10), "car", ()).best_index, 0)

    def test_cancelled_task_returns_without_matching(self):
        cancel = Event()
        cancel.set()
        self.assertIsNone(match_gt((0, 0, 10, 10), "car", (GTCandidate(1, (0, 0, 10, 10), "car"),), cancel))


if __name__ == "__main__":
    unittest.main()

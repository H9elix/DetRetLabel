import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest
from PySide6.QtWidgets import QApplication, QDialog
from detretlabel.export_dialogs import ManualCategoryDialog
from detretlabel.type_mapping import TypeMapping


class ManualCategoryDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_known_categories_keep_numeric_and_string_zero_distinct(self):
        for value in (0, "0"):
            dialog = ManualCategoryDialog(None, [0, "0", 0], TypeMapping({"0": "1"}))
            self.assertEqual(dialog.category.count(), 3)
            for index in range(1, dialog.category.count()):
                candidate = dialog.category.itemData(index)
                if type(candidate) is type(value):
                    dialog.category.setCurrentIndex(index)
                    break
            dialog.accept()
            self.assertEqual(dialog.result(), QDialog.DialogCode.Accepted)
            self.assertIs(type(dialog.value), type(value))
            self.assertEqual(dialog.value, value)

    def test_custom_numeric_text_defaults_to_string_and_numeric_type_is_explicit(self):
        for numeric in (False, True):
            dialog = ManualCategoryDialog(None, [], TypeMapping())
            dialog.category.setEditText("0")
            dialog.value_type.setCurrentIndex(int(numeric))
            dialog.accept()
            self.assertEqual(dialog.value, 0 if numeric else "0")

    def test_non_finite_or_non_numeric_category_is_rejected_in_numeric_mode(self):
        for text in ("NaN", "Infinity", "true", '"0"', "[]"):
            dialog = ManualCategoryDialog(None, [], TypeMapping())
            dialog.category.setEditText(text)
            dialog.value_type.setCurrentIndex(1)
            dialog.accept()
            self.assertNotEqual(dialog.result(), QDialog.DialogCode.Accepted)
            self.assertTrue(dialog.error.text())

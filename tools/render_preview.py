"""Render reproducible screenshots with synthetic test images, without opening a desktop window."""
from __future__ import annotations

import os
os.environ["QT_QPA_PLATFORM"] = "offscreen"

import sys
import copy
import json
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings, QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPen
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from detretlabel.app import MainWindow, STYLE
from detretlabel.core import Collection, guess_mapping, import_results, load_json
from detretlabel.mapping_dialog import MappingDialog


def main():
    root = Path(__file__).resolve().parents[1]
    output = root / "artifacts"
    images = output / "preview-data"
    images.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    # The Windows offscreen Qt plugin does not discover system fonts itself.
    if not QFontDatabase.families() and os.name == "nt":
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/msyh.ttc")
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    for filename in ("0001.jpg", "0002.png"):
        image = QImage(960, 600, QImage.Format.Format_RGB32)
        image.fill(QColor("#dbe6ef"))
        painter = QPainter(image)
        painter.setPen(QPen(QColor("#c0cfde"), 1))
        for x in range(0, 960, 40):
            painter.drawLine(x, 0, x, 600)
        for y in range(0, 600, 40):
            painter.drawLine(0, y, 960, y)
        painter.fillRect(QRect(100, 80, 240, 200), QColor("#8baaca"))
        painter.fillRect(QRect(400, 120, 70, 180), QColor("#627e9e"))
        painter.setPen(QColor("#35516d"))
        painter.setFont(QFont("Segoe UI", 30))
        painter.drawText(QRect(70, 420, 820, 70), Qt.AlignmentFlag.AlignLeft, f"TEST IMAGE / {filename}")
        painter.setFont(QFont("Segoe UI", 16))
        painter.drawText(QRect(70, 490, 820, 50), Qt.AlignmentFlag.AlignLeft, "Synthetic fixture - 960 x 600 pixels")
        painter.end()
        if not image.save(str(images / filename)):
            raise RuntimeError("Could not save fixture image")
    settings = QSettings(str(output / "preview-settings.ini"), QSettings.Format.IniFormat)
    settings.clear()
    window = MainWindow(settings)
    window.resize(1440, 960)
    window.show()
    window.scan_folder(images)
    deadline = time.monotonic() + 10
    while window.busy and time.monotonic() < deadline:
        app.processEvents()
        QTest.qWait(10)
    if window.busy:
        raise RuntimeError("Preview scan timed out")
    source = root / "examples" / "results_grouped.json"
    document = load_json(source)
    mapping = guess_mapping(document, Collection("/results"))
    state = window.sources["algorithm"]
    state.document, state.path, state.mapping = document, source, mapping
    state.result = import_results(document, mapping, window.images)
    gt_document = copy.deepcopy(document)
    for record in gt_document["results"]:
        for target in record["detections"]:
            target.pop("conf", None)
    gt = window.sources["ground_truth"]
    gt.document, gt.path = gt_document, output / "preview-ground-truth.json"
    gt.path.write_text(json.dumps(gt_document, ensure_ascii=False, indent=2), encoding="utf-8")
    gt.mapping = guess_mapping(gt_document, Collection("/results"))
    gt.result = import_results(gt_document, gt.mapping, window.images)
    window._refresh()
    app.processEvents()
    window.grab().save(str(output / "main-window.png"))
    window.folder_section.set_expanded(False)
    window.data_section.set_expanded(False)
    app.processEvents()
    window.grab().save(str(output / "sections-collapsed.png"))
    window.folder_section.set_expanded(True)
    window.data_section.set_expanded(True)
    dialog = MappingDialog(document, str(source), window)
    dialog.show()
    app.processEvents()
    dialog.grab().save(str(output / "field-mapping.png"))
    dialog.close()
    window.close()
    print(f"Screenshots: {output}")


if __name__ == "__main__":
    main()

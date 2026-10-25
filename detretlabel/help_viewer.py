"""Read-only, offline Markdown help with in-app document navigation."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTabWidget, QTextBrowser, QVBoxLayout,
)

from .app_info import AUTHOR, BUILD, RESOURCE_ROOT, VERSION


def heading_slug(text: str) -> str:
    text = "".join(c for c in text.lower() if not unicodedata.category(c).startswith("P") or c in "-_")
    return re.sub(r"\s", "-", text)


class HelpBrowser(QTextBrowser):
    """Keep documentation images readable without horizontal overflow."""
    def __init__(self):
        super().__init__()
        self._fit_pending = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not self._fit_pending:
            self._fit_pending = True
            QTimer.singleShot(0, self.fit_images)

    def fit_images(self):
        self._fit_pending = False
        document = self.document()
        maximum = max(1, self.viewport().width() - 2 * document.documentMargin())
        updates = []
        block = document.begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().isImageFormat():
                    format_ = fragment.charFormat().toImageFormat()
                    image = document.resource(QTextDocument.ResourceType.ImageResource, QUrl(format_.name()))
                    if image is not None and hasattr(image, "width") and image.width() > 0:
                        width = min(image.width(), maximum)
                        height = image.height() * width / image.width()
                        if abs(format_.width() - width) > .5 or abs(format_.height() - height) > .5:
                            format_.setWidth(width)
                            format_.setHeight(height)
                            updates.append((fragment.position(), fragment.length(), format_))
                iterator += 1
            block = block.next()
        for position, length, format_ in updates:
            cursor = QTextCursor(document)
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
            cursor.setCharFormat(format_)


class HelpDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("帮助 · DetRetLabel")
        self.resize(1060, 780)
        self.setMinimumSize(720, 500)
        self.root = RESOURCE_ROOT.resolve()
        self.paths: dict[QTextBrowser, Path] = {}
        layout = QVBoxLayout(self)
        title = QLabel("DetRetLabel · 使用帮助")
        title.setObjectName("dialogTitle")
        layout.addWidget(title)
        self.info = QLabel(f"作者：{AUTHOR}    版本：{VERSION}    打包：{BUILD}")
        self.info.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.info)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("在当前文档中查找…  Ctrl+F")
        self.search.setClearButtonEnabled(True)
        self.search.returnPressed.connect(self.find_next)
        search_row.addWidget(self.search, 1)
        find = QPushButton("查找下一个")
        find.clicked.connect(self.find_next)
        # Enter in the browser should not accidentally activate this button.
        find.setAutoDefault(False)
        search_row.addWidget(find)
        layout.addLayout(search_row)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.message = QLabel("文档随程序内置，可离线查看。")
        self.message.setObjectName("muted")
        self.message.setWordWrap(True)
        layout.addWidget(self.message)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText("关闭")
        buttons.button(QDialogButtonBox.StandardButton.Close).setAutoDefault(False)
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self.find_action = QAction(self)
        self.find_action.setShortcut(QKeySequence.StandardKey.Find)
        self.find_action.triggered.connect(self.focus_search)
        self.addAction(self.find_action)
        self.open_document(self.root / "README.md", "README.md")
        self.open_document(self.root / "docs" / "使用指南.md", "详细使用指南")
        self.tabs.setCurrentIndex(0)

    def focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def show_page(self, index: int) -> None:
        self.tabs.setCurrentIndex(index)
        self.show()
        self.raise_()
        self.activateWindow()

    def open_document(self, path: Path, title: str | None = None) -> QTextBrowser:
        path = path.resolve()
        for browser, existing in self.paths.items():
            if existing == path:
                self.tabs.setCurrentWidget(browser)
                return browser
        browser = HelpBrowser()
        browser.setOpenLinks(False)
        browser.setOpenExternalLinks(False)
        browser.setStyleSheet("QTextBrowser { background: white; border: 0; padding: 12px; }")
        browser.document().setDefaultStyleSheet(
            "h1 { color: #24344b; } h2 { color: #1f4e97; margin-top: 20px; } "
            "td, th { padding: 5px; } th { background-color: #eaf0f7; } "
            "pre { background-color: #f2f5f9; } a { color: #2563eb; }"
        )
        browser.document().setBaseUrl(QUrl.fromLocalFile(str(path)))
        self.paths[browser] = path
        try:
            text = path.read_text(encoding="utf-8-sig")
            if path.suffix.lower() == ".md":
                browser.setMarkdown(text)
                self._add_heading_anchors(browser)
            else:
                browser.setPlainText(text)
        except (OSError, UnicodeError) as exc:
            browser.setPlainText(f"无法读取帮助文档：{path.name}\n\n{exc}\n\n请检查程序文件是否完整。")
        browser.anchorClicked.connect(lambda url, b=browser: self.open_link(b, url))
        self.tabs.addTab(browser, title or path.name)
        self.tabs.setCurrentWidget(browser)
        browser.fit_images()
        return browser

    @staticmethod
    def _add_heading_anchors(browser: QTextBrowser) -> None:
        # QTextDocument renders Markdown headings without GitHub's implicit link anchors.
        counts = {}
        block = browser.document().begin()
        while block.isValid():
            if block.blockFormat().headingLevel():
                slug = heading_slug(block.text())
                count = counts.get(slug, 0)
                counts[slug] = count + 1
                anchor = slug + (f"-{count}" if count else "")
                cursor = QTextCursor(block)
                cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                format_ = QTextCharFormat()
                format_.setAnchor(True)
                format_.setAnchorNames([anchor])
                cursor.mergeCharFormat(format_)
            block = block.next()

    def open_link(self, browser: QTextBrowser, url: QUrl) -> None:
        resolved = browser.document().baseUrl().resolved(url)
        if resolved.scheme() in ("http", "https"):
            QDesktopServices.openUrl(resolved)
            return
        if not resolved.isLocalFile():
            self.message.setText("此链接无法在帮助预览中打开。")
            return
        target = Path(resolved.toLocalFile()).resolve()
        if not target.is_relative_to(self.root):
            self.message.setText("帮助预览只打开程序内附带的文档。")
            return
        if target != self.paths[browser]:
            if not target.is_file():
                self.message.setText("此文件未随程序附带；如需测试素材或构建源文件，请使用完整项目。")
                return
            if target.suffix.lower() not in (".md", ".txt", ".json", ".toml", ".bat", ".py", ".spec"):
                self.message.setText("此文件不能作为文字文档预览。")
                return
            browser = self.open_document(target)
        if resolved.fragment():
            browser.fit_images()
            browser.scrollToAnchor(resolved.fragment())
        self.message.setText("文档随程序内置，可离线查看。")

    def find_next(self) -> None:
        query = self.search.text().strip()
        browser = self.tabs.currentWidget()
        if not query or not isinstance(browser, QTextBrowser):
            return
        if not browser.find(query):
            cursor = browser.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            browser.setTextCursor(cursor)
            if not browser.find(query):
                self.message.setText(f"未找到“{query}”。")
                return
        self.message.setText(f"已找到“{query}”；按 Enter 查找下一处。")

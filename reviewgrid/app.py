"""A local, read-only desktop inbox for the PRs Brain controller."""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDir, QLockFile, QPoint, QPointF, QRectF, QSettings, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QGuiApplication, QLinearGradient, QPainter, QPen, QPolygonF
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QVBoxLayout, QWidget,
)

from .model import CONTROLLER_ID, DEFAULT_STATE, Review, build_action, load_reviews

STYLE = """
QWidget { background: transparent; color: #d9f7ff; font-family: 'Menlo'; font-size: 11px; }
QWidget#inbox { border: none; }
QLabel { border: none; background: transparent; }
QLabel#brand { color: #00f0ff; font-size: 11px; font-weight: 700; letter-spacing: 4px; }
QLabel#heading { color: #83f5ff; font-size: 24px; font-weight: 800; letter-spacing: 3px; }
QLabel#muted { color: #99abc6; font-size: 10px; }
QPushButton { background: #0b1b30; border: 1px solid #2b6e89; border-radius: 0; padding: 6px 9px; }
QPushButton:hover { background: #12304b; border-color: #ff39d2; color: #00f0ff; }
QPushButton:focus { border: 1px solid #00f0ff; }
QPushButton:checked { color: #00f0ff; border-color: #00f0ff; background: #14263b; }
QPushButton#review { background: qlineargradient(x1:0,y1:0,x2:1,y2:1,
    stop:0 #102039,stop:1 #0d1228); border: 1px solid #28617a; border-left: 3px solid #00d8ef; text-align: left; }
QPushButton#review:hover { background: #14243e; border-color: #bb50ff; border-left-color: #ff39d2; }
QScrollArea, QScrollArea QWidget { border: none; background: transparent; }
QScrollBar:vertical { background: #0c1628; width: 5px; margin: 0; }
QScrollBar::handle:vertical { background: #00d8ef; border-radius: 0; min-height: 25px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
"""


def label(text: str, name: str = "") -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    return widget


class DragHeader(QWidget):
    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()


class Horizon(QWidget):
    """Static wireframe data towers, painted without an animation timer."""
    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(86)
        self.setAccessibleName("Wireframe data towers above a neon grid")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = self.width()
        painter.setPen(QPen(QColor("#205275"), 0.7))
        for x in range(-width, width * 2, 38):
            painter.drawLine(QPointF(width * 0.68, 48), QPointF(x, 110))
        for y in (62, 67, 75, 85):
            painter.drawLine(QPointF(0, y), QPointF(width, y))
        for x, top, height in ((width - 88, 22, 38), (width - 54, 3, 52), (width - 126, 38, 25)):
            painter.setPen(QPen(QColor("#16bad9"), 1))
            painter.setBrush(QColor("#0a172c"))
            painter.drawRect(QRectF(x, top, 20, height))
            painter.drawPolygon(QPolygonF([QPointF(x, top), QPointF(x + 10, top - 7),
                                          QPointF(x + 30, top - 7), QPointF(x + 20, top)]))
            painter.drawPolygon(QPolygonF([QPointF(x + 20, top), QPointF(x + 30, top - 7),
                                          QPointF(x + 30, top + height - 7), QPointF(x + 20, top + height)]))
            painter.setPen(QPen(QColor("#7c42b5"), 0.8))
            for y in range(top + 8, top + height, 8):
                painter.drawLine(QPointF(x, y), QPointF(x + 20, y))
        painter.setPen(QPen(QColor("#00eaff"), 1))
        painter.drawLine(QPointF(0, 85), QPointF(width, 85))
        painter.end()


class ReviewWindow(QWidget):
    def __init__(self, state_path: Path, settings: QSettings,
                 opener: Callable[[QUrl], bool] = QDesktopServices.openUrl,
                 controller_id: str = CONTROLLER_ID) -> None:
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.state_path, self.settings, self.opener = state_path, settings, opener
        self.controller_id = controller_id
        self.reviews: list[Review] = []
        self.signature = None
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setObjectName("inbox")
        self.setWindowTitle("Review Grid")
        self.setAccessibleName("Review Grid tracked pull requests")
        self.setStyleSheet(STYLE)
        self.setFixedWidth(466)
        self.resize(466, 580)
        self._build()
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.setInterval(500)
        self.save_timer.timeout.connect(self.save_position)
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start(3000)
        self.refresh()
        self.pin.setChecked(settings.value("pinned", True, type=bool))
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.pin.isChecked())

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 12)
        outer.setSpacing(10)
        header = DragHeader()
        top = QHBoxLayout(header)
        top.setContentsMargins(0, 0, 0, 0)
        brand = label("// REVIEW GRID", "brand")
        brand.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        top.addWidget(brand)
        top.addStretch()
        self.pin = QPushButton("Pin")
        self.pin.setCheckable(True)
        self.pin.setToolTip("Keep this widget above other windows")
        self.pin.clicked.connect(self.toggle_pin)
        top.addWidget(self.pin)
        close = QPushButton("×")
        close.setAccessibleName("Close PR widget")
        close.clicked.connect(self.close)
        top.addWidget(close)
        outer.addWidget(header)
        horizon = Horizon()
        horizon_layout = QVBoxLayout(horizon)
        horizon_layout.setContentsMargins(0, 4, 100, 17)
        horizon_layout.addWidget(label("REVIEW INBOX", "heading"))
        outer.addWidget(horizon)
        self.summary = label("", "muted")
        outer.addWidget(self.summary)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_body = QWidget()
        self.rows = QVBoxLayout(self.list_body)
        self.rows.setContentsMargins(0, 0, 8, 0)
        self.rows.setSpacing(8)
        self.rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(self.list_body)
        outer.addWidget(scroll, 1)
        self.empty = label("No tracked PRs right now.")
        self.empty.setWordWrap(True)
        self.rows.addWidget(self.empty)
        self.health = label("", "muted")
        self.health.setWordWrap(True)
        outer.addWidget(self.health)
        self.feedback = label("Click a PR to open Codex and copy its context prompt.", "muted")
        self.feedback.setWordWrap(True)
        outer.addWidget(self.feedback)
        footer = QHBoxLayout()
        footer.addWidget(label("Approval stays in the controller chat", "muted"))
        footer.addStretch()
        brain = QPushButton("Controller ↗")
        brain.clicked.connect(self.open_controller)
        footer.addWidget(brain)
        outer.addLayout(footer)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        boundary = QPolygonF([QPointF(13, 1), QPointF(self.width() - 2, 1),
                              QPointF(self.width() - 2, self.height() - 13),
                              QPointF(self.width() - 14, self.height() - 1),
                              QPointF(1, self.height() - 1), QPointF(1, 13)])
        background = QLinearGradient(0, 0, self.width(), self.height())
        background.setColorAt(0, QColor("#071321"))
        background.setColorAt(1, QColor("#160b27"))
        painter.setBrush(background)
        painter.setPen(QPen(QColor("#144051"), 4))
        painter.drawPolygon(boundary)
        painter.setClipRegion(self.rect())
        painter.setPen(QPen(QColor(42, 114, 146, 22), 0.7))
        for x in range(-self.width(), self.width() * 2, 65):
            painter.drawLine(QPointF(self.width() / 2, 140), QPointF(x, self.height()))
        for y in range(160, self.height(), 30):
            painter.drawLine(QPointF(8, y), QPointF(self.width() - 8, y))
        painter.setPen(QPen(QColor("#00d3ec"), 1))
        painter.drawPolygon(boundary)
        painter.setPen(QPen(QColor("#d236ec"), 2))
        painter.drawLine(QPointF(self.width() - 2, 45), QPointF(self.width() - 2, 130))
        painter.drawLine(QPointF(1, self.height() - 40), QPointF(1, self.height() - 13))
        painter.end()

    def refresh(self) -> None:
        try:
            reviews = load_reviews(self.state_path)
            modified = self.state_path.stat().st_mtime
        except (OSError, ValueError, UnicodeError):
            self.health.setText("Queue unavailable · showing last good list. Retrying every 3 seconds.")
            self.health.setStyleSheet("color: #ffb36b")
            return
        self.health.setStyleSheet("")
        stamp = datetime.fromtimestamp(modified).astimezone().strftime("%I:%M %p").lstrip("0")
        self.health.setText(f"Queue updated {stamp} · local refresh every 3 sec")
        signature = tuple(reviews)
        if signature == self.signature:
            return
        self.signature, self.reviews = signature, reviews
        for index in reversed(range(self.rows.count())):
            widget = self.rows.itemAt(index).widget()
            if widget is not self.empty:
                self.rows.removeWidget(widget)
                widget.deleteLater()
        self.empty.setVisible(not reviews)
        ready = sum(row.status in {"awaiting-approval", "clean"} for row in reviews)
        running = sum(row.status == "reviewing" for row in reviews)
        self.summary.setText(f"{len(reviews)} tracked   ·   {running} reviewing   ·   {ready} ready for you")
        for review in reviews:
            self.rows.addWidget(self.review_card(review))

    def review_card(self, review: Review) -> QPushButton:
        button = QPushButton()
        button.setObjectName("review")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(f"{review.key}\n{review.title}\nOpen Codex + copy context prompt")
        button.setAccessibleName(f"{review.key}, {review.label}. Open Codex and copy prompt")
        box = QVBoxLayout(button)
        box.setContentsMargins(12, 10, 12, 10)
        box.setSpacing(5)
        head = QHBoxLayout()
        repo = label(review.key.split("/", 1)[1])
        repo.setStyleSheet("color: #71f0f5; font-weight: 650; font-size: 10px")
        head.addWidget(repo)
        head.addStretch()
        status = label("● " + review.label)
        status.setStyleSheet(f"color: {review.color}; font-size: 10px")
        head.addWidget(status)
        box.addLayout(head)
        title = label(review.title[:160] + ("…" if len(review.title) > 160 else ""))
        title.setWordWrap(True)
        box.addWidget(title)
        if review.followup:
            box.addWidget(label("Follow-up review", "muted"))
        # QPushButton's native size hint ignores its child layout, so reserve the card's content height.
        button.setMinimumHeight(max(110, box.totalHeightForWidth(390)))
        for child in button.findChildren(QLabel):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        button.clicked.connect(lambda checked=False: self.activate_review(review))
        return button

    def activate_review(self, review: Review) -> None:
        try:
            url, prompt = build_action(review, self.controller_id)
        except ValueError as exc:
            self.feedback.setText(str(exc))
            return
        QApplication.clipboard().setText(prompt)
        if self.opener(QUrl(url)):
            self.feedback.setText("Prompt copied. Paste into Codex, then Send when ready.")
        else:
            self.feedback.setText("Could not open Codex. Prompt copied; use Open brain or open the app.")

    def open_controller(self) -> None:
        from .model import _thread_id
        controller = _thread_id(self.controller_id)
        if not controller:
            self.feedback.setText("Set --controller-thread to your Codex controller chat UUID.")
            return
        if not self.opener(QUrl(f"codex://threads/{controller}")):
            self.feedback.setText("Could not open Codex. Open the app and choose your controller chat.")

    def toggle_pin(self) -> None:
        position = self.pos()
        self.settings.setValue("pinned", self.pin.isChecked())
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, self.pin.isChecked())
        self.show()
        self.move(position)

    def place(self) -> None:
        saved = self.settings.value("position")
        if isinstance(saved, QPoint) and QGuiApplication.screenAt(saved + QPoint(40, 40)):
            self.move(saved)
            return
        screen = QGuiApplication.primaryScreen().availableGeometry()
        self.move(screen.right() - self.width() - 24, screen.top() + 70)

    def save_position(self) -> None:
        self.settings.setValue("position", self.pos())
        self.settings.sync()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if self.isVisible() and hasattr(self, "save_timer"):
            self.save_timer.start()

    def closeEvent(self, event) -> None:
        self.save_timer.stop()
        self.save_position()
        super().closeEvent(event)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--controller-thread", default=CONTROLLER_ID, help="Codex controller chat UUID (or REVIEWGRID_CONTROLLER)")
    args = parser.parse_args()
    app = QApplication([sys.argv[0]])
    app.setApplicationName("Review Grid")
    name = "pr-brain-widget-" + hashlib.sha256(str(args.state.resolve()).encode()).hexdigest()[:16]
    lock = QLockFile(str(Path(QDir.tempPath()) / (name + ".lock")))
    if not lock.tryLock(100):
        socket = QLocalSocket()
        socket.connectToServer(name)
        socket.waitForConnected(500)
        socket.write(b"show")
        socket.waitForBytesWritten(500)
        return 0
    server = QLocalServer()
    QLocalServer.removeServer(name)
    server.listen(name)
    window = ReviewWindow(args.state.expanduser(), QSettings("reviewgrid", "desktop"), controller_id=args.controller_thread)

    def focus() -> None:
        socket = server.nextPendingConnection()
        if socket:
            socket.close()
            socket.deleteLater()
        window.showNormal()
        window.raise_()
        window.activateWindow()

    server.newConnection.connect(focus)
    window.show()
    # On macOS, restoring a frameless position before show() causes titlebar-height drift.
    window.place()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

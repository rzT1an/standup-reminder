import argparse
import os
import sys

from PySide6.QtCore import (
    QEasingCurve,
    QEventLoop,
    QPoint,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QPropertyAnimation,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QApplication, QLabel, QWidget


APP_DIR = os.path.dirname(__file__)
PREVIEW_PATH = os.path.join(APP_DIR, "assets", "qt_popup_preview.png")


def dp(value):
    return int(round(value))


def bubble_path(rect, radius, tail_x, tail_width, tail_height):
    body = QRectF(rect)
    body.setHeight(body.height() - tail_height)

    path = QPainterPath()
    path.addRoundedRect(body, radius, radius)

    base_y = body.bottom() - 0.5
    tail = QPainterPath()
    tail.moveTo(tail_x - tail_width / 2, base_y)
    tail.lineTo(tail_x, body.bottom() + tail_height)
    tail.lineTo(tail_x + tail_width / 2, base_y)
    tail.closeSubpath()
    path = path.united(tail)
    return path


class PillButton(QWidget):
    def __init__(self, text, primary=False, parent=None):
        super().__init__(parent)
        self.text = text
        self.primary = primary
        self.hovered = False
        self.setCursor(Qt.PointingHandCursor)

    def enterEvent(self, event):
        self.hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = rect.height() / 2

        if self.primary:
            gradient = QLinearGradient(rect.topLeft(), rect.topRight())
            if self.hovered:
                gradient.setColorAt(0.0, QColor("#5F78D6"))
                gradient.setColorAt(0.56, QColor("#806BC4"))
                gradient.setColorAt(1.0, QColor("#609879"))
            else:
                gradient.setColorAt(0.0, QColor("#6D8FE6"))
                gradient.setColorAt(0.58, QColor("#8B79D2"))
                gradient.setColorAt(1.0, QColor("#6BAE8B"))
            painter.setPen(Qt.NoPen)
            painter.setBrush(gradient)
            painter.drawRoundedRect(rect, radius, radius)
            text_color = QColor("#FFFFFF")
        else:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#E9ECEF" if self.hovered else "#F1F3F4"))
            painter.drawRoundedRect(rect, radius, radius)
            text_color = QColor("#30343B")

        font = QFont("Microsoft YaHei UI", 9)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(text_color)
        painter.drawText(self.rect(), Qt.AlignCenter, self.text)


class QtReminderPopup(QWidget):
    def __init__(self, minutes=30, snooze_minutes=5, snooze_enabled=True, parent=None):
        super().__init__(parent)
        self.minutes = minutes
        self.snooze_minutes = snooze_minutes
        self.snooze_enabled = snooze_enabled
        self.result = "done"
        self.finished_callback = None
        self.drag_pos = None
        self.base_w = 420
        self.base_h = 236
        self.tail_h = 22
        self.radius = 28
        self.tail_x = self.base_w - 70

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
            | Qt.NoDropShadowWindowHint
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating, False)
        self.setFixedSize(self.base_w, self.base_h)

        self.snooze_btn = PillButton(f"稍后 {self.snooze_minutes} 分钟", primary=False, parent=self)
        self.done_btn = PillButton("我现在起身", primary=True, parent=self)
        self.close_btn = QLabel("×", self)
        self.close_btn.setAlignment(Qt.AlignCenter)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setStyleSheet(
            "QLabel { color: #8A9099; font: 18px 'Segoe UI'; border-radius: 12px; }"
            "QLabel:hover { background: #F1F3F4; color: #5F6368; }"
        )

        self.snooze_btn.setGeometry(168, 156, 108, 34)
        self.done_btn.setGeometry(290, 156, 100, 34)
        self.close_btn.setGeometry(self.base_w - 46, 28, 28, 28)

        if not self.snooze_enabled:
            self.snooze_btn.hide()
            self.done_btn.setGeometry(156, 156, 190, 34)

        self.snooze_btn.mouseReleaseEvent = lambda event: self.finish("snooze")
        self.done_btn.mouseReleaseEvent = lambda event: self.finish("done")
        self.close_btn.mouseReleaseEvent = lambda event: self.finish("done")

        self.opacity_anim = QPropertyAnimation(self, b"windowOpacity", self)
        self.opacity_anim.setDuration(180)
        self.opacity_anim.setStartValue(0.0)
        self.opacity_anim.setEndValue(1.0)
        self.opacity_anim.setEasingCurve(QEasingCurve.OutCubic)

    def move_to_anchor(self, anchor=None):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        if anchor:
            anchor = normalize_anchor(anchor, screen)
            x = int(anchor[0] - self.tail_x)
            y = int(anchor[1] - self._tail_tip_y())
        else:
            x = screen.right() - self.width() - 64
            y = screen.bottom() - self.height() - 190
        x = max(screen.left() + 12, min(x, screen.right() - self.width() - 12))
        y = max(screen.top() + 12, min(y, screen.bottom() - self.height() - 12))
        self.move(x, y)

    def show_at_anchor(self, anchor=None):
        self.move_to_anchor(anchor)
        self.setWindowOpacity(0)
        self.show()
        self.raise_()
        self.activateWindow()
        self.opacity_anim.start()

    def _tail_tip_y(self):
        rect = QRectF(10, 10, self.width() - 20, self.height() - 24)
        return rect.top() + (rect.height() - self.tail_h) + self.tail_h

    def finish(self, result):
        self.result = result
        if self.finished_callback:
            self.finished_callback(result)
        self.close()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_pos and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.drag_pos = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)

        rect = QRectF(10, 10, self.width() - 20, self.height() - 24)
        path = bubble_path(rect, self.radius, rect.left() + self.tail_x - 10, 34, self.tail_h)

        for i, alpha in enumerate((24, 16, 9, 5)):
            offset = 4 + i * 2
            shadow = QPainterPath(path)
            shadow.translate(0, offset)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(16, 24, 40, alpha))
            painter.drawPath(shadow)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawPath(path)
        painter.setPen(QPen(QColor("#E7EAEE"), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        gradient = QLinearGradient(46, 134, self.width() - 46, 134)
        for pos, color in (
            (0.0, "#6D8FE6"),
            (0.30, "#A47ACF"),
            (0.52, "#D77D75"),
            (0.75, "#E5B35B"),
            (1.0, "#70A874"),
        ):
            gradient.setColorAt(pos, QColor(color))
        painter.setPen(QPen(gradient, 2.2))
        painter.drawLine(46, 134, self.width() - 46, 134)

        tag_font = QFont("Microsoft YaHei UI", 9)
        tag_font.setWeight(QFont.DemiBold)
        painter.setFont(tag_font)
        painter.setPen(QColor("#5F6368"))
        painter.drawText(46, 56, "久坐提醒")

        pill = QRectF(self.width() - 124, 28, 68, 28)
        painter.setPen(QPen(QColor("#E7EAEE"), 1))
        painter.setBrush(QColor("#F6F8FA"))
        painter.drawRoundedRect(pill, 14, 14)
        painter.setPen(QColor("#5F6368"))
        painter.drawText(pill, Qt.AlignCenter, f"{self.minutes} 分钟")

        title_font = QFont("Microsoft YaHei UI", 22)
        title_font.setWeight(QFont.Black)
        painter.setFont(title_font)
        painter.setPen(QColor("#202124"))
        painter.drawText(46, 88, "该起身活动一下了")

        body_font = QFont("Microsoft YaHei UI", 10)
        painter.setFont(body_font)
        painter.setPen(QColor("#5F6368"))
        body = QRectF(46, 102, self.width() - 92, 42)
        painter.drawText(
            body,
            Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
            "离开屏幕两分钟，伸展肩颈，走几步，顺手喝点水。",
        )


class QtHint(QWidget):
    def __init__(self, text="腰挺直一点。"):
        super().__init__()
        self.text = text
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(132, 36)

    def move_to_anchor(self, anchor=None):
        screen = QGuiApplication.primaryScreen().availableGeometry()
        if anchor:
            anchor = normalize_anchor(anchor, screen)
            x = int(anchor[0] - self.width() / 2)
            y = int(anchor[1] - self.height())
        else:
            x = screen.right() - self.width() - 64
            y = screen.bottom() - self.height() - 190
        x = max(screen.left() + 12, min(x, screen.right() - self.width() - 12))
        y = max(screen.top() + 12, min(y, screen.bottom() - self.height() - 12))
        self.move(x, y)

    def show_at_anchor(self, anchor=None):
        self.move_to_anchor(anchor)
        self.show()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        rect = QRectF(2, 2, self.width() - 4, self.height() - 4)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(16, 24, 40, 18))
        painter.drawRoundedRect(rect.translated(0, 2), 16, 16)
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(rect, 16, 16)
        painter.setPen(QPen(QColor("#E7EAEE"), 1))
        painter.drawRoundedRect(rect, 16, 16)

        font = QFont("Microsoft YaHei UI", 9)
        font.setWeight(QFont.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor("#202124"))
        painter.drawText(self.rect(), Qt.AlignCenter, self.text)


def render_preview():
    os.makedirs(os.path.dirname(PREVIEW_PATH), exist_ok=True)
    app = QApplication.instance() or QApplication(sys.argv)
    popup = QtReminderPopup()
    hint = QtHint()

    canvas = QPixmap(580, 330)
    canvas.fill(QColor("#EEF2F7"))
    painter = QPainter(canvas)
    painter.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)

    popup_pixmap = QPixmap(popup.size())
    popup_pixmap.fill(Qt.transparent)
    popup.render(popup_pixmap)
    painter.drawPixmap(70, 48, popup_pixmap)

    hint_pixmap = QPixmap(hint.size())
    hint_pixmap.fill(Qt.transparent)
    hint.render(hint_pixmap)
    painter.drawPixmap(340, 250, hint_pixmap)
    painter.end()
    canvas.save(PREVIEW_PATH)
    print(PREVIEW_PATH)
    app.quit()


def normalize_anchor(anchor, screen):
    x, y = anchor
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            px = user32.GetSystemMetrics(76)
            py = user32.GetSystemMetrics(77)
            pw = user32.GetSystemMetrics(78)
            ph = user32.GetSystemMetrics(79)
            if pw > 0 and ph > 0:
                if x > screen.right() + 8 or y > screen.bottom() + 8:
                    x = screen.left() + (x - px) * (screen.width() / pw)
                    y = screen.top() + (y - py) * (screen.height() / ph)
        except Exception:
            pass
    return x, y


def read_anchor_file(path):
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read().strip()
        x, y = text.split(",", 1)
        return float(x), float(y)
    except Exception:
        return None


def show_qt_popup(minutes=30, snooze_minutes=5, snooze_enabled=True,
                  anchor=None, timeout_seconds=300, anchor_file=None):
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    loop = QEventLoop()
    popup = QtReminderPopup(minutes, snooze_minutes, snooze_enabled)
    result = {"value": "done"}
    popup.finished_callback = lambda value: (result.update(value=value), loop.quit())
    popup.show_at_anchor(anchor)
    follow_timer = QTimer(popup)
    follow_timer.setInterval(33)

    def follow_anchor():
        latest = read_anchor_file(anchor_file)
        if latest is not None:
            popup.move_to_anchor(latest)

    if anchor_file:
        follow_timer.timeout.connect(follow_anchor)
        follow_timer.start()

    QTimer.singleShot(max(1, int(timeout_seconds * 1000)), lambda: popup.finish("done"))
    loop.exec()
    return result["value"]


def show_qt_hint(text="腰挺直一点。", duration=8, anchor=None, anchor_file=None):
    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    hint = QtHint(text)
    hint.show_at_anchor(anchor)
    follow_timer = QTimer(hint)
    follow_timer.setInterval(33)

    def follow_anchor():
        latest = read_anchor_file(anchor_file)
        if latest is not None:
            hint.move_to_anchor(latest)

    if anchor_file:
        follow_timer.timeout.connect(follow_anchor)
        follow_timer.start()

    QTimer.singleShot(max(1, int(duration * 1000)), app.quit)
    app.exec()


def run_demo():
    app = QApplication(sys.argv)
    popup = QtReminderPopup()
    popup.show_at_anchor()

    hint = QtHint()
    hint.move(popup.x() + popup.width() - 170, popup.y() + popup.height() + 8)
    hint.show()
    QTimer.singleShot(6000, hint.close)

    app.exec()
    return 0 if popup.result == "done" else 2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--render-preview", action="store_true")
    parser.add_argument("--popup", action="store_true")
    parser.add_argument("--hint", action="store_true")
    parser.add_argument("--result-file")
    parser.add_argument("--minutes", type=int, default=30)
    parser.add_argument("--snooze-minutes", type=int, default=5)
    parser.add_argument("--no-snooze", action="store_true")
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--text", default="腰挺直一点。")
    parser.add_argument("--duration", type=float, default=8)
    parser.add_argument("--anchor-x", type=float)
    parser.add_argument("--anchor-y", type=float)
    parser.add_argument("--anchor-file")
    args = parser.parse_args()
    anchor = None
    if args.anchor_x is not None and args.anchor_y is not None:
        anchor = (args.anchor_x, args.anchor_y)
    if args.render_preview:
        render_preview()
        return 0
    if args.popup:
        result = show_qt_popup(
            minutes=args.minutes,
            snooze_minutes=args.snooze_minutes,
            snooze_enabled=not args.no_snooze,
            anchor=anchor,
            timeout_seconds=args.timeout,
            anchor_file=args.anchor_file,
        )
        if args.result_file:
            with open(args.result_file, "w", encoding="utf-8") as f:
                f.write(result)
        return 0 if result == "done" else 2
    if args.hint:
        show_qt_hint(args.text, args.duration, anchor, args.anchor_file)
        return 0
    return run_demo()


if __name__ == "__main__":
    raise SystemExit(main())

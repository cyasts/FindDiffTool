import os
import sys
from dataclasses import dataclass
from typing import List, Optional

from PySide6 import QtCore, QtGui, QtWidgets
from editor import EditorWindow
from version import version

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif'}


@dataclass
class ImagePair:
    name: str
    directory: str
    image_path_a: str
    image_path_b: str
    ext_a: str
    ext_b: str


class ImageCard(QtWidgets.QFrame):
    clicked = QtCore.Signal(object)

    def __init__(self, pair: ImagePair, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.pair = pair
        self.setStyleSheet(
            "QFrame { background: white;}"
        )
        self.setFixedSize(240, 190)
        self.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

        self.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Image preview
        image_label = QtWidgets.QLabel(self)
        image_label.setFixedSize(240, 150)
        image_label.setAlignment(QtCore.Qt.AlignCenter)
        # image_label.setStyleSheet("border-top-left-radius: 8px; border-top-right-radius: 8px;")
        image_label.setStyleSheet("background:white;")
        pixmap = QtGui.QPixmap(self.pair.image_path_a)
        if not pixmap.isNull():
            image_label.setPixmap(pixmap.scaled(image_label.size(), QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation))
            pixmap.scaled(
                image_label.size(),
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.FastTransformation
            )
        layout.addWidget(image_label)

        # Title
        title = QtWidgets.QLabel(self.pair.name, self)
        title.setAlignment(QtCore.Qt.AlignHCenter | QtCore.Qt.AlignVCenter)
        title.setFixedHeight(20)
        title.setStyleSheet(
            "padding: 0 12px; font-weight: 600; color: #333;"
        )
        layout.addWidget(title)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self.clicked.emit(self.pair)
        super().mouseReleaseEvent(event)


class FlowGrid(QtWidgets.QScrollArea):
    def __init__(self, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.cols = 4
        self.card_width = 240
        self.margin = 10
        self.container = QtWidgets.QWidget()
        self.grid = QtWidgets.QGridLayout(self.container)
        self.grid.setContentsMargins(self.margin, self.margin, self.margin, self.margin)
        self.grid.setHorizontalSpacing(16)
        self.grid.setVerticalSpacing(16)
        self.grid.setAlignment(QtCore.Qt.AlignTop)

        self.setWidget(self.container)

    def set_cards(self, cards: List[ImageCard]) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)

        if not cards:
            empty = QtWidgets.QLabel("暂无图片\n请点击\"加载图片\"按钮选择包含图片资源的文件夹（文件名需匹配 *_A 与 *_B）")
            empty.setAlignment(QtCore.Qt.AlignCenter)
            empty.setStyleSheet("color:#666; padding: 60px 20px;")
            self.grid.addWidget(empty, 0, 0)
            return

        cols = self.cols
        row = col = 0
        for card in cards:
            self.grid.addWidget(card, row, col)
            col += 1
            if col >= cols:
                col = 0
                row += 1
        self.apply_flow_metrics()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self.apply_flow_metrics()

    def apply_flow_metrics(self) -> None:
        # Distribute remaining width evenly as left/right margins and gaps (space-between)
        vw = self.viewport().width()
        min_gap = 12
        total_cards = self.cols * self.card_width
        remaining = max(0, vw - total_cards)
        gap = max(min_gap, remaining // (self.cols + 1))
        self.grid.setHorizontalSpacing(gap)
        self.grid.setContentsMargins(gap, self.margin, gap, 0)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"找猫游戏关卡编辑器 [v{version}]")
        self.resize(1100, 760)

        self.settings = QtCore.QSettings("FindCatsTool", "PySideApp")
        self.config_dir: str = self.settings.value("configDir", "", type=str)
        self.image_dir: str = self.settings.value("imageDir", "", type=str)

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        vbox = QtWidgets.QVBoxLayout(root)
        vbox.setContentsMargins(16, 16, 16, 16)
        vbox.setSpacing(12)

        # Header
        header = QtWidgets.QWidget()
        header_layout = QtWidgets.QHBoxLayout(header)
        header_layout.setContentsMargins(12, 12, 12, 12)
        header_layout.setSpacing(10)
        header.setStyleSheet("background:white; border-radius:8px;")

        self.header = header  # 保存引用，后面算宽度要用
        self.header_layout = header_layout

        self.title_label = QtWidgets.QLabel("找猫游戏关卡编辑器")
        self.title_label.setWordWrap(False)
        self.title_label.setSizePolicy(QtWidgets.QSizePolicy.Ignored, QtWidgets.QSizePolicy.Fixed)
        self.title_label.setMinimumWidth(220)
        self.title_label.setStyleSheet("font-size:20px; font-weight:700; color:#333;")

        header_layout.addWidget(self.title_label)
        header_layout.addStretch(1)

        self.btn_set_config = QtWidgets.QPushButton("设置输出目录")
        self.btn_load_images = QtWidgets.QPushButton("加载图片")

        btn_css = """
        QPushButton{
        background:#0d6efd; color:#fff; padding:6px 12px;
        border-radius:6px; border:1px solid #0d6efd;
        }
        QPushButton:hover{ background:#0b5ed7; border-color:#0b5ed7; }
        """
        self.btn_set_config.setStyleSheet(btn_css)
        self.btn_load_images.setStyleSheet(btn_css)

        header_layout.addWidget(self.btn_set_config)
        header_layout.addWidget(self.btn_load_images)

        vbox.addWidget(header, 0)

        # Config directory persistent display
        self.config_dir_label = QtWidgets.QLabel()
        self.config_dir_label.setStyleSheet("background:#f8f9fa; padding:6px 10px; border:1px solid #eee; border-radius:4px; font-size:12px; color:#333;")
        self.config_dir_label.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        vbox.addWidget(self.config_dir_label, 0)

        # Status bar like label
        self.status_label = QtWidgets.QLabel()
        self.status_label.setStyleSheet("background:#e9ecef; padding:8px 10px; border-radius:4px; font-size:13px;")
        vbox.addWidget(self.status_label, 0)

        # Image grid
        self.grid = FlowGrid()
        vbox.addWidget(self.grid, 1)

        # Connections
        self.btn_set_config.clicked.connect(self.on_set_config)
        self.btn_load_images.clicked.connect(self.on_load_images)

        # Init
        if self.config_dir:
            self.set_status(f"已加载保存的输出目录: {self.config_dir}")
        else:
            self.set_status("请先设置输出目录，然后加载图片资源")

        # 尝试自动加载上次的图片目录
        if self.image_dir and os.path.isdir(self.image_dir):
            QtCore.QTimer.singleShot(0, lambda: self.load_images(self.image_dir, from_startup=True))

        # initial config dir label
        self.refresh_config_dir_label()

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def on_set_config(self) -> None:
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "选择输出保存目录")
        if directory:
            self.config_dir = directory
            self.settings.setValue("configDir", self.config_dir)
            self.set_status(f"输出目录已设置: {self.config_dir}")
            self.refresh_config_dir_label()

    def _validate_image_path(self, pair: ImagePair) -> (bool, str):
        """检查 A/B 文件存在、可读、且 Qt 能加载。"""
        for label, path in (("A", pair.image_path_a), ("B", pair.image_path_b)):
            if not path:
                return False, f"未提供{label}图片路径。"
            if not os.path.exists(path):
                return False, f"{label}图片文件不存在：\n{path}"
            if not os.path.isfile(path):
                return False, f"{label}不是一个有效文件：\n{path}"
            if not os.access(path, os.R_OK):
                return False, f"{label}没有读取权限：\n{path}"
            pix = QtGui.QPixmap(path)
            if pix.isNull():
                reader = QtGui.QImageReader(path)
                fmt = reader.format().data().decode("ascii", "ignore") if reader.format() else "unknown"
                err = reader.errorString() if hasattr(reader, "errorString") else "unknown"
                return False, f"无法加载{label}图片（格式:{fmt}）：\n{path}\n错误：{err}"
        return True, ""

    def on_load_images(self) -> None:
        directory = QtWidgets.QFileDialog.getExistingDirectory(self, "选择图片资源文件夹")
        if not directory:
            return
        self.load_images(directory)

    def load_images(self, directory: str, from_startup: bool = False) -> None:
        try:
            files = sorted(os.listdir(directory))
        except Exception as exc:
            if from_startup:
                # 启动时自动加载失败，仅提示状态栏，不打扰用户
                self.set_status(f"加载图片失败: {exc}")
                return
            QtWidgets.QMessageBox.critical(self, "加载图片失败", str(exc))
            return

        image_files = [f for f in files if os.path.splitext(f)[1].lower() in IMAGE_EXTENSIONS]

        paired: dict = {}
        for file in image_files:
            stem, ext = os.path.splitext(file)
            if "_" not in stem:
                continue
            base, suffix = stem.rsplit("_", 1)
            if suffix.lower() not in ("a", "b") or not base:
                continue
            entry = paired.setdefault(base, {"A": None, "B": None, "ext_a": None, "ext_b": None})
            full_path = os.path.join(directory, file)
            if suffix.lower() == "a":
                entry["A"] = full_path
                entry["ext_a"] = ext
            else:
                entry["B"] = full_path
                entry["ext_b"] = ext

        pairs: List[ImagePair] = []
        for base, info in paired.items():
            if info["A"] and info["B"]:
                pairs.append(
                    ImagePair(
                        name=base,
                        directory=directory,
                        image_path_a=info["A"],
                        image_path_b=info["B"],
                        ext_a=info["ext_a"] or os.path.splitext(info["A"])[1],
                        ext_b=info["ext_b"] or os.path.splitext(info["B"])[1],
                    )
                )

        cards: List[ImageCard] = []
        for pair in pairs:
            card = ImageCard(pair)
            card.clicked.connect(self.open_editor)
            cards.append(card)

        self.grid.set_cards(cards)
        self.image_dir = directory
        self.settings.setValue("imageDir", self.image_dir)
        if from_startup:
            self.set_status(f"成功加载 {len(cards)} 组图片（来自上次使用的图片目录）")
        else:
            if not cards:
                self.set_status("未找到成对的图片，请确保文件名形如 name_A.jpg 和 name_B.jpg")
            else:
                self.set_status(f"成功加载 {len(cards)} 组图片")

    def refresh_config_dir_label(self) -> None:
        path = self.config_dir if self.config_dir else "未设置"
        text = f"输出目录: {path}"
        # elide middle if too long
        metrics = self.config_dir_label.fontMetrics()
        elided = metrics.elidedText(text, QtCore.Qt.ElideMiddle, self.config_dir_label.width() - 20)
        self.config_dir_label.setText(elided)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        # keep elided label updated
        self.refresh_config_dir_label()

    def open_editor(self, pair: ImagePair) -> None:
        if not self.config_dir:
            QtWidgets.QMessageBox.information(self, "提示", "请先设置输出目录")
            return

        ok, reason = self._validate_image_path(pair)
        if not ok:
            # 弹窗 + 状态栏提示，方便用户知道问题与路径
            QtWidgets.QMessageBox.warning(self, "无法打开图片", reason)
            self.set_status(f"打开失败：{reason.replace(os.linesep, ' ')}")
            return

        win = EditorWindow(pair=pair, config_dir=self.config_dir, parent=None)
        # keep a reference to avoid immediate GC when parented
        if not hasattr(self, "_open_editors"):
            self._open_editors = []
        self._open_editors.append(win)
        win.destroyed.connect(lambda *_: self._open_editors.remove(win) if win in self._open_editors else None)
        win.show()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        dirty_editors = [w for w in getattr(self, "_open_editors", []) if getattr(w, "_is_dirty", False)]
        if dirty_editors:
            ret = QtWidgets.QMessageBox.question(
                self,
                "确认退出",
                f"有 {len(dirty_editors)} 个编辑器尚未保存，是否要全部关闭？",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Cancel
            )
            if ret != QtWidgets.QMessageBox.Yes:
                event.ignore()
                return

        for win in list(getattr(self, "_open_editors", [])):
            try:
                win.close()
            except Exception:
                pass
        event.accept()

def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setOrganizationName("FindDifferenceEditor")
    app.setApplicationName("PySideApp")
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

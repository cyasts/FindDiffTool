from PySide6 import QtCore, QtGui, QtWidgets


class ImageScene(QtWidgets.QGraphicsScene):
    def __init__(self, pixmap: QtGui.QPixmap):
        super().__init__(0, 0, pixmap.width(), pixmap.height())
        self.bg = QtWidgets.QGraphicsPixmapItem(pixmap)
        self.addItem(self.bg)


class ImageView(QtWidgets.QGraphicsView):
    def __init__(self, scene: ImageScene):
        super().__init__(scene)
        self.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.SmoothPixmapTransform)
        # 默认不使用手型拖拽，保持箭头光标
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.viewport().setCursor(QtCore.Qt.ArrowCursor)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.SmartViewportUpdate)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self.fitInView(self.sceneRect(), QtCore.Qt.KeepAspectRatio)

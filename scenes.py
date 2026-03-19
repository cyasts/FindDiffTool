from PySide6 import QtCore, QtGui, QtWidgets


class ImageScene(QtWidgets.QGraphicsScene):
    def __init__(self, pixmap: QtGui.QPixmap):
        super().__init__(0, 0, pixmap.width(), pixmap.height())
        self.bg = QtWidgets.QGraphicsPixmapItem(pixmap)
        self.addItem(self.bg)


class ImageView(QtWidgets.QGraphicsView):
    """支持滚轮缩放（以指针为中心）和右键拖动平移的 QGraphicsView。"""

    ZOOM_MIN = 0.02
    ZOOM_MAX = 50.0
    ZOOM_BASE = 1.15  # 每 120 单位（一格滚轮）的缩放倍率

    def __init__(self, scene: ImageScene):
        super().__init__(scene)
        self.setRenderHints(QtGui.QPainter.Antialiasing | QtGui.QPainter.SmoothPixmapTransform)
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.viewport().setCursor(QtCore.Qt.ArrowCursor)
        self.setViewportUpdateMode(QtWidgets.QGraphicsView.SmartViewportUpdate)
        # 让 view 可以接收鼠标滚轮事件来缩放，而不是滚动
        self.setTransformationAnchor(QtWidgets.QGraphicsView.NoAnchor)
        self.setResizeAnchor(QtWidgets.QGraphicsView.NoAnchor)

        self._panning = False
        self._pan_start = QtCore.QPoint()
        self._current_zoom = 1.0
        self._fit_done = False  # 是否已经做过初始 fitInView

    # ---- 初始化 & 重置 ----

    def resetView(self) -> None:
        """复位到适配窗口的默认视图。"""
        self.resetTransform()
        self.fitInView(self.sceneRect(), QtCore.Qt.KeepAspectRatio)
        self._current_zoom = self.transform().m11()

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        # 只在首次显示时自动 fitInView，之后不再重置用户的缩放
        if not self._fit_done:
            self.fitInView(self.sceneRect(), QtCore.Qt.KeepAspectRatio)
            self._current_zoom = self.transform().m11()
            self._fit_done = True

    def _get_fit_scale(self) -> float:
        """获取如果执行 fitInView(KeepAspectRatio) 时对应的真实缩放比例。"""
        v_rect = self.viewport().rect()
        s_rect = self.sceneRect()
        if s_rect.width() <= 0 or s_rect.height() <= 0:
            return 1.0
        # 实际可用大小（可选减去滚动条，但 viewport() 已经是内部有效区域）
        scale_w = v_rect.width() / s_rect.width()
        scale_h = v_rect.height() / s_rect.height()
        return min(scale_w, scale_h)

    # ---- 滚轮缩放（以鼠标指针为中心） ----

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        # 计算缩放因子
        angle = event.angleDelta().y()
        if angle == 0:
            event.ignore()
            return

        # 适配 Mac 触控板的连续平滑滚动，并防止鼠标滚轮一次滚动导致幅度过大
        import math
        factor = math.pow(self.ZOOM_BASE, angle / 120.0)

        # 限制缩放范围（最小不得小于填满当前窗口的自适应大小，即不变成漂浮小图）
        min_zoom = self._get_fit_scale()
        new_zoom = self._current_zoom * factor
        
        if new_zoom < min_zoom:
            factor = min_zoom / self._current_zoom
        elif new_zoom > self.ZOOM_MAX:
            factor = self.ZOOM_MAX / self._current_zoom

        if abs(factor - 1.0) < 1e-6:
            event.accept()
            return

        # 以鼠标指针位置为中心进行缩放
        old_pos = self.mapToScene(event.position().toPoint())
        self.scale(factor, factor)
        new_pos = self.mapToScene(event.position().toPoint())

        # 补偿位移，使鼠标下方的场景点保持不动
        delta = new_pos - old_pos
        self.translate(delta.x(), delta.y())

        self._current_zoom *= factor
        event.accept()

    # ---- 右键拖动平移 ----

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.RightButton:
            self._panning = True
            self._pan_start = event.position().toPoint()
            self.viewport().setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._panning:
            delta = event.position().toPoint() - self._pan_start
            self._pan_start = event.position().toPoint()
            # 通过滚动条实现平移
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.RightButton and self._panning:
            self._panning = False
            self.viewport().setCursor(QtCore.Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ---- 双击复位 ----

    def mouseDoubleClickEvent(self, event: QtGui.QMouseEvent) -> None:
        if event.button() == QtCore.Qt.LeftButton:
            self.resetView()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

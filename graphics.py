# -*- coding: utf-8 -*-
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets
from typing import Optional, Dict, Tuple

from models import Difference, RADIUS_LEVELS, MIN_RECT_SIZE


# ==============================================================
# 1) 可广播的模型层：DifferenceModel（绑定一份 Difference 数据）
# ==============================================================

class DifferenceModel(QtCore.QObject):
    """把 dataclass Difference 包一层，用 Qt 信号广播变更。"""
    geometryChanged = QtCore.Signal(object)  # source
    circleChanged   = QtCore.Signal(object)  # source
    anyChanged      = QtCore.Signal(object)  # source

    def __init__(self, d: Difference):
        super().__init__()
        self.data = d
        self._updating = False  # 批量/重入保护

    # ------- 读取便捷属性（只读映射到 dataclass） -------
    @property
    def id(self):          return self.data.id
    @property
    def x(self):           return float(self.data.x)
    @property
    def y(self):           return float(self.data.y)
    @property
    def width(self):       return float(self.data.width)
    @property
    def height(self):      return float(self.data.height)
    @property
    def cx(self):          return float(self.data.cx)
    @property
    def cy(self):          return float(self.data.cy)
    @property
    def hint_level(self):  return int(self.data.hint_level)
    @property
    def section(self):     return self.data.section
    @property
    def label(self):       return self.data.label
    @property
    def category(self):    return self.data.category

    # ------- 修改 API：写回 dataclass 并广播 -------
    def set_rect(self, x: float, y: float, w: float, h: float, *, source=None):
        if self._updating: return
        d = self.data
        changed = (x != d.x) or (y != d.y) or (w != d.width) or (h != d.height)
        if not changed: return
        d.x, d.y, d.width, d.height = float(x), float(y), float(w), float(h)
        self.geometryChanged.emit(source)
        self.anyChanged.emit(source)

    def set_circle(self, cx: float, cy: float, *, source=None):
        if self._updating: return
        d = self.data
        changed = (cx != d.cx) or (cy != d.cy)
        if not changed: return
        d.cx, d.cy = float(cx), float(cy)
        self.circleChanged.emit(source)
        self.anyChanged.emit(source)

    def set_hint_level(self, level: int, *, source=None):
        if self._updating: return
        lvl = int(level)
        if lvl == self.data.hint_level: return
        self.data.hint_level = lvl
        self.anyChanged.emit(source)

    # 可选：批量更新（避免中间反复发信号）
    def begin(self): self._updating = True
    def end(self, *, source=None):
        self._updating = False
        self.anyChanged.emit(source)


# ==============================================================
# 2) 一个总线：同一个 Difference（按 id）对应同一个 DifferenceModel
# ==============================================================

class _DiffBus:
    def __init__(self):
        self._by_id: Dict[str, DifferenceModel] = {}

    def get_model(self, d: Difference) -> DifferenceModel:
        m = self._by_id.get(d.id)
        if m is None:
            m = DifferenceModel(d)
            self._by_id[d.id] = m
        return m

DIFF_BUS = _DiffBus()

def get_model_for_difference(d: Difference) -> DifferenceModel:
    return DIFF_BUS.get_model(d)


# ==============================================================
# 3) 视图层：DifferenceItem（保持你的构造签名不变）
# ==============================================================

class DifferenceItem(QtWidgets.QGraphicsObject):
    """带信号的图元：对外发射半径变化信号，并支持同步/防抖/文字自适配。"""
    # 对外唯一信号：半径改变（携带 diff_id 与新半径）
    radiusChanged = QtCore.Signal(str, float)

    # ------- 共享画笔/画刷 -------
    # ---- 固定配色（统一风格）----
    # 矩形：红边 + 红色半透明填充
    PEN_RECT      = QtGui.QPen(QtGui.QColor('#d32f2f'), 2)           # 红
    BRUSH_RECT    = QtGui.QBrush(QtGui.QColor(211, 47, 47, 40))      # 红(40/255)
    # 矩形高亮：更鲜明的红边 + 更亮的红填充
    PEN_RECT_HL   = QtGui.QPen(QtGui.QColor('#ff1744'), 3)           # 亮红
    BRUSH_RECT_HL = QtGui.QBrush(QtGui.QColor(255, 23, 68, 48))      # 亮红(48/255)

    # 圆：绿色边
    PEN_CIRCLE    = QtGui.QPen(QtGui.QColor('#00c853'), 3)           # 绿
    # 圆高亮：更亮的绿边 + 轻微绿色内辉
    PEN_CIRCLE_HL = QtGui.QPen(QtGui.QColor('#00e676'), 4)           # 亮绿
    BRUSH_CIRC_HL = QtGui.QBrush(QtGui.QColor(0, 230, 118, 30))      # 亮绿(30/255)
    # 角手柄
    HANDLE_BR     = QtGui.QBrush(QtGui.QColor('#d32f2f'))            # 红点
    HANDLE_PEN    = QtGui.QPen(QtCore.Qt.NoPen)

    # ------- 几何阈值 -------
    HANDLE_SIZE    = 9.0
    EDGE_THRESH    = 8.0
    CORNER_THRESH  = 12.0

    class Mode:
        NONE=0; MOVE=1; RESIZE_CORNER=2; RESIZE_EDGE=3; DRAG_CIRCLE=4

    def __init__(self, diff: Difference,
                 color: Optional[QtGui.QColor] = None,
                 on_change=None,
                 is_up: bool = True):
        super().__init__()
        # 绑定共享 model
        self.model = get_model_for_difference(diff)
        self.is_up = is_up
        self._on_change = on_change

        self._extern_selected: bool = False
        self._selected_alpha: int = 200

        # 文字颜色默认 #333；若传入 color，则矩形描边与文字都用它
        self._text_color = QtGui.QColor('#333')
        if color is not None:
            self._text_color = QtGui.QColor(color)

        # 可见性（内部控制，默认全开）
        self._show_rect   = True
        self._show_circle = True
        self._show_label  = True

        # 本地几何（从 model 初始化）
        self._rect = QtCore.QRectF(0, 0,
                                   max(MIN_RECT_SIZE, float(self.model.width)),
                                   max(MIN_RECT_SIZE, float(self.model.height)))
        self.setPos(float(self.model.x), float(self.model.y))

        # 圆心/半径（本地坐标）
        self._radius = self._auto_radius(self._rect)
        cx = self.model.cx if self.model.cx >= 0 else self._rect.width()/2
        cy = self.model.cy if self.model.cy >= 0 else self._rect.height()/2
        self._circle_center = QtCore.QPointF(
            max(self._radius, min(cx, self._rect.width()  - self._radius)),
            max(self._radius, min(cy, self._rect.height() - self._radius))
        )
        self._hint_level = int(self.model.hint_level)

        # 文本缓存（自动换行/居中/字号自适配）
        self._label = (self.model.label or "").strip()
        self._text_font = QtGui.QFont()
        self._text_cache_key: Optional[Tuple[int, int, str]] = None
        self._text_cached_pt: float = 10.0

        # 交互状态
        self._mode = self.Mode.NONE
        self._drag_corner = -1
        self._edge_code = ''  # 'L','R','T','B'
        self._press_pos_scene = QtCore.QPointF()
        self._press_item_pos  = QtCore.QPointF()
        self._press_rect      = QtCore.QRectF()
        self._press_center    = QtCore.QPointF()

        # 按下时矩形四角（场景坐标）与对角锚点
        self._press_tl_scene = QtCore.QPointF()
        self._press_br_scene = QtCore.QPointF()
        self._anchor_scene   = QtCore.QPointF()

        # hover 高亮
        self._hl_rect   = False
        self._hl_circle = False

        # 抖动防治状态
        self._is_resizing   = False  # 正在拉伸（角/边）
        self._suppress_sync = False  # 内部 setPos 期间屏蔽 itemChange 的同步

        # 性能
        self.setCacheMode(QtWidgets.QGraphicsItem.DeviceCoordinateCache)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setAcceptedMouseButtons(QtCore.Qt.LeftButton)
        self.setAcceptHoverEvents(True)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.setZValue(1)

        # 通知/防回环
        self._pending_notify = False
        self._updating_from_model = False

        # 半径去抖缓存 + 构造后先发一次
        self._last_emitted_radius: Optional[float] = None
        QtCore.QTimer.singleShot(0.5, lambda: self._emit_radius_if_changed(self._radius))

        # 订阅 model（另一侧变化时我同步）
        self.model.geometryChanged.connect(self._on_model_geometry_changed)
        self.model.circleChanged.connect(self._on_model_circle_changed)
        self.model.anyChanged.connect(self._on_model_any_changed)

    # -------------------- 绘制 --------------------
    def paint(self, p: QtGui.QPainter, option, widget=None):
        p.setRenderHints(QtGui.QPainter.RenderHint(0))

         # 矩形（含高亮 + 外部选中态的不透明度增强）
        if self._show_rect:
            if self._hl_rect:
                pen = self.PEN_RECT_HL
                base_brush = self.BRUSH_RECT_HL
            else:
                pen = self.PEN_RECT
                base_brush = self.BRUSH_RECT

            p.setPen(pen)

            # 不要直接修改类级别画刷；复制颜色后按需调 alpha
            col = QtGui.QColor(base_brush.color())
            if self._extern_selected:
                # 选中时提高不透明度（取最大不超过 255）
                col.setAlpha(min(255, self._selected_alpha))
            # 未选中使用原始 alpha（类里是 40/48）
            p.setBrush(QtGui.QBrush(col))
            p.drawRect(self._rect)

        # 本侧显示（沿用 up/down 逻辑）
        visible_for_side = (self.model.section == 'up') == self.is_up

        # 圆（含高亮）
        if self._show_circle:
            if self._hl_circle:
                p.setPen(self.PEN_CIRCLE_HL); p.setBrush(self.BRUSH_CIRC_HL)
            else:
                p.setPen(self.PEN_CIRCLE);    p.setBrush(QtCore.Qt.NoBrush)
            r = self._radius; c = self._circle_center
            p.drawEllipse(QtCore.QRectF(c.x()-r, c.y()-r, 2*r, 2*r))

        # 文本：居中 + 自动换行 + 字号自适配
        if visible_for_side and self._show_label and self._label:
            box_side = min(self._rect.width(), self._rect.height()) * 0.9
            c = self._circle_center
            text_rect = QtCore.QRectF(c.x() - box_side/2.0,
                                      c.y() - box_side/2.0,
                                      box_side, box_side)

            pt = self._compute_fitting_pointsize(text_rect.width(), text_rect.height(), self._label)
            self._text_font.setPointSizeF(pt)
            p.setFont(self._text_font)
            p.setPen(QtGui.QPen(self._text_color))
            flags = QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap
            p.drawText(text_rect, flags, self._label)

        # 角把手（跟随矩形显示）
        if self._show_rect:
            hs = self.HANDLE_SIZE
            p.setPen(self.HANDLE_PEN); p.setBrush(self.HANDLE_BR)
            tl = self._rect.topLeft(); tr = self._rect.topRight()
            br = self._rect.bottomRight(); bl = self._rect.bottomLeft()
            for ptc in (tl, tr, br, bl):
                p.drawEllipse(QtCore.QRectF(ptc.x()-hs/2, ptc.y()-hs/2, hs, hs))

    def boundingRect(self) -> QtCore.QRectF:
        return self._rect.adjusted(-4, -4, 4, 4)

    def shape(self) -> QtGui.QPainterPath:
        path = QtGui.QPainterPath()
        path.addRect(self._rect)
        return path

    # -------------------- hover：高亮 + 指针 --------------------
    def hoverMoveEvent(self, e: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        if self._is_resizing:
            return  # 拉伸中不改变光标/高亮

        pos = e.pos()

        # 角优先
        corner = self._hit_corner(pos)
        if self._show_rect and corner >= 0:
            self.setCursor(QtCore.Qt.SizeFDiagCursor if corner in (0, 2) else QtCore.Qt.SizeBDiagCursor)
            self._set_hover_state(rect_hl=True, circ_hl=False)
            return

        # 边
        edge = self._hit_edge(pos)
        if self._show_rect and edge:
            self.setCursor(QtCore.Qt.SizeHorCursor if edge in ('L','R') else QtCore.Qt.SizeVerCursor)
            self._set_hover_state(rect_hl=True, circ_hl=False)
            return

        # 圆
        if self._hit_circle(pos):
            self.setCursor(QtCore.Qt.OpenHandCursor)
            self._set_hover_state(rect_hl=False, circ_hl=True)
            return

        # 矩形内部也高亮
        if self._show_rect and self._rect.contains(pos):
            self.setCursor(QtCore.Qt.OpenHandCursor)
            self._set_hover_state(rect_hl=True, circ_hl=False)
            return

        # 其它
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self._set_hover_state(rect_hl=False, circ_hl=False)
        super().hoverMoveEvent(e)

    def hoverLeaveEvent(self, e: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        self.unsetCursor()
        self._set_hover_state(rect_hl=False, circ_hl=False)
        super().hoverLeaveEvent(e)

    def _set_hover_state(self, rect_hl: bool, circ_hl: bool):
        changed = False
        if self._hl_rect != rect_hl:
            self._hl_rect = rect_hl; changed = True
        if self._hl_circle != circ_hl:
            self._hl_circle = circ_hl; changed = True
        if changed:
            self.update()

    # -------------------- 鼠标交互 --------------------
    def mousePressEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        self._mode = self.Mode.NONE
        self._drag_corner = -1
        self._edge_code = ''
        pos = e.pos()

        # 统一记录按下时快照
        self._press_item_pos  = self.pos()
        self._press_rect      = QtCore.QRectF(self._rect)
        self._press_center    = QtCore.QPointF(self._circle_center)
        self._press_tl_scene = self.mapToScene(self._rect.topLeft())
        self._press_br_scene = self.mapToScene(self._rect.bottomRight())

        # 圆命中优先
        if self._hit_circle(pos):
            self._mode = self.Mode.DRAG_CIRCLE
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            e.accept(); return

        # 角
        corner = self._hit_corner(pos)
        if self._show_rect and corner >= 0:
            self._mode = self.Mode.RESIZE_CORNER
            self._drag_corner = corner
            tl, br = self._press_tl_scene, self._press_br_scene
            opp = [QtCore.QPointF(br.x(), br.y()),   # 拖 TL → 锚 BR
                   QtCore.QPointF(tl.x(), br.y()),   # 拖 TR → 锚 BL
                   QtCore.QPointF(tl.x(), tl.y()),   # 拖 BR → 锚 TL
                   QtCore.QPointF(br.x(), tl.y())]   # 拖 BL → 锚 TR
            self._anchor_scene = opp[corner]
            self._is_resizing = True
            self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, False)
            e.accept(); return

        # 边
        edge = self._hit_edge(pos)
        if self._show_rect and edge:
            self._mode = self.Mode.RESIZE_EDGE
            self._edge_code = edge
            self._is_resizing = True
            self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, False)
            e.accept(); return

        # 默认移动（交给内置拖动）
        self._mode = self.Mode.MOVE
        self.setCursor(QtCore.Qt.ClosedHandCursor)
        super().mousePressEvent(e)
        e.accept()

    def mouseMoveEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        if self._mode == self.Mode.MOVE:
            super().mouseMoveEvent(e)
            e.accept(); return

        scene_rect = self.scene().sceneRect() if self.scene() \
            else QtCore.QRectF(-1e6, -1e6, 2e6, 2e6)

        if self._mode == self.Mode.RESIZE_CORNER:
            cur = e.scenePos()
            # 新的场景 TL/BR：和对角锚点组成对角点
            tl_scene = QtCore.QPointF(min(cur.x(), self._anchor_scene.x()),
                                      min(cur.y(), self._anchor_scene.y()))
            br_scene = QtCore.QPointF(max(cur.x(), self._anchor_scene.x()),
                                      max(cur.y(), self._anchor_scene.y()))
            # 夹紧
            tl_scene, br_scene = self._clamp_scene_rect(tl_scene, br_scene, scene_rect)
            # 应用
            self._apply_scene_rect(tl_scene, br_scene)
            self._after_resize_update()
            e.accept(); return

        if self._mode == self.Mode.RESIZE_EDGE:
            cur = e.scenePos()
            tl0, br0 = self._press_tl_scene, self._press_br_scene
            tl_scene = QtCore.QPointF(tl0)
            br_scene = QtCore.QPointF(br0)
            if self._edge_code == 'L':
                x = min(cur.x(), br0.x() - MIN_RECT_SIZE)
                tl_scene.setX(x)
            elif self._edge_code == 'R':
                x = max(cur.x(), tl0.x() + MIN_RECT_SIZE)
                br_scene.setX(x)
            elif self._edge_code == 'T':
                y = min(cur.y(), br0.y() - MIN_RECT_SIZE)
                tl_scene.setY(y)
            elif self._edge_code == 'B':
                y = max(cur.y(), tl0.y() + MIN_RECT_SIZE)
                br_scene.setY(y)

            tl_scene, br_scene = self._clamp_scene_rect(tl_scene, br_scene, scene_rect)
            self._apply_scene_rect(tl_scene, br_scene)
            self._after_resize_update()
            e.accept(); return

        if self._mode == self.Mode.DRAG_CIRCLE:
            p_local = self.mapFromScene(e.scenePos())
            self._circle_center = QtCore.QPointF(
                max(self._radius, min(p_local.x(), self._rect.width()  - self._radius)),
                max(self._radius, min(p_local.y(), self._rect.height() - self._radius))
            )
            self._sync_model(); self.update(); self._throttled_notify()
            e.accept(); return

        e.ignore()

    def mouseReleaseEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        self._mode = self.Mode.NONE
        self._is_resizing = False
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)  # 恢复内置移动
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self._sync_model()
        self._emit_change()
        super().mouseReleaseEvent(e)

    def updateLabel(self):
        self._label = self.model.label or ""
        self._text_cache_key = None
        self.update()

    def setVis(self, show_rect: bool, show_circle: bool, show_label: bool):
        changed = False
        if self._show_rect   != show_rect:   self._show_rect   = show_rect;   changed = True
        if self._show_circle != show_circle: self._show_circle = show_circle; changed = True
        if self._show_label  != show_label:  self._show_label  = show_label;  changed = True
        if changed:
            self.update()

    # -------------------- 模型 <-> 视图 同步 --------------------
    def _sync_model(self):
        if self._updating_from_model:
            return
        self.model.set_rect(self.pos().x(), self.pos().y(),
                            self._rect.width(), self._rect.height(),
                            source=self)
        self.model.set_circle(self._circle_center.x(), self._circle_center.y(),
                              source=self)
        self.model.set_hint_level(self._hint_level, source=self)

    @QtCore.Slot(object)
    def _on_model_geometry_changed(self, source):
        if source is self:
            return
        self._updating_from_model = True
        try:
            self.setPos(self.model.x, self.model.y)
            self.prepareGeometryChange()
            self._rect = QtCore.QRectF(0, 0,
                                       max(MIN_RECT_SIZE, float(self.model.width)),
                                       max(MIN_RECT_SIZE, float(self.model.height)))
            new_r = self._auto_radius(self._rect)  # 会更新 _hint_level
            self._emit_radius_if_changed(new_r)
            self._radius = new_r
            self._text_cache_key = None
            self.update()
        finally:
            self._updating_from_model = False

    @QtCore.Slot(object)
    def _on_model_circle_changed(self, source):
        if source is self:
            return
        self._updating_from_model = True
        try:
            self._circle_center = QtCore.QPointF(
                max(self._radius, min(self.model.cx, self._rect.width()  - self._radius)),
                max(self._radius, min(self.model.cy, self._rect.height() - self._radius))
            )
            self.update()
        finally:
            self._updating_from_model = False

    @QtCore.Slot(object)
    def _on_model_any_changed(self, source):
        if source is self:
            return
        self._label = (self.model.label or "").strip()
        self._hint_level = int(self.model.hint_level)
        self._text_cache_key = None
        self.update()

    # --- 供外部调用：设置/取消选中 ---
    def setExternalSelected(self, selected: bool, *, raise_z: bool = True) -> None:
        """
        外部设置该图元为选中/非选中。
        选中：提高矩形填充不透明度；可选把 Z 值抬高以便覆盖。
        取消选中：恢复原始不透明度与 Z 值（若你有自定义 Z，可按需调整）。
        """
        if self._extern_selected == bool(selected):
            return
        self._extern_selected = bool(selected)
        if raise_z:
            # 选中时略微抬高层级，取消选中恢复
            self.setZValue(2 if self._extern_selected else 1)
        self.update()


    # -------------------- 其它辅助 --------------------
    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.ItemPositionChange and self.scene():
            # 拉伸过程中我们自己用场景 TL/BR 算完再 setPos，别再夹一次，避免抖动
            if self._is_resizing:
                return value
            new_pos: QtCore.QPointF = value
            scene_rect = self.scene().sceneRect()
            new_x = max(scene_rect.left(),  min(new_pos.x(), scene_rect.right()  - self._rect.width()))
            new_y = max(scene_rect.top(),   min(new_pos.y(), scene_rect.bottom() - self._rect.height()))
            return QtCore.QPointF(new_x, new_y)

        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged:
            if not self._is_resizing and not getattr(self, '_suppress_sync', False):
                self._sync_model()
                self._throttled_notify()
        return super().itemChange(change, value)

    def _auto_radius(self, r: QtCore.QRectF) -> float:
        """从 RADIUS_LEVELS（list）选 <= 内切半径的最大值；太小时退化为 half。"""
        size = min(r.width(), r.height())
        half = size * 0.5
        allowed = [v for v in RADIUS_LEVELS if v <= half - 10]
        if allowed:
            radius = float(allowed[-1])
            self._hint_level = len(allowed)          # 1-based
        else:
            radius = float(RADIUS_LEVELS[0])
            self._hint_level = 1
        return radius

    # ====== 文本字号自适配 ======
    def _compute_fitting_pointsize(self, box_w: float, box_h: float, text: str) -> float:
        """在给定盒子内，用二分法找最大可用字号（支持换行，数字也可断行）。"""
        if box_w <= 1 or box_h <= 1 or not text:
            return 10.0

        key = (int(box_w), int(box_h), text)
        if self._text_cache_key == key:
            return float(self._text_cached_pt)

        lo, hi = 8.0, max(14.0, box_h * 0.9)
        best = lo

        test_font = QtGui.QFont(self._text_font)
        test_rect = QtCore.QRect(0, 0, int(box_w), 10_000)

        # 关键：允许“任意位置换行”，数字也能断行
        flags = (QtCore.Qt.AlignCenter
                | QtCore.Qt.TextWordWrap
                | QtCore.Qt.TextWrapAnywhere)  # ← 新增

        while hi - lo > 0.5:
            mid = (lo + hi) / 2.0
            test_font.setPointSizeF(mid)
            fm = QtGui.QFontMetrics(test_font)

            br = fm.boundingRect(test_rect, flags, text)

            # 关键：同时卡“高”和“宽”
            if br.height() <= box_h and br.width() <= box_w:
                best = mid
                lo = mid
            else:
                hi = mid

        self._text_cache_key = key
        self._text_cached_pt = float(best)
        return float(best)

    def _throttled_notify(self):
        if self._on_change and not self._pending_notify:
            self._pending_notify = True
            QtCore.QTimer.singleShot(0, self._emit_change)

    def _emit_change(self):
        self._pending_notify = False
        if callable(self._on_change):
            try:
                self._on_change(self.model.id)
            except Exception:
                pass

    def _after_resize_update(self):
        # 半径根据新尺寸挑选；圆心按照 press 时的位置“重夹”，避免跳变
        new_r = self._auto_radius(self._rect)
        c0 = self._press_center
        self._circle_center = QtCore.QPointF(
            max(new_r, min(c0.x(), self._rect.width()  - new_r)),
            max(new_r, min(c0.y(), self._rect.height() - new_r))
        )
        self._emit_radius_if_changed(new_r)  # 自身拉伸时发
        self._radius = new_r
        self._text_cache_key = None
        self._sync_model(); self.update(); self._throttled_notify()

    # --- 半径信号去抖 ---
    def _emit_radius_if_changed(self, r: float, eps: float = 1e-6):
        if getattr(self, "_last_emitted_radius", None) is None or abs(r - self._last_emitted_radius) > eps:
            self._last_emitted_radius = float(r)
            self.radiusChanged.emit(self.model.id, float(r))

    # -------------------- 命中工具 --------------------
    def _hit_corner(self, pos: QtCore.QPointF) -> int:
        r = self._rect
        corners = [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]
        for i, c in enumerate(corners):
            if QtCore.QLineF(pos, c).length() <= self.CORNER_THRESH:
                return i
        return -1

    def _hit_edge(self, pos: QtCore.QPointF) -> str:
        r = self._rect
        et = self.EDGE_THRESH
        if 0 <= pos.y() <= r.height():
            if abs(pos.x()-0.0)       <= et: return 'L'
            if abs(pos.x()-r.width()) <= et: return 'R'
        if 0 <= pos.x() <= r.width():
            if abs(pos.y()-0.0)       <= et: return 'T'
            if abs(pos.y()-r.height())<= et: return 'B'
        return ''

    def _hit_circle(self, pos: QtCore.QPointF) -> bool:
        if not self._show_circle:
            return False
        return QtCore.QLineF(pos, self._circle_center).length() <= self._radius

    # -------------------- 场景几何工具 --------------------
    def _clamp_scene_rect(self, tl: QtCore.QPointF, br: QtCore.QPointF,
                          scene_rect: QtCore.QRectF) -> Tuple[QtCore.QPointF, QtCore.QPointF]:
        # 归一化
        tl = QtCore.QPointF(min(tl.x(), br.x()), min(tl.y(), br.y()))
        br = QtCore.QPointF(max(tl.x(), br.x()), max(tl.y(), br.y()))
        # 夹到场景边界
        tl.setX(max(scene_rect.left(),  tl.x()))
        tl.setY(max(scene_rect.top(),   tl.y()))
        br.setX(min(scene_rect.right(), br.x()))
        br.setY(min(scene_rect.bottom(),br.y()))
        # 最小尺寸
        w = max(MIN_RECT_SIZE, br.x() - tl.x())
        h = max(MIN_RECT_SIZE, br.y() - tl.y())
        # 若尺寸因边界受限不足，优先推开 br（不越界）
        br = QtCore.QPointF(min(scene_rect.right(),  tl.x() + w),
                            min(scene_rect.bottom(), tl.y() + h))
        return tl, br

    def _apply_scene_rect(self, tl_scene: QtCore.QPointF, br_scene: QtCore.QPointF):
        """把场景 TL/BR 应用到 item：setPos(TL) + _rect=(0,0,w,h)。"""
        w = max(MIN_RECT_SIZE, br_scene.x() - tl_scene.x())
        h = max(MIN_RECT_SIZE, br_scene.y() - tl_scene.y())
        self._suppress_sync = True
        try:
            self.setPos(tl_scene)                  # item 的 scenePos = TL
            self.prepareGeometryChange()
            self._rect = QtCore.QRectF(0, 0, w, h) # 本地始终从原点开始
        finally:
            self._suppress_sync = False

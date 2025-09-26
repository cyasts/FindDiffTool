# -*- coding: utf-8 -*-
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets
from typing import Optional, Dict, Tuple

# 由你的工程提供
from models import Difference, RADIUS_LEVELS, MIN_RECT_SIZE


# ==============================================================
# 1) Model：作为唯一真源（SSOT），负责自动维护 hint_level
# ==============================================================

class DifferenceModel(QtCore.QObject):
    """把 dataclass Difference 包一层，用 Qt 信号广播变更；并在 set_rect 时自动维护 hint_level。"""
    geometryChanged = QtCore.Signal(object)  # source
    circleChanged   = QtCore.Signal(object)  # source
    anyChanged      = QtCore.Signal(object)  # source

    def __init__(self, d: Difference):
        super().__init__()
        self.data = d
        self._updating = False  # 批量/重入保护

        self.set_rect(d.x, d.y, d.width, d.height, source=self, force=True)

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

    # ------- 自动 hint 计算（半径以内切圆上限） -------
    def _auto_hint_level(self, w: float, h: float) -> int:
        size = max(0.0, min(float(w), float(h)) * 0.5)
        chosen = 1
        for i, r in enumerate(RADIUS_LEVELS, start=1):
            if r <= size: chosen = i
            else: break
        return chosen

    # ------- 修改 API：写回 dataclass 并广播 -------
    def set_rect(self, x: float, y: float, w: float, h: float, *, source=None, force: bool=False):
        if self._updating and not force:
            return
        d = self.data
        x, y, w, h = float(x), float(y), float(w), float(h)
        changed = (x != d.x) or (y != d.y) or (w != d.width) or (h != d.height)

        # 允许在几何“未变化”时也强制执行后续逻辑（用于初次载入自动适应）
        if not changed and not force:
            return

        d.x, d.y, d.width, d.height = x, y, w, h

        # ★ 无论 changed 与否，只要 force=True 或 changed，就重算 hint
        lvl = self._auto_hint_level(w, h)
        if lvl != d.hint_level:
            d.hint_level = lvl

        self.geometryChanged.emit(source)
        self.anyChanged.emit(source)

    def set_circle(self, cx: float, cy: float, *, source=None):
        if self._updating: return
        d = self.data
        cx, cy = float(cx), float(cy)
        changed = (cx != d.cx) or (cy != d.cy)
        if not changed: return
        d.cx, d.cy = cx, cy
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
# 3) 视图层：DifferenceItem（轻薄视图，仅缓存上一帧尺寸用于 prepareGeometryChange）
# ==============================================================

class DifferenceItem(QtWidgets.QGraphicsObject):
    """轻视图：不再持有业务状态（rect/circle/hint），全部读 model；仅缓存上一帧尺寸用于几何契约。"""
    radiusChanged = QtCore.Signal(str, float)

    # ---- 固定配色 ----
    PEN_RECT      = QtGui.QPen(QtGui.QColor('#d32f2f'), 2)
    BRUSH_RECT    = QtGui.QBrush(QtGui.QColor(211, 47, 47, 40))
    PEN_RECT_HL   = QtGui.QPen(QtGui.QColor('#ff1744'), 3)
    BRUSH_RECT_HL = QtGui.QBrush(QtGui.QColor(255, 23, 68, 48))

    PEN_CIRCLE    = QtGui.QPen(QtGui.QColor('#00c853'), 3)
    PEN_CIRCLE_HL = QtGui.QPen(QtGui.QColor('#00e676'), 4)
    BRUSH_CIRC_HL = QtGui.QBrush(QtGui.QColor(0, 230, 118, 30))

    HANDLE_BR     = QtGui.QBrush(QtGui.QColor('#d32f2f'))
    HANDLE_PEN    = QtGui.QPen(QtCore.Qt.NoPen)

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
        self.model = get_model_for_difference(diff)
        self.is_up = is_up
        self._on_change = on_change

        # 文字颜色与缓存
        self._text_color = QtGui.QColor('#333') if color is None else QtGui.QColor(color)
        self._text_font = QtGui.QFont()
        self._text_cache_key: Optional[Tuple[int, int, str]] = None
        self._text_cached_pt: float = 10.0

        # UI/交互状态（与业务无关）
        self._extern_selected: bool = False
        self._selected_alpha: int = 200
        self._hl_rect   = False
        self._hl_circle = False

        self._mode = self.Mode.NONE
        self._drag_corner = -1
        self._edge_code = ''  # 'L','R','T','B'
        self._press_tl_scene = QtCore.QPointF()
        self._press_br_scene = QtCore.QPointF()
        self._anchor_scene   = QtCore.QPointF()
        self._press_center   = QtCore.QPointF()  # 圆心按下快照（局部）
        self._is_resizing    = False

        # 仅缓存“上一帧尺寸”
        self._cached_rect_size = QtCore.QSizeF(
            max(MIN_RECT_SIZE, float(self.model.width)),
            max(MIN_RECT_SIZE, float(self.model.height))
        )

        # 性能/Flags
        self.setCacheMode(QtWidgets.QGraphicsItem.DeviceCoordinateCache)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)              # 使用内置拖动
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsGeometryChanges, True)   # 以便截获移动
        self.setAcceptedMouseButtons(QtCore.Qt.LeftButton)
        self.setAcceptHoverEvents(True)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.setZValue(1)

        # 初始位置
        self.setPos(self.model.x, self.model.y)

        # 订阅 model（另一侧变化时我同步）
        self.model.geometryChanged.connect(self._on_model_geometry_changed)
        self.model.circleChanged.connect(self._on_model_circle_changed)
        self.model.anyChanged.connect(self._on_model_any_changed)

        # 首次发一次半径
        QtCore.QTimer.singleShot(0, self._emit_current_radius)

    # -------------------- 派生值（现算现用） --------------------
    def _radius_from_model(self) -> float:
        lvl = max(1, min(int(self.model.hint_level), len(RADIUS_LEVELS)))
        return float(RADIUS_LEVELS[lvl - 1])

    def _current_rect_local(self) -> QtCore.QRectF:
        """本地坐标下的矩形：始终 (0,0,w,h)"""
        w = max(MIN_RECT_SIZE, float(self.model.width))
        h = max(MIN_RECT_SIZE, float(self.model.height))
        return QtCore.QRectF(0, 0, w, h)

    def _current_circle_local(self) -> Tuple[QtCore.QPointF, float]:
        """返回（局部圆心, 半径），渲染时进行夹紧，不改 model。"""
        rect = self._current_rect_local()
        w, h = rect.width(), rect.height()
        r = self._radius_from_model()
        cx = float(self.model.cx) if self.model.cx >= 0 else w/2
        cy = float(self.model.cy) if self.model.cy >= 0 else h/2
        cx = max(r, min(cx, w - r))
        cy = max(r, min(cy, h - r))
        return QtCore.QPointF(cx, cy), r

    # -------------------- 绘制 --------------------
    def paint(self, p: QtGui.QPainter, option, widget=None):
        p.setRenderHints(QtGui.QPainter.RenderHint(0))
        rect = self._current_rect_local()
        c, r = self._current_circle_local()

        # 矩形
        if self._hl_rect:
            pen = self.PEN_RECT_HL; base_brush = self.BRUSH_RECT_HL
        else:
            pen = self.PEN_RECT;    base_brush = self.BRUSH_RECT
        p.setPen(pen)
        col = QtGui.QColor(base_brush.color())
        if self._extern_selected:
            col.setAlpha(min(255, self._selected_alpha))
        p.setBrush(QtGui.QBrush(col))
        p.drawRect(rect)

        # 本侧显示（沿用 up/down 逻辑）
        visible_for_side = (self.model.section == 'up') == self.is_up

        # 圆
        if self._hl_circle:
            p.setPen(self.PEN_CIRCLE_HL); p.setBrush(self.BRUSH_CIRC_HL)
        else:
            p.setPen(self.PEN_CIRCLE);    p.setBrush(QtCore.Qt.NoBrush)
        p.drawEllipse(QtCore.QRectF(c.x()-r, c.y()-r, 2*r, 2*r))

        # 文本：居中 + 自动换行 + 字号自适配
        label = (self.model.label or "").strip()
        if visible_for_side and label:
            box_side = min(rect.width(), rect.height()) * 0.9
            text_rect = QtCore.QRectF(c.x() - box_side/2.0,
                                      c.y() - box_side/2.0,
                                      box_side, box_side)
            pt = self._compute_fitting_pointsize(text_rect.width(), text_rect.height(), label)
            self._text_font.setPointSizeF(pt)
            p.setFont(self._text_font)
            p.setPen(QtGui.QPen(self._text_color))
            flags = QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap
            p.drawText(text_rect, flags, label)

        # 角把手
        hs = self.HANDLE_SIZE
        p.setPen(self.HANDLE_PEN); p.setBrush(self.HANDLE_BR)
        tl = rect.topLeft(); tr = rect.topRight()
        br = rect.bottomRight(); bl = rect.bottomLeft()
        for ptc in (tl, tr, br, bl):
            p.drawEllipse(QtCore.QRectF(ptc.x()-hs/2, ptc.y()-hs/2, hs, hs))

    def boundingRect(self) -> QtCore.QRectF:
        """基于缓存尺寸，遵守 Qt 的几何契约。"""
        sz = self._cached_rect_size
        return QtCore.QRectF(-4, -4, sz.width()+8, sz.height()+8)

    def shape(self) -> QtGui.QPainterPath:
        path = QtGui.QPainterPath()
        sz = self._cached_rect_size
        path.addRect(QtCore.QRectF(0, 0, sz.width(), sz.height()))
        return path

    # ====== 文本字号自适配 ======
    def _compute_fitting_pointsize(self, box_w: float, box_h: float, text: str) -> float:
        if box_w <= 1 or box_h <= 1 or not text:
            return 10.0
        key = (int(box_w), int(box_h), text)
        if self._text_cache_key == key:
            return float(self._text_cached_pt)

        lo, hi = 8.0, max(14.0, box_h * 0.9)
        best = lo
        test_font = QtGui.QFont(self._text_font)
        test_rect = QtCore.QRect(0, 0, int(box_w), 10_000)
        flags = QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap | QtCore.Qt.TextWrapAnywhere

        while hi - lo > 0.5:
            mid = (lo + hi) / 2.0
            test_font.setPointSizeF(mid)
            fm = QtGui.QFontMetrics(test_font)
            br = fm.boundingRect(test_rect, flags, text)
            if br.height() <= box_h and br.width() <= box_w:
                best = mid; lo = mid
            else:
                hi = mid

        self._text_cache_key = key
        self._text_cached_pt = float(best)
        return float(best)

    # -------------------- hover：高亮 + 指针 --------------------
    def hoverMoveEvent(self, e: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        if self._is_resizing:
            return
        pos = e.pos()
        rect = self._current_rect_local()

        # 角优先
        corner = self._hit_corner(rect, pos)
        if corner >= 0:
            self.setCursor(QtCore.Qt.SizeFDiagCursor if corner in (0, 2) else QtCore.Qt.SizeBDiagCursor)
            self._set_hover_state(rect_hl=True, circ_hl=False)
            return

        # 边
        edge = self._hit_edge(rect, pos)
        if edge:
            self.setCursor(QtCore.Qt.SizeHorCursor if edge in ('L','R') else QtCore.Qt.SizeVerCursor)
            self._set_hover_state(rect_hl=True, circ_hl=False)
            return

        # 圆
        if self._hit_circle(pos):
            self.setCursor(QtCore.Qt.OpenHandCursor)
            self._set_hover_state(rect_hl=False, circ_hl=True)
            return

        # 矩形内部也高亮
        if rect.contains(pos):
            self.setCursor(QtCore.Qt.OpenHandCursor)
            self._set_hover_state(rect_hl=True, circ_hl=False)
            return

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
        rect = self._current_rect_local()

        # 圆命中优先
        if self._hit_circle(e.pos()):
            self._mode = self.Mode.DRAG_CIRCLE
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            # 记录按下时圆心
            center, _ = self._current_circle_local()
            self._press_center = QtCore.QPointF(center)
            e.accept(); return

        # 角
        corner = self._hit_corner(rect, e.pos())
        if corner >= 0:
            self._mode = self.Mode.RESIZE_CORNER
            self._drag_corner = corner
            # 记录按下时 TL/BR（场景）
            tl_scene = self.mapToScene(rect.topLeft())
            br_scene = self.mapToScene(rect.bottomRight())
            self._press_tl_scene = tl_scene
            self._press_br_scene = br_scene
            # 对角锚点
            opp = [QtCore.QPointF(br_scene.x(), br_scene.y()),
                   QtCore.QPointF(tl_scene.x(), br_scene.y()),
                   QtCore.QPointF(tl_scene.x(), tl_scene.y()),
                   QtCore.QPointF(br_scene.x(), tl_scene.y())]
            self._anchor_scene = opp[corner]
            self._is_resizing = True
            self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, False)
            e.accept(); return

        # 边
        edge = self._hit_edge(rect, e.pos())
        if edge:
            self._mode = self.Mode.RESIZE_EDGE
            self._edge_code = edge
            tl_scene = self.mapToScene(rect.topLeft())
            br_scene = self.mapToScene(rect.bottomRight())
            self._press_tl_scene = tl_scene
            self._press_br_scene = br_scene
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

        scene_rect = self.scene().sceneRect() if self.scene() else QtCore.QRectF(-1e6, -1e6, 2e6, 2e6)

        if self._mode == self.Mode.RESIZE_CORNER:
            cur = e.scenePos()
            tl_scene = QtCore.QPointF(min(cur.x(), self._anchor_scene.x()),
                                      min(cur.y(), self._anchor_scene.y()))
            br_scene = QtCore.QPointF(max(cur.x(), self._anchor_scene.x()),
                                      max(cur.y(), self._anchor_scene.y()))
            tl_scene, br_scene = self._clamp_scene_rect(tl_scene, br_scene, scene_rect)
            # 应用到 model
            x, y, w, h = tl_scene.x(), tl_scene.y(), br_scene.x()-tl_scene.x(), br_scene.y()-tl_scene.y()
            self.model.set_rect(x, y, max(MIN_RECT_SIZE, w), max(MIN_RECT_SIZE, h), source=self)
            e.accept(); return

        if self._mode == self.Mode.RESIZE_EDGE:
            cur = e.scenePos()
            tl0, br0 = self._press_tl_scene, self._press_br_scene
            tl_scene = QtCore.QPointF(tl0)
            br_scene = QtCore.QPointF(br0)
            if self._edge_code == 'L':
                x = min(cur.x(), br0.x() - MIN_RECT_SIZE); tl_scene.setX(x)
            elif self._edge_code == 'R':
                x = max(cur.x(), tl0.x() + MIN_RECT_SIZE); br_scene.setX(x)
            elif self._edge_code == 'T':
                y = min(cur.y(), br0.y() - MIN_RECT_SIZE); tl_scene.setY(y)
            elif self._edge_code == 'B':
                y = max(cur.y(), tl0.y() + MIN_RECT_SIZE); br_scene.setY(y)

            tl_scene, br_scene = self._clamp_scene_rect(tl_scene, br_scene, scene_rect)
            x, y, w, h = tl_scene.x(), tl_scene.y(), br_scene.x()-tl_scene.x(), br_scene.y()-tl_scene.y()
            self.model.set_rect(x, y, max(MIN_RECT_SIZE, w), max(MIN_RECT_SIZE, h), source=self)
            e.accept(); return

        if self._mode == self.Mode.DRAG_CIRCLE:
            rect = self._current_rect_local()
            c0, r = self._current_circle_local()
            p_local = self.mapFromScene(e.scenePos())
            cx = max(r, min(p_local.x(), rect.width()  - r))
            cy = max(r, min(p_local.y(), rect.height() - r))
            self.model.set_circle(cx, cy, source=self)
            e.accept(); return

        e.ignore()

    def mouseReleaseEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        self._mode = self.Mode.NONE
        self._is_resizing = False
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        if callable(self._on_change):
            try:
                self._on_change(self.model.id)
            except Exception:
                pass
        super().mouseReleaseEvent(e)

    # -------------------- 外部选中态 --------------------
    def setExternalSelected(self, selected: bool, *, raise_z: bool = True) -> None:
        if self._extern_selected == bool(selected):
            return
        self._extern_selected = bool(selected)
        if raise_z:
            self.setZValue(2 if self._extern_selected else 1)
        self.update()

    # -------------------- itemChange：移动夹紧并写回 model --------------------
    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.ItemPositionChange and self.scene():
            # 夹到场景
            new_pos: QtCore.QPointF = value
            rect = self._current_rect_local()
            scene_rect = self.scene().sceneRect()
            new_x = max(scene_rect.left(),  min(new_pos.x(), scene_rect.right()  - rect.width()))
            new_y = max(scene_rect.top(),   min(new_pos.y(), scene_rect.bottom() - rect.height()))
            return QtCore.QPointF(new_x, new_y)

        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged:
            # 把移动写回 model（尺寸不变）
            rect = self._current_rect_local()
            self.model.set_rect(self.pos().x(), self.pos().y(), rect.width(), rect.height(), source=self)
        return super().itemChange(change, value)

    # -------------------- model → view 同步 --------------------
    @QtCore.Slot(object)
    def _on_model_geometry_changed(self, source):
        # 无论 source 是否 self，都更新缓存尺寸与位置（setPos 相同值不会抖）
        new_w = max(MIN_RECT_SIZE, float(self.model.width))
        new_h = max(MIN_RECT_SIZE, float(self.model.height))
        old_sz = self._cached_rect_size
        if abs(new_w - old_sz.width()) > 1e-6 or abs(new_h - old_sz.height()) > 1e-6:
            self.prepareGeometryChange()
            self._cached_rect_size = QtCore.QSizeF(new_w, new_h)
        self.setPos(self.model.x, self.model.y)
        self._text_cache_key = None
        self.update()
        # 尺寸变化可能影响半径（hint_level 已由 model 自动更新）
        self._emit_current_radius()

    @QtCore.Slot(object)
    def _on_model_circle_changed(self, source):
        self.update()

    @QtCore.Slot(object)
    def _on_model_any_changed(self, source):
        self._text_cache_key = None
        self.update()

    # -------------------- 半径信号 --------------------
    def _emit_current_radius(self):
        self.radiusChanged.emit(self.model.id, self._radius_from_model())

    # -------------------- 命中工具 --------------------
    def _hit_corner(self, rect: QtCore.QRectF, pos: QtCore.QPointF) -> int:
        corners = [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]
        for i, c in enumerate(corners):
            if QtCore.QLineF(pos, c).length() <= self.CORNER_THRESH:
                return i
        return -1

    def _hit_edge(self, rect: QtCore.QRectF, pos: QtCore.QPointF) -> str:
        et = self.EDGE_THRESH
        if 0 <= pos.y() <= rect.height():
            if abs(pos.x()-0.0)            <= et: return 'L'
            if abs(pos.x()-rect.width())   <= et: return 'R'
        if 0 <= pos.x() <= rect.width():
            if abs(pos.y()-0.0)            <= et: return 'T'
            if abs(pos.y()-rect.height())  <= et: return 'B'
        return ''

    def _hit_circle(self, pos: QtCore.QPointF) -> bool:
        c, r = self._current_circle_local()
        return QtCore.QLineF(pos, c).length() <= r

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
        br = QtCore.QPointF(min(scene_rect.right(),  tl.x() + w),
                            min(scene_rect.bottom(), tl.y() + h))
        return tl, br

    # -------------------- 外部 API --------------------
    def updateLabel(self):
        # 现用现取 model.label，仅需清缓存
        self._text_cache_key = None
        self.update()

    def setVis(self, show_rect: bool, show_circle: bool, show_label: bool):
        # 这里仍可扩展：若需要真正的“隐藏圆/矩形/文字”，可以加局部变量控制
        # 简化起见，先保持全部显示；如需开关，可仿照原有结构加 3 个布尔并在 paint 中判断
        self.update()

# -*- coding: utf-8 -*-
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets
from typing import Optional, Dict, Tuple

# 由你的工程提供
from models import Difference, RADIUS_LEVELS, MIN_RECT_SIZE
from circle_provider import CirclePixmapProvider

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
    # ------- 点击区域（补充） -------
    @property
    def click_customized(self): return bool(self.data.click_customized)
    @property
    def click_shape(self):  return self.data.cshape
    @property
    def click_cx(self):     return float(self.data.ccx)
    @property
    def click_cy(self):     return float(self.data.ccy)
    @property
    def click_a(self):      return float(self.data.ca)
    @property
    def click_b(self):      return float(self.data.cb)

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

    # ------- 设置 API -------
    def set_click_center(self, cx_abs: float, cy_abs: float, *, source=None):
        if self._updating: return
        d = self.data
        cx_abs, cy_abs = float(cx_abs), float(cy_abs)
        changed = (cx_abs != getattr(d, "ccx", -1.0)) or (cy_abs != getattr(d, "ccy", -1.0))
        if not changed: return
        d.ccx, d.ccy = cx_abs, cy_abs
        try: d.click_customized = True
        except Exception: pass
        self.anyChanged.emit(source)

    def set_click_axes(self, a: float, b: float, *, source=None):
        if self._updating: return
        d = self.data
        a = max(1.0, float(a)); b = max(1.0, float(b))
        changed = (a != getattr(d, "ca", 0.0)) or (b != getattr(d, "cb", 0.0))
        if not changed: return
        d.ca, d.cb = a, b
        try: d.click_customized = True
        except Exception: pass
        self.anyChanged.emit(source)

    def set_click_shape(self, shape: str, *, source=None):
        if self._updating: return
        d = self.data
        shape = (str(shape) or "rect").lower()
        shape = str(shape) if shape in ("rect", "ellipse") else "rect"
        if shape == getattr(d, "cshape", "rect"): return
        d.cshape = shape
        try: d.click_customized = True
        except Exception: pass
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

    # 新增：蓝色点击区域
    PEN_CLICK = QtGui.QPen(QtGui.QColor('#1976d2'), 2)
    BRUSH_CLICK = QtGui.QBrush(QtGui.QColor(25, 118, 210, 24))
    PEN_CLICK_HL = QtGui.QPen(QtGui.QColor('#42a5f5'), 3)
    BRUSH_CLICK_HL = QtGui.QBrush(QtGui.QColor(66, 165, 245, 36))

    HANDLE_BR_CLICK = QtGui.QBrush(QtGui.QColor('#1976d2'))   # 蓝色（和点击区域一致）
    HANDLE_PEN      = QtGui.QPen(QtCore.Qt.NoPen)

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
        CLICK_MOVE=5; CLICK_EDGE=6; CLICK_CORNER=7

    def __init__(self, diff: Difference,
                 color: Optional[QtGui.QColor] = None,
                 on_change=None,
                 is_up: bool = True):
        super().__init__()
        self.model = get_model_for_difference(diff)
        self.is_up = is_up
        self._on_change = on_change
        self._ordinal: int = 1 # 新增：显示用序号（1-based）

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
        self._hl_click  = False

        self._mode = self.Mode.NONE
        self._drag_corner = -1
        self._edge_code = ''  # 'L','R','T','B'
        self._press_tl_scene = QtCore.QPointF()
        self._press_br_scene = QtCore.QPointF()
        self._anchor_scene   = QtCore.QPointF()
        self._press_center   = QtCore.QPointF()  # 圆心按下快照（局部）
        self._is_resizing    = False

        # 可见性（内部控制，默认全开）
        self._show_click = True
        self._show_rect = True
        self._show_circle = True
        self._show_label = True

        self._cached_bounds_rect = self._compute_bounds_union()

        # 仅缓存“上一帧尺寸”
        self._cached_rect_size = QtCore.QSizeF(
            max(MIN_RECT_SIZE, float(self.model.width)),
            max(MIN_RECT_SIZE, float(self.model.height))
        )

        # 性能/Flags
        self.setCacheMode(QtWidgets.QGraphicsItem.NoCache)
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

        scene = self.scene()
        scene_rect = scene.sceneRect() if scene else QtCore.QRectF(-1e6, -1e6, 2e6, 2e6)

        # 若未设定绝对坐标，则默认用“矩形中心的场景坐标”
        if self.model.cx < 0 or self.model.cy < 0:
            cx_scene = self.model.x + w / 2.0
            cy_scene = self.model.y + h / 2.0
        else:
            cx_scene = float(self.model.cx)
            cy_scene = float(self.model.cy)
        # 夹紧到场景，保证整圆可见（如不想限制可移除这段）
        cx_scene = max(scene_rect.left() + r,  min(cx_scene, scene_rect.right()  - r))
        cy_scene = max(scene_rect.top()  + r,  min(cy_scene, scene_rect.bottom() - r))

        cx_local = cx_scene - self.model.x
        cy_local = cy_scene - self.model.y
        return QtCore.QPointF(cx_local, cy_local), r

    def _current_click_local(self) -> Tuple[QtCore.QPointF, float, float, str]:
        """
        返回 (局部中心, a, b, shape)；不对中心和半轴做红框约束。
        回退：若参数缺省/无效，使用红框中心和半轴；圆强制 a==b。
        """
        rect = self._current_rect_local()
        w, h = rect.width(), rect.height()

        cx_abs = self.model.click_cx
        cy_abs = self.model.click_cy
        a = self.model.click_a
        b = self.model.click_b
        shape = self.model.click_shape

        # 回退（未自定义/无效）
        if cx_abs < 0 or cy_abs < 0 or a <= 0 or b <= 0:
            c = QtCore.QPointF(w/2, h/2)
            if shape == "rect":
                a, b = w/2, h/2
            else:
                r = min(w, h) / 2
                a = b = r
                shape = "ellipse"
            return c, float(a), float(b), shape

        # 绝对 → 本地（不夹紧到红框）
        local_cx = cx_abs - self.model.x
        local_cy = cy_abs - self.model.y

        # 半轴最小值兜底（不做上限）
        a = max(1.0, float(a))
        b = max(1.0, float(b))
        cx_local, cy_local = self._clamp_center_to_scene(local_cx, local_cy, a, b)
        return QtCore.QPointF(cx_local, cy_local), float(a), float(b), shape


    def _click_handles(self, c: QtCore.QPointF, a: float, b: float, shape: str):
        """
        始终返回 8 个手柄：TL/TR/BR/BL（角） + L/R/T/B（边）
        a,b 为半轴（rect=半宽/半高；ellipse=长/短轴）
        """
        cx, cy = c.x(), c.y()

        # 角点
        TL = QtCore.QPointF(cx - a, cy - b)
        TR = QtCore.QPointF(cx + a, cy - b)
        BR = QtCore.QPointF(cx + a, cy + b)
        BL = QtCore.QPointF(cx - a, cy + b)

        # 边点
        L  = QtCore.QPointF(cx - a, cy)
        R  = QtCore.QPointF(cx + a, cy)
        T  = QtCore.QPointF(cx,     cy - b)
        B  = QtCore.QPointF(cx,     cy + b)

        return {"TL": TL, "TR": TR, "BR": BR, "BL": BL,
                "L": L, "R": R, "T": T, "B": B}

    def _drawRectCrisp(self, p: QtGui.QPainter, rect: QtCore.QRectF):
        p.save()
        p.translate(0.5, 0.5)
        r = QtCore.QRectF(int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height()))
        p.drawRect(r)
        p.restore()

    # -------------------- 绘制 --------------------
    # —— 小徽标绘制工具 —— #
    def _draw_badge(self, p: QtGui.QPainter, box: QtCore.QRectF, text: str,
                corner: str = "lt", d: float = 60.0, pad: float = 6.0):
        """
        固定字号 26 的序号徽标：红色渐变 + 白描边 + 白字(带阴影)。
        高度稍大，宽度随文字自适应（胶囊形）。
        """
        if not text:
            return

        # ---- 定位角落 ----
        if corner == "rt":
            x = box.right()  - d - pad; y = box.top()    + pad
        elif corner == "lb":
            x = box.left()   + pad;     y = box.bottom() - d - pad
        elif corner == "rb":
            x = box.right()  - d - pad; y = box.bottom() - d - pad
        else:  # "lt"
            x = box.left()   + pad;     y = box.top()    + pad

        # ---- 固定字号 26，测量文字 ----
        f = QtGui.QFont(self._text_font)
        f.setBold(True)
        f.setPointSizeF(26)
        fm = QtGui.QFontMetricsF(f)
        br = fm.tightBoundingRect(text)

        # ---- 内边距与尺寸 ----
        h = d * 1.2                        # 高度稍大一点
        hpad = max(8.0, d * 0.22)
        w = max(d, br.width() + 2 * hpad)
        badge = QtCore.QRectF(x, y, w, h)

        p.save()
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)

        # ---- 背景红渐变 + 白描边 ----
        grad = QtGui.QLinearGradient(badge.center().x() - w/2, badge.center().y(),
                                    badge.center().x() + w/2, badge.center().y())
        grad.setColorAt(0.0, QtGui.QColor("#b30000"))
        grad.setColorAt(0.5, QtGui.QColor("#ff4d4f"))
        grad.setColorAt(1.0, QtGui.QColor("#b30000"))
        p.setBrush(QtGui.QBrush(grad))
        p.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), 2))
        r = h * 0.5
        p.drawRoundedRect(badge, r, r)

        # ---- 白字 + 黑影 ----
        p.setFont(f)
        shadow_off = max(1.0, d * 0.03)
        p.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 180)))
        p.drawText(badge.translated(shadow_off, shadow_off), QtCore.Qt.AlignCenter, text)
        p.setPen(QtGui.QPen(QtCore.Qt.white))
        p.drawText(badge, QtCore.Qt.AlignCenter, text)

        p.restore()

    def paint(self, p: QtGui.QPainter, option, widget=None):
        p.setRenderHints(QtGui.QPainter.RenderHint(0))
        rect = self._current_rect_local()

        # 矩形
        if self._show_rect:
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
        if self._show_circle:
            c, r = self._current_circle_local()             # 先算
            lvl = max(1, min(int(self.model.hint_level), len(RADIUS_LEVELS)))
            pm  = CirclePixmapProvider.instance().get(lvl)
            bbox = self._circle_pixmap_bbox()
            if bbox:
                p.drawPixmap(bbox.topLeft(), pm)            # PNG 按 bbox 放置
                self._draw_badge(p, bbox, str(self._ordinal), corner="lt", d=20.0, pad=4.0)

            if self._hl_circle:
                p.save()
                p.setRenderHint(QtGui.QPainter.Antialiasing, True)
                # A) 外沿描边
                p.setPen(QtGui.QPen(QtGui.QColor('#00e676'), 2.5))
                p.setBrush(QtCore.Qt.NoBrush)
                p.drawEllipse(bbox)
                # B) 光晕环（外扩一圈）
                glow = 8.0
                outer = bbox.adjusted(-glow, -glow, glow, glow)
                path_outer = QtGui.QPainterPath(); path_outer.addEllipse(outer)
                path_inner = QtGui.QPainterPath(); path_inner.addEllipse(bbox)
                ring = path_outer.subtracted(path_inner)
                p.setPen(QtCore.Qt.NoPen)
                p.setBrush(QtGui.QColor(0, 230, 118, 40))
                p.drawPath(ring)
                p.restore()

        # 文本：居中 + 自动换行 + 字号自适配
        label = (self.model.label or "").strip()
        if visible_for_side and self._show_label and label:
            rect_local = self._current_rect_local()
            # 给文字留一点内边距
            pad = max(4.0, min(rect_local.width(), rect_local.height()) * 0.06)
            text_rect = rect_local.adjusted(pad, pad, -pad, -pad)
            # 自适应字号
            pt = self._compute_fitting_pointsize(text_rect.width(), text_rect.height(), label)
            self._text_font.setPointSizeF(pt)
            p.setFont(self._text_font)
            p.setPen(QtGui.QPen(self._text_color))
            flags = QtCore.Qt.AlignCenter | QtCore.Qt.TextWordWrap
            p.drawText(text_rect, flags, label)

        # 角把手
        if self._show_rect:
            hs = self.HANDLE_SIZE
            p.setPen(self.HANDLE_PEN); p.setBrush(self.HANDLE_BR)
            tl = rect.topLeft(); tr = rect.topRight()
            br = rect.bottomRight(); bl = rect.bottomLeft()
            for ptc in (tl, tr, br, bl):
                p.drawEllipse(QtCore.QRectF(ptc.x()-hs/2, ptc.y()-hs/2, hs, hs))

        if self.model.click_customized and self._show_click:
            c, a, b, shape = self._current_click_local()
            click_rect = QtCore.QRectF(c.x()-a, c.y()-b, 2*a, 2*b)
            p.setPen(self.PEN_CLICK if not self._hl_click else self.PEN_CLICK_HL)
            p.setBrush(self.BRUSH_CLICK if not self._hl_click else self.BRUSH_CLICK_HL)
            if shape == "rect":
                p.drawRect(click_rect)
            else:  # ellipse
                p.drawEllipse(QtCore.QRectF(c.x()-a, c.y()-b, 2*a, 2*b))
            self._draw_badge(p, click_rect, str(self._ordinal), corner="lt", d=20.0, pad=4.0)

            hs = self.HANDLE_SIZE
            p.setPen(self.HANDLE_PEN); p.setBrush(self.HANDLE_BR_CLICK)
            for ptc in self._click_handles(c, a, b, shape).values():
                p.drawEllipse(QtCore.QRectF(ptc.x()-hs/2, ptc.y()-hs/2, hs, hs))

    def _scene_pick_radius(self, px: float = 12.0) -> float:
        """
        把屏幕像素(px)换算成当前场景坐标下的长度，随 QGraphicsView 的缩放自适应。
        无视图/无场景时返回 px 作为兜底。
        """
        scene = self.scene()
        if scene is None:
            return float(px)

        views = scene.views()
        if not views:
            return float(px)

        view = views[0]
        # QTransform.inverted() 在 PySide6 返回 (inv, ok)
        inv, ok = view.transform().inverted()
        if not ok:
            return float(px)

        # 把一个 px x px 的屏幕矩形映射到场景，取其宽作为命中半径
        rect_in_scene = inv.mapRect(QtCore.QRectF(0.0, 0.0, float(px), float(px)))
        # 最小兜底，避免过小导致难命中
        return max(2.0, rect_in_scene.width())
    
    def _compute_bounds_union(self) -> QtCore.QRectF:
        rect = self._current_rect_local()
        uni  = QtCore.QRectF(rect)

        if self._show_circle:
            bbox = self._circle_pixmap_bbox()
            if bbox:
                uni = uni.united(bbox)

        if self._show_click:
            c, a, b, _ = self._current_click_local()
            click_rect = QtCore.QRectF(c.x()-a, c.y()-b, 2*a, 2*b)
            uni = uni.united(click_rect)

        # ★ margin 取 hand-pick 半径与 8 的较大者，确保手柄泡泡也在 boundingRect 内
        margin = max(8.0, self._scene_pick_radius(12.0))
        return uni.adjusted(-margin, -margin, margin, margin)

    def _refresh_bounds_if_needed(self):
        old = QtCore.QRectF(self._cached_bounds_rect)
        new = self._compute_bounds_union()
        if (abs(new.x()-old.x())>1e-6 or abs(new.y()-old.y())>1e-6 or
            abs(new.width()-old.width())>1e-6 or abs(new.height()-old.height())>1e-6):
            self.prepareGeometryChange()
            self._cached_bounds_rect = new
            # 同时无效化旧+新区域，抹干净残影
            self.update(old.united(new))

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(self._cached_bounds_rect)
    
    def shape(self) -> QtGui.QPainterPath:
        path = QtGui.QPainterPath()
        rect = self._current_rect_local()
        path.addRect(rect)

        if self._show_circle:
            bbox = self._circle_pixmap_bbox()
            if bbox:
                circ = QtGui.QPainterPath()
                circ.addRect(bbox)  # 用 PNG 的外接矩形，而不是几何圆
                path = path.united(circ)

        if self._show_click:
            c, a, b, shape = self._current_click_local()
            click_rect = QtCore.QRectF(c.x()-a, c.y()-b, 2*a, 2*b)
            click = QtGui.QPainterPath()
            if shape == "rect":
                click.addRect(click_rect)
            else:
                click.addEllipse(click_rect)

            # 椭圆边沿粗描边（整条边易点）
            stroker = QtGui.QPainterPathStroker()
            stroker.setWidth(self._scene_pick_radius(16.0))
            fat_edge = stroker.createStroke(click)

            # ★ 关键：把 8 个手柄“泡泡”并入 shape（角点在椭圆外也能接收事件）
            pick = self._scene_pick_radius(12.0)
            handles = self._click_handles(c, a, b, shape)
            handle_path = QtGui.QPainterPath()
            for pt in handles.values():
                handle_path.addEllipse(QtCore.QRectF(pt.x()-pick, pt.y()-pick, 2*pick, 2*pick))

            path = path.united(click).united(fat_edge).united(handle_path)

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

        # 点击区域手柄优先
        if self._show_click:
            hcode = self._hit_click_handle(pos)
            if hcode:
                if hcode in ("L","R"): self.setCursor(QtCore.Qt.SizeHorCursor)
                elif hcode in ("T","B"): self.setCursor(QtCore.Qt.SizeVerCursor)
                elif hcode in ("TL", "BR"): self.setCursor(QtCore.Qt.SizeFDiagCursor)
                else : self.setCursor(QtCore.Qt.SizeBDiagCursor)
                self._set_hover_state(rect_hl=False, circ_hl=False, click_hl=True); return

            # 点击区域本体
            if self._hit_click_inside(pos):
                self.setCursor(QtCore.Qt.OpenHandCursor)
                self._set_hover_state(rect_hl=False, circ_hl=False, click_hl=True); return
            
        # 圆
        if self._show_circle and self._hit_circle(pos):
            self.setCursor(QtCore.Qt.OpenHandCursor)
            self._set_hover_state(rect_hl=False, circ_hl=True, click_hl=False)
            return
        
        # 矩形
        # 角优先
        if self._show_rect:
            corner = self._hit_corner(rect, pos)
            if corner >= 0:
                self.setCursor(QtCore.Qt.SizeFDiagCursor if corner in (0, 2) else QtCore.Qt.SizeBDiagCursor)
                self._set_hover_state(rect_hl=True, circ_hl=False, click_hl=False)
                return

            # 边
            edge = self._hit_edge(rect, pos)
            if edge:
                self.setCursor(QtCore.Qt.SizeHorCursor if edge in ('L','R') else QtCore.Qt.SizeVerCursor)
                self._set_hover_state(rect_hl=True, circ_hl=False, click_hl=False)
                return

        

        # 矩形内部也高亮
        if self._show_rect and rect.contains(pos):
            self.setCursor(QtCore.Qt.OpenHandCursor)
            self._set_hover_state(rect_hl=True, circ_hl=False, click_hl=False)
            return

        self.setCursor(QtCore.Qt.OpenHandCursor)
        self._set_hover_state(rect_hl=False, circ_hl=False, click_hl=False)
        super().hoverMoveEvent(e)

    def hoverLeaveEvent(self, e: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        self.unsetCursor()
        self._set_hover_state(rect_hl=False, circ_hl=False, click_hl=False)
        super().hoverLeaveEvent(e)

    def _set_hover_state(self, rect_hl: bool, circ_hl: bool, click_hl: bool=False):
        changed = False
        if self._hl_rect != rect_hl:
            self._hl_rect = rect_hl; changed = True
        if self._hl_circle != circ_hl:
            self._hl_circle = circ_hl; changed = True
        if self._hl_click != click_hl:
            self._hl_click = click_hl; changed = True
        if changed:
            self.update()

    # -------------------- 鼠标交互 --------------------
    def mousePressEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        self._mode = self.Mode.NONE
        self._drag_corner = -1
        self._edge_code = ''
        rect = self._current_rect_local()

        hcode = self._hit_click_handle(e.pos())
        if hcode:
            self._mode = self.Mode.CLICK_EDGE if hcode in ("L","R","T","B") else self.Mode.CLICK_CORNER
            self._click_hcode = hcode
            self._click_press_local = e.pos()
            self._click_press_center, self._click_press_a, self._click_press_b, self._click_press_shape = self._current_click_local()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            e.accept(); return
        # 点击区域本体
        if self._hit_click_inside(e.pos()):
            self._mode = self.Mode.CLICK_MOVE
            self._click_press_local = e.pos()
            self._click_press_center, self._click_press_a, self._click_press_b, self._click_press_shape = self._current_click_local()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            e.accept(); return

        # 圆命中优先
        if self._hit_circle(e.pos()):
            if not self._show_circle:
                return False
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
            # 目标圆心（场景坐标）
            cx_scene = e.scenePos().x()
            cy_scene = e.scenePos().y()

            # 半径
            _, r = self._current_circle_local()

            # 场景夹紧（可选）
            scene = self.scene()
            scene_rect = scene.sceneRect() if scene else QtCore.QRectF(-1e6, -1e6, 2e6, 2e6)
            cx_scene = max(scene_rect.left() + r,  min(cx_scene, scene_rect.right()  - r))
            cy_scene = max(scene_rect.top()  + r,  min(cy_scene, scene_rect.bottom() - r))

            # ★ 写回“绝对（场景）坐标”
            self.model.set_circle(cx_scene, cy_scene, source=self)
            e.accept(); return
        
        if self._mode in (self.Mode.CLICK_MOVE, self.Mode.CLICK_EDGE, self.Mode.CLICK_CORNER):
            rect = self._current_rect_local()
            w, h = rect.width(), rect.height()
            cur = self.mapFromScene(e.scenePos())

            c0, a0, b0, shape = self._click_press_center, self._click_press_a, self._click_press_b, self._click_press_shape
            cx, cy = c0.x(), c0.y()
            a, b = a0, b0

            # 场景矩形（用于夹紧）
            scene = self.scene()
            scene_rect = scene.sceneRect() if scene else QtCore.QRectF(-1e6, -1e6, 2e6, 2e6)

            if self._mode == self.Mode.CLICK_MOVE:
                dx = cur.x() - self._click_press_local.x()
                dy = cur.y() - self._click_press_local.y()
                cx_moved = cx + dx
                cy_moved = cy + dy

                # 只夹中心：保证整框在场景内；a0/b0 不变
                a_fix = max(1.0, float(a0))
                b_fix = max(1.0, float(b0))
                cx_scene = self.model.x + cx_moved
                cy_scene = self.model.y + cy_moved
                cx_scene = min(max(scene_rect.left()  + a_fix, cx_scene), scene_rect.right()  - a_fix)
                cy_scene = min(max(scene_rect.top()   + b_fix, cy_scene), scene_rect.bottom() - b_fix)

                # 写回绝对坐标
                self.model.set_click_center(cx_scene, cy_scene, source=self)
                e.accept(); return

            if self._mode == self.Mode.CLICK_EDGE:
                code = self._click_hcode
                cx_loc, cy_loc = cx, cy  # 局部中心保持不变

                # 根据拖动方向更新 a/b（局部测度）
                if code in ("L", "R"):
                    a_new = abs(cur.x() - cx_loc); b_new = b0
                elif code in ("T", "B"):
                    a_new = a0;                    b_new = abs(cur.y() - cy_loc)
                else:
                    a_new, b_new = a0, b0  # 兜底

                # 只夹半轴：以中心为锚，半轴不得越出场景
                cx_scene = self.model.x + cx_loc
                cy_scene = self.model.y + cy_loc
                max_a = max(1.0, min(cx_scene - scene_rect.left(),  scene_rect.right()  - cx_scene))
                max_b = max(1.0, min(cy_scene - scene_rect.top(),   scene_rect.bottom() - cy_scene))
                a_new = max(1.0, min(float(a_new), max_a))
                b_new = max(1.0, min(float(b_new), max_b))

                self.model.set_click_axes(a_new, b_new, source=self)
                e.accept(); return
            if self._mode == self.Mode.CLICK_CORNER:
                cx_loc, cy_loc = cx, cy  # 局部中心保持不变
                if shape == "rect":
                    a_new = abs(cur.x() - cx_loc)
                    b_new = abs(cur.y() - cy_loc)
                else:
                    # 椭圆：等比缩放
                    v0 = self._click_press_local - c0
                    v1 = cur - c0
                    len0 = max(1e-6, QtCore.QLineF(QtCore.QPointF(0, 0), v0).length())
                    s = QtCore.QLineF(QtCore.QPointF(0, 0), v1).length() / len0
                    a_new = max(1.0, a0 * s)
                    b_new = max(1.0, b0 * s)

                # 只夹半轴：以中心为锚，半轴不得越出场景
                cx_scene = self.model.x + cx_loc
                cy_scene = self.model.y + cy_loc
                max_a = max(1.0, min(cx_scene - scene_rect.left(),  scene_rect.right()  - cx_scene))
                max_b = max(1.0, min(cy_scene - scene_rect.top(),   scene_rect.bottom() - cy_scene))
                a_new = max(1.0, min(float(a_new), max_a))
                b_new = max(1.0, min(float(b_new), max_b))

                self.model.set_click_axes(a_new, b_new, source=self)
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

    def contextMenuEvent(self, e: QtWidgets.QGraphicsSceneContextMenuEvent) -> None:
        if not self._show_click:
            return super().contextMenuEvent(e)

        pos = e.pos()
        hit_handle = self._hit_click_handle(pos)
        hit_inside = self._hit_click_inside(pos)
        if not (hit_handle or hit_inside):
            return super().contextMenuEvent(e)

        # 当前形状
        _, _, _, shape = self._current_click_local()

        menu = QtWidgets.QMenu(self.scene().views()[0] if self.scene() and self.scene().views() else None)
        act_toggle = menu.addAction("改为椭圆" if shape == "rect" else "改为矩形")

        # 关键修复：把 screenPos 处理成 QPoint
        sp = e.screenPos()
        if isinstance(sp, QtCore.QPointF):
            global_pt = sp.toPoint()
        else:
            global_pt = sp  # 已经是 QPoint

        chosen = menu.exec(global_pt)  # 这里传 QPoint 即可
        if chosen is None:
            e.accept(); return

        if chosen == act_toggle:
            new_shape = "ellipse" if shape == "rect" else "rect"
            self.model.set_click_shape(new_shape, source=self)
            if hasattr(self, "_refresh_bounds_if_needed"):
                self._refresh_bounds_if_needed()
            self.update()
            if (callable(self._on_change)):
                try:
                    self._on_change(self.model.id)
                except Exception:
                    pass
            e.accept(); return

        e.accept()

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
        self._refresh_bounds_if_needed()
        self._text_cache_key = None
        self.update()

    @QtCore.Slot(object)
    def _on_model_circle_changed(self, source):
        self._refresh_bounds_if_needed()   # ★ 新增
        self.update()

    @QtCore.Slot(object)
    def _on_model_any_changed(self, source):
        self._text_cache_key = None
        self._refresh_bounds_if_needed()
        self.update()

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
        bbox = self._circle_pixmap_bbox()
        if not bbox:
            # 无 PNG 时退回旧的“半径判定”
            c, r = self._current_circle_local()
            pad = self._scene_pick_radius(4.0)
            return QtCore.QLineF(pos, c).length() <= (r + pad)

        # 轻微外扩，提升命中手感
        pad = self._scene_pick_radius(3.0)
        hit = bbox.adjusted(-pad, -pad, pad, pad)
        return hit.contains(pos)
    
    def _hit_click_handle(self, pos):
        if not self._show_click: return None
        c, a, b, shape = self._current_click_local()
        handles = self._click_handles(c, a, b, shape)
        pick_edge   = self._scene_pick_radius(16.0)  # L/R/T/B 更宽松
        pick_corner = self._scene_pick_radius(12.0)

        for code in ("L","R","T","B"):
            if code in handles and QtCore.QLineF(pos, handles[code]).length() <= pick_edge:
                return code
        for code in ("TL","TR","BR","BL"):
            if code in handles and QtCore.QLineF(pos, handles[code]).length() <= pick_corner:
                return code
        return None
    
    def _hit_click_inside(self, pos: QtCore.QPointF) -> bool:
        if not self._show_click: return False
        c, a, b, shape = self._current_click_local()
        dx, dy = pos.x()-c.x(), pos.y()-c.y()
        if shape == "rect":
            return abs(dx) <= a and abs(dy) <= b
        # ellipse
        return (dx*dx)/(a*a+1e-6) + (dy*dy)/(b*b+1e-6) <= 1.0

    # -------------------- 场景几何工具 --------------------
    def _circle_pixmap_bbox(self) -> Optional[QtCore.QRectF]:
        if not self._show_circle:
            return None
        c, r = self._current_circle_local()

        lvl = max(1, min(int(self.model.hint_level), len(RADIUS_LEVELS)))
        pm  = CirclePixmapProvider.instance().get(lvl)
        if pm.isNull():
            # 没有 PNG 时退回数学圆（也能工作）
            return QtCore.QRectF(c.x()-r, c.y()-r, 2*r, 2*r)

        # 处理高 DPI：逻辑尺寸 = 像素尺寸 / DPR
        dpr = getattr(pm, "devicePixelRatio", lambda: 1.0)()
        w = pm.width()  / (dpr or 1.0)
        h = pm.height() / (dpr or 1.0)
        top_left = QtCore.QPointF(c.x() - w*0.5, c.y() - h*0.5)
        return QtCore.QRectF(top_left, QtCore.QSizeF(w, h))

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
    
    def _clamp_center_to_scene(self, cx_local: float, cy_local: float, a: float, b: float):
        """只夹紧中心点到 sceneRect，a/b 完全不动。入参/出参均为【局部坐标】。"""
        scene = self.scene()
        if not scene:
            return cx_local, cy_local
        scene_rect = scene.sceneRect()

        # 当前中心的“场景坐标”
        cx_scene = self.model.x + float(cx_local)
        cy_scene = self.model.y + float(cy_local)

        # 仅用 a/b 计算可见边界，绝不改 a/b
        a = max(1.0, float(a)); b = max(1.0, float(b))
        cx_scene = min(max(scene_rect.left()  + a, cx_scene), scene_rect.right()  - a)
        cy_scene = min(max(scene_rect.top()   + b, cy_scene), scene_rect.bottom() - b)

        # 回到局部
        return cx_scene - self.model.x, cy_scene - self.model.y


    def _clamp_axes_to_scene(self, cx_local: float, cy_local: float, a: float, b: float):
        """只夹紧半轴到 sceneRect，中心点不动。入参/出参均为【局部坐标】。"""
        scene = self.scene()
        if not scene:
            return max(1.0, float(a)), max(1.0, float(b))
        scene_rect = scene.sceneRect()

        cx_scene = self.model.x + float(cx_local)
        cy_scene = self.model.y + float(cy_local)

        # 以“中心点”为锚，半轴不能越出场景
        max_a = max(1.0, min(cx_scene - scene_rect.left(),  scene_rect.right()  - cx_scene))
        max_b = max(1.0, min(cy_scene - scene_rect.top(),   scene_rect.bottom() - cy_scene))

        a = max(1.0, min(float(a), max_a))
        b = max(1.0, min(float(b), max_b))
        return a, b

    
    def _clamp_click_to_scene(self, cx, cy, a, b, shape: str):
        scene_rect = self.scene().sceneRect() if self.scene() else QtCore.QRectF(-1e6,-1e6,2e6,2e6)
        # 中心（场景）
        cx_scene = self.model.x + cx
        cy_scene = self.model.y + cy

        # 允许超出红框，但保证在场景内完整可见
        max_a = min(cx_scene - scene_rect.left(), scene_rect.right() - cx_scene)
        max_b = min(cy_scene - scene_rect.top(),  scene_rect.bottom() - cy_scene)
        max_a = max(1.0, float(max_a))
        max_b = max(1.0, float(max_b))

        a = max(1.0, min(float(a), max_a))
        b = max(1.0, min(float(b), max_b))

        cx_scene = max(scene_rect.left()  + a, min(cx_scene, scene_rect.right()  - a))
        cy_scene = max(scene_rect.top()   + b, min(cy_scene, scene_rect.bottom() - b))

        return cx_scene - self.model.x, cy_scene - self.model.y, a, b


    # -------------------- 外部 API --------------------
    def setOrdinal(self, n: int):
        n = max(1, int(n))
        if self._ordinal != n:
            self._ordinal = n
            self.update()

    def updateLabel(self):
        # 现用现取 model.label，仅需清缓存
        self._text_cache_key = None
        self.update()

    def setVis(self, show_click: bool, show_rect: bool, show_circle: bool, show_label: bool):
        # 这里仍可扩展：若需要真正的“隐藏圆/矩形/文字”，可以加局部变量控制
        # 简化起见，先保持全部显示；如需开关，可仿照原有结构加 3 个布尔并在 paint 中判断
        changed = False
        if self._show_click  != bool(show_click):  self._show_click  = bool(show_click);  changed = True
        if self._show_rect   != bool(show_rect):   self._show_rect   = bool(show_rect);   changed = True
        if self._show_circle != bool(show_circle): self._show_circle = bool(show_circle); changed = True
        if self._show_label  != bool(show_label):  self._show_label  = bool(show_label);  changed = True
        if changed:
            # 关闭矩形时去掉矩形高亮；关闭圆时去掉圆高亮
            if not self._show_rect:   self._hl_rect = False
            if not self._show_circle: self._hl_circle = False
            if not self._show_click:  self._hl_click = False
            self._refresh_bounds_if_needed()
            self.update()

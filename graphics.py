# -*- coding: utf-8 -*-
from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets
from typing import Optional, Tuple
# 由你的工程提供
from models import Cat, MIN_RECT_SIZE


# ==============================================================
# ==============================================================

class CatItem(QtWidgets.QGraphicsObject):
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


    HANDLE_BR     = QtGui.QBrush(QtGui.QColor('#d32f2f'))
    HANDLE_PEN    = QtGui.QPen(QtCore.Qt.NoPen)

    HANDLE_SIZE    = 9.0
    EDGE_THRESH    = 8.0
    CORNER_THRESH  = 12.0

    class Mode:
        NONE=0; MOVE=1; RESIZE_CORNER=2; RESIZE_EDGE=3;
        CLICK_MOVE=4; CLICK_EDGE=5; CLICK_CORNER=6

    def __init__(self, cat: Cat,
                 color: Optional[QtGui.QColor] = None,
                 on_change=None,
                 is_up: bool = True):
        super().__init__()
        self.model = cat
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

        self._cached_bounds_rect = self._compute_bounds_union()

        # 仅缓存“上一帧尺寸”
        self._cached_rect_size = QtCore.QSizeF(
            max(MIN_RECT_SIZE, float(self.model.width)),
            max(MIN_RECT_SIZE, float(self.model.height))
        )
        self._syncing_from_model = False
        # 性能/Flags
        self.setCacheMode(QtWidgets.QGraphicsItem.NoCache)
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsGeometryChanges, True)   # 以便截获移动
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, bool(self.model.enabled))
        self.setAcceptedMouseButtons(QtCore.Qt.LeftButton)
        self.setAcceptHoverEvents(True)
        self.setCursor(QtCore.Qt.OpenHandCursor)
        self.setZValue(1)

        # 初始位置
        self.setPos(self.model.x, self.model.y)

    # -------------------- 派生值（现算现用） --------------------
    def _current_rect_local(self) -> QtCore.QRectF:
        """本地坐标下的矩形：始终 (0,0,w,h)"""
        w = max(MIN_RECT_SIZE, float(self.model.width))
        h = max(MIN_RECT_SIZE, float(self.model.height))
        return QtCore.QRectF(0, 0, w, h)

    def sync_from_model(self) -> None:
        self._syncing_from_model = True
        try:
            self.setPos(float(self.model.x), float(self.model.y))
            self._refresh_bounds_if_needed()
            self.update()
        finally:
            self._syncing_from_model = False

    def _emit_change(self) -> None:
        if callable(self._on_change):
            try:
                self._on_change(self.model.id)
            except Exception:
                pass

        # 仅当 enabled 为 True 才允许矩形交互（移动/拉伸）
    def _rect_interactions_allowed(self) -> bool:
        return bool(self.model.enabled)

    def _current_click_local(self) -> Tuple[QtCore.QPointF, float, float, str]:
        """
        返回 (局部中心, a, b, shape)；不对中心和半轴做红框约束。
        回退：若参数缺省/无效，使用红框中心和半轴；圆强制 a==b。
        """
        rect = self._current_rect_local()
        w, h = rect.width(), rect.height()

        cx_abs = self.model.ccx
        cy_abs = self.model.ccy
        a = self.model.ca
        b = self.model.cb
        shape = self.model.cshape

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

    def _click_handles(self, c: QtCore.QPointF, a: float, b: float):
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

        # 矩形
        if self._show_rect:
            rect = self._current_rect_local()
            pen = self.PEN_RECT_HL if self._hl_rect else self.PEN_RECT
            base_brush = self.BRUSH_RECT_HL if self._hl_rect else self.BRUSH_RECT
            p.setPen(pen)
            col = QtGui.QColor(base_brush.color())
            if self._extern_selected:
                col.setAlpha(min(255, self._selected_alpha))
            p.setBrush(QtGui.QBrush(col))
            p.drawRect(rect)
            self._draw_badge(p, rect, str(self._ordinal), corner="lt", d=20.0, pad=4.0)

            # 角把手
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
            p.drawRect(click_rect)
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

    # -------------------- hover：高亮 + 指针 --------------------
    def hoverMoveEvent(self, e: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        if self._is_resizing:
            return
        pos = e.pos()
        rect = self._current_rect_local()

        # 点击区域手柄优先
        if self._can_hit_click():
            hcode = self._hit_click_handle(pos)
            if hcode:
                if hcode in ("L","R"): self.setCursor(QtCore.Qt.SizeHorCursor)
                elif hcode in ("T","B"): self.setCursor(QtCore.Qt.SizeVerCursor)
                elif hcode in ("TL", "BR"): self.setCursor(QtCore.Qt.SizeFDiagCursor)
                else : self.setCursor(QtCore.Qt.SizeBDiagCursor)
                self._set_hover_state(rect_hl=False, click_hl=True)
                return

            # 点击区域本体
            if self._hit_click_inside(pos):
                self.setCursor(QtCore.Qt.OpenHandCursor)
                self._set_hover_state(rect_hl=False, click_hl=True)
                return

        # 矩形
        if self._can_hit_rect():
            # 角优先
            corner = self._hit_corner(rect, pos)
            if corner >= 0:
                self.setCursor(QtCore.Qt.SizeFDiagCursor if corner in (0, 2) else QtCore.Qt.SizeBDiagCursor)
                self._set_hover_state(rect_hl=True, click_hl=False)
                return

            # 边
            edge = self._hit_edge(rect, pos)
            if edge:
                self.setCursor(QtCore.Qt.SizeHorCursor if edge in ('L','R') else QtCore.Qt.SizeVerCursor)
                self._set_hover_state(rect_hl=True, click_hl=False)
                return

            # 矩形内部也高亮
            if self._show_rect and rect.contains(pos):
                self.setCursor(QtCore.Qt.OpenHandCursor)
                self._set_hover_state(rect_hl=True, click_hl=False)
                return

        # self.setCursor(QtCore.Qt.OpenHandCursor)
        self.setCursor(QtCore.Qt.ArrowCursor)
        self._set_hover_state(rect_hl=False, click_hl=False)
        super().hoverMoveEvent(e)

    def hoverLeaveEvent(self, e: QtWidgets.QGraphicsSceneHoverEvent) -> None:
        self.unsetCursor()
        self._set_hover_state(rect_hl=False, click_hl=False)
        super().hoverLeaveEvent(e)

    def _set_hover_state(self, rect_hl: bool, click_hl: bool=False):
        changed = False
        if self._hl_rect != rect_hl:
            self._hl_rect = rect_hl; changed = True
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

        if self._can_hit_click():
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

        if self._can_hit_rect():
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
                e.accept(); return

            # 默认移动（交给内置拖动）
            self._mode = self.Mode.MOVE
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            super().mousePressEvent(e)
            e.accept(); return

        e.ignore()

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
            self.model.set_rect(x, y, max(MIN_RECT_SIZE, w), max(MIN_RECT_SIZE, h))
            self._refresh_bounds_if_needed()
            self.update()
            self._emit_change()
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
            self.model.set_rect(x, y, max(MIN_RECT_SIZE, w), max(MIN_RECT_SIZE, h))
            self._refresh_bounds_if_needed()
            self.update()
            self._emit_change()
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
                self.model.set_click_center(cx_scene, cy_scene)
                self._refresh_bounds_if_needed()
                self.update()
                self._emit_change()
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

                self.model.set_click_axes(a_new, b_new)
                self._refresh_bounds_if_needed()
                self.update()
                self._emit_change()
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

                self.model.set_click_axes(a_new, b_new)
                self._refresh_bounds_if_needed()
                self.update()
                self._emit_change()
                e.accept(); return

        e.ignore()

    def mouseReleaseEvent(self, e: QtWidgets.QGraphicsSceneMouseEvent):
        self._mode = self.Mode.NONE
        self._is_resizing = False
        self.setCursor(QtCore.Qt.OpenHandCursor)
        super().mouseReleaseEvent(e)

    def itemChange(self, change, value):
        if change == QtWidgets.QGraphicsItem.ItemPositionChange and self.scene():
            if self._syncing_from_model:
                return value
            if not self._rect_interactions_allowed():
                return QtCore.QPointF(self.pos())
            rect = self._current_rect_local()
            scene_rect = self.scene().sceneRect()
            new_pos: QtCore.QPointF = value
            new_x = max(scene_rect.left(),  min(new_pos.x(), scene_rect.right()  - rect.width()))
            new_y = max(scene_rect.top(),   min(new_pos.y(), scene_rect.bottom() - rect.height()))
            return QtCore.QPointF(new_x, new_y)

        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged:
            if not self._syncing_from_model and self._rect_interactions_allowed():
                rect = self._current_rect_local()
                self.model.set_rect(self.pos().x(), self.pos().y(), rect.width(), rect.height())
                self._emit_change()
            return super().itemChange(change, value)
        return super().itemChange(change, value)

    # -------------------- 命中工具 --------------------

    # ===== 统一命中前置判定（新增） =====
    def _can_hit_rect(self) -> bool:
        """红框是否参与命中：需可见且允许交互（enabled）。"""
        return self._show_rect and self._rect_interactions_allowed() and self.isVisible()

    def _can_hit_click(self) -> bool:
        """点击区域是否参与命中：需已自定义且可见。"""
        return self.model.click_customized and self._show_click and self.isVisible()

    def _hit_corner(self, rect: QtCore.QRectF, pos: QtCore.QPointF) -> int:
        if not self._can_hit_rect():
            return -1
        corners = [rect.topLeft(), rect.topRight(), rect.bottomRight(), rect.bottomLeft()]
        for i, c in enumerate(corners):
            if QtCore.QLineF(pos, c).length() <= self.CORNER_THRESH:
                return i
        return -1

    def _hit_edge(self, rect: QtCore.QRectF, pos: QtCore.QPointF) -> str:
        if not self._can_hit_rect():
            return ''
        et = self.EDGE_THRESH
        if 0 <= pos.y() <= rect.height():
            if abs(pos.x()-0.0)            <= et: return 'L'
            if abs(pos.x()-rect.width())   <= et: return 'R'
        if 0 <= pos.x() <= rect.width():
            if abs(pos.y()-0.0)            <= et: return 'T'
            if abs(pos.y()-rect.height())  <= et: return 'B'
        return ''

    def _hit_click_handle(self, pos):
        if not self._can_hit_click():
            return None
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
        if not self._can_hit_click():
            return None
        c, a, b, shape = self._current_click_local()
        dx, dy = pos.x()-c.x(), pos.y()-c.y()
        if shape == "rect":
            return abs(dx) <= a and abs(dy) <= b
        # ellipse
        return (dx*dx)/(a*a+1e-6) + (dy*dy)/(b*b+1e-6) <= 1.0

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

    # -------------------- 外部 API --------------------
    def setOrdinal(self, n: int):
        n = max(1, int(n))
        if self._ordinal != n:
            self._ordinal = n
            self.update()

    def setVis(self, show_click: bool, show_rect: bool):
        # 这里仍可扩展：若需要真正的“隐藏圆/矩形/文字”，可以加局部变量控制
        # 简化起见，先保持全部显示；如需开关，可仿照原有结构加 3 个布尔并在 paint 中判断
        changed = False
        if self._show_click  != bool(show_click):  self._show_click  = bool(show_click);  changed = True
        if self._show_rect   != bool(show_rect):   self._show_rect   = bool(show_rect);   changed = True
        if changed:
            # 关闭矩形时去掉矩形高亮；关闭圆时去掉圆高亮
            if not self._show_rect:   self._hl_rect = False
            if not self._show_click:  self._hl_click = False
            self._refresh_bounds_if_needed()
            self.update()

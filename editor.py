import json
import os
import requests
import shutil
import time
from dataclasses import dataclass, asdict
import math
from typing import Dict, List, Optional, Tuple
import cv2
import numpy as np

from PySide6 import QtCore, QtGui, QtWidgets
try:
    import shiboken6  # for isValid checks on Qt objects
except Exception:  # pragma: no cover
    shiboken6 = None

ENABLE_MOUSE : bool = True

CATEGORY_COLOR_MAP: Dict[str, QtGui.QColor] = {
    '情感': QtGui.QColor('#ff7f50'),
    '颜色': QtGui.QColor('#28a745'),
    '增强': QtGui.QColor('#6f42c1'),
    '置换': QtGui.QColor('#6c63ff'),
    '修改': QtGui.QColor('#ff9800'),
}

# Discrete hint-circle radius levels (in natural pixels)
RADIUS_LEVELS: List[int] = [53, 59, 65, 71, 76, 81, 85, 90, 95, 100, 105,110, 117, 124,129]
# Min rectangle size (natural pixels)
MIN_RECT_SIZE: float = 110


@dataclass
class Difference:
    id: str
    name: str
    section: str  # 'up' | 'down'
    category: str
    label: str
    enabled: bool
    visible: bool
    # rectangle stored in natural pixel coordinates
    x: float
    y: float
    width: float
    height: float
    # independent hint circles for up/down
    hint_level: int = 0
    cx: float = -1.0
    cy: float = -1.0


def now_id() -> str:
    return str(int(time.time() * 1000))


def rect_points(diff: Difference) -> List[QtCore.QPointF]:
    return [
        QtCore.QPointF(diff.x, diff.y),
        QtCore.QPointF(diff.x + diff.width, diff.y),
        QtCore.QPointF(diff.x + diff.width, diff.y + diff.height),
        QtCore.QPointF(diff.x, diff.y + diff.height),
    ]


def bounding_rect(points: List[QtCore.QPointF]) -> QtCore.QRectF:
    xs = [p.x() for p in points]
    ys = [p.y() for p in points]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    return QtCore.QRectF(min_x, min_y, max_x - min_x, max_y - min_y)


def overlap_ratio(a: QtCore.QRectF, b: QtCore.QRectF) -> float:
    inter = a.intersected(b)
    if inter.isNull():
        return 0.0
    inter_area = max(0.0, inter.width()) * max(0.0, inter.height())
    min_area = max(1.0, min(a.width() * a.height(), b.width() * b.height()))
    return inter_area / min_area

def _alpha_paste_no_resize_cv(src: np.ndarray, dst: np.ndarray, x: int, y: int) -> None:
    """
    src: BGR 或 BGRA(带alpha)；dst: BGR
    不缩放；若越界自动裁剪；在 (x, y) 处贴入。
    """
    if src is None or dst is None:
        return
    sh, sw = src.shape[:2]
    dh, dw = dst.shape[:2]

    # 计算dst上的有效区域
    dx1 = max(0, min(x, dw))
    dy1 = max(0, min(y, dh))
    dx2 = max(0, min(x + sw, dw))
    dy2 = max(0, min(y + sh, dh))
    if dx2 <= dx1 or dy2 <= dy1:
        return

    # 对应src的裁剪起点
    sx1 = dx1 - x
    sy1 = dy1 - y
    sx2 = sx1 + (dx2 - dx1)
    sy2 = sy1 + (dy2 - dy1)

    src_roi = src[sy1:sy2, sx1:sx2]
    dst_roi = dst[dy1:dy2, dx1:dx2]

    if src_roi.shape[2] == 4:
        # BGRA alpha 融合
        src_bgr = src_roi[..., :3].astype(np.float32)
        alpha = (src_roi[..., 3:4].astype(np.float32)) / 255.0  # (H,W,1)
        out = src_bgr * alpha + dst_roi.astype(np.float32) * (1.0 - alpha)
        dst[dy1:dy2, dx1:dx2] = out.astype(np.uint8)
    else:
        # 无alpha直接覆盖
        dst[dy1:dy2, dx1:dx2] = src_roi

def _to_bgr(img: np.ndarray, bg_bgr=(255, 255, 255)) -> np.ndarray:
    """把可能的 BGRA 转成 BGR，并在给定背景色上做 alpha 合成。"""
    if img is None:
        return None
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.shape[2] == 3:
        return img
    if img.shape[2] == 4:
        bgr = img[..., :3].astype(np.float32)
        a = (img[..., 3:4].astype(np.float32)) / 255.0
        bg = np.full_like(bgr, bg_bgr, dtype=np.float32)
        out = bgr * a + bg * (1.0 - a)
        return out.astype(np.uint8)
    return img

def compose_pin_layout(
    origin_bgr: np.ndarray,
    up_bgr: np.ndarray,
    down_bgr: np.ndarray,
    out_path: str,
    margin: int = 40,
    gap: int = 24,
    bg_bgr=(255, 255, 255),
) -> str:
    """
    生成“倒品”字形拼图（不缩放）：
      顶部：origin 居中
      底部：up、down 左右并排
    画布大小按内容自适应：宽 = max(origin_w, up_w + gap + down_w) + 2*margin
                         高 = margin + origin_h + gap + max(up_h, down_h) + margin
    """
    o = _to_bgr(origin_bgr, bg_bgr)
    u = _to_bgr(up_bgr, bg_bgr)
    d = _to_bgr(down_bgr, bg_bgr)

    oh, ow = o.shape[:2]
    uh, uw = u.shape[:2]
    dh, dw = d.shape[:2]

    inner_w = max(ow, uw + gap + dw)
    inner_h = oh + gap + max(uh, dh)

    canvas_w = inner_w + 2 * margin
    canvas_h = inner_h + 2 * margin

    canvas = np.full((canvas_h, canvas_w, 3), bg_bgr, dtype=np.uint8)


    # 顶部 up 和 down 左右并排
    top_y = margin
    up_x = margin
    canvas[top_y:top_y+uh, up_x:up_x+uw] = u

    down_x = margin + uw + gap
    canvas[top_y:top_y+dh, down_x:down_x+dw] = d

    # 底部 origin 居中
    origin_y = top_y + uh + gap
    origin_x = margin + (inner_w - ow) // 2
    canvas[origin_y:origin_y+oh, origin_x:origin_x+ow] = o

    cv2.imwrite(out_path, canvas)
    return out_path

class ImageEditRequester:
    def __init__(self, image_path: str, prompt: str):
        self.image_path = image_path
        self.prompt = prompt
        self.BASE_URL = "https://ai.t8star.cn/"
        # 建议在系统环境变量中设置 BANANA_API_KEY，避免把密钥写入代码库
        self.API_KEY = "sk-RX5FUdtuNTfQvr3LAOsDsL7OdkJZxf7DIhQ73Gfqj7yq50ZO"
        self.MODEL = "nano-banana"
        self.url = f"{self.BASE_URL}/v1/images/edits"
        self.headers = {
            'Authorization': f'Bearer {self.API_KEY}'
        }

    def send_request(self):
        import base64
        from PIL import Image

        # 记录原始图片尺寸
        with Image.open(self.image_path) as img:
            width, height = img.size

        files = [
            ('image', (self.image_path, open(self.image_path, 'rb'), 'image/png')),
        ]
        payload = {
            'model': self.MODEL,
            'prompt': self.prompt,
            'response_format': 'b64_json',
            'size': f"{width}x{height}",
        }
        response = requests.request("POST", self.url, headers=self.headers, data=payload, files=files)

        try:
            resp_json = response.json()
        except Exception:
            raise RuntimeError(f"AI返回非JSON: {response.text[:200]}")

        if 'data' not in resp_json or not resp_json['data']:
            raise RuntimeError("AI返回数据为空")

        data0 = resp_json['data'][0]
        b64img = data0.get('b64_json')
        out_path = self.image_path.replace('.png', '_result.png')
        if b64img:
            # 兼容 data url 前缀
            if b64img.startswith('data:image'):
                b64img = b64img.split(',', 1)[-1]
            b64img = ''.join(b64img.split())
            # 修复base64 padding
            missing_padding = len(b64img) % 4
            if missing_padding:
                b64img += '=' * (4 - missing_padding)
            img_bytes = base64.b64decode(b64img)
            with open(out_path, 'wb') as f:
                f.write(img_bytes)
        elif 'url' in data0:
            img_url = data0['url']
            img_resp = requests.get(img_url)
            img_resp.raise_for_status()
            with open(out_path, 'wb') as f:
                f.write(img_resp.content)
        else:
            raise RuntimeError("AI未返回b64或url")

        # 保证输出尺寸一致
        with Image.open(out_path) as out_img:
            if out_img.size != (width, height):
                out_img = out_img.resize((width, height), Image.LANCZOS)
                out_img.save(out_path)

class HandleItem(QtWidgets.QGraphicsEllipseItem):
    def __init__(self, size: float = 12.0, owner: Optional['DifferenceRectItem'] = None, index: int = 0):
        super().__init__(-size / 2, -size / 2, size, size)
        self.owner = owner
        self.index = index
        # solid red dot
        self.setBrush(QtGui.QBrush(QtGui.QColor('#d32f2f')))
        self.setPen(QtGui.QPen(QtCore.Qt.NoPen))
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, True)
        self.setAcceptedMouseButtons(QtCore.Qt.LeftButton)
        # directional cursors by corner
        if index in (0, 2):
            self.setCursor(QtCore.Qt.SizeFDiagCursor)
        else:
            self.setCursor(QtCore.Qt.SizeBDiagCursor)
        self.setZValue(10)

    def mouseMoveEvent(self, event: 'QtWidgets.QGraphicsSceneMouseEvent') -> None:
        global ENABLE_MOUSE
        if not ENABLE_MOUSE :
            event.accept()
            return
        
        super().mouseMoveEvent(event)
        if self.owner is not None:
            try:
                # use scene position mapped to parent's local to avoid drift
                p_local = self.owner.mapFromScene(event.scenePos())
                self.owner.on_handle_moved(self.index, p_local)
            except Exception:
                pass

    def mousePressEvent(self, event: 'QtWidgets.QGraphicsSceneMouseEvent') -> None:
        global ENABLE_MOUSE
        if not ENABLE_MOUSE :
            event.accept()
            return
        # For handle drag,不要更改选择状态，避免触发选中后的移动/缩放逻辑
        super().mousePressEvent(event)


class CircleItem(QtWidgets.QGraphicsEllipseItem):
    def __init__(self, owner: 'DifferenceRectItem'):
        super().__init__(0, 0, 10, 10)
        self.owner = owner
        # do not grab mouse press; parent处理拖动
        self.setAcceptedMouseButtons(QtCore.Qt.NoButton)
        # hover for highlight
        self.setAcceptHoverEvents(True)

    def hoverEnterEvent(self, event: 'QtWidgets.QGraphicsSceneHoverEvent') -> None:
        global ENABLE_MOUSE
        if not ENABLE_MOUSE :
            event.accept()
            return
        # 禁用圆的 hover 高亮，仅设置光标
        self.setCursor(QtCore.Qt.OpenHandCursor)
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event: 'QtWidgets.QGraphicsSceneHoverEvent') -> None:
        global ENABLE_MOUSE
        if not ENABLE_MOUSE :
            event.accept()
            return
        # 禁用圆的 hover 高亮恢复
        super().hoverLeaveEvent(event)

    def hoverMoveEvent(self, event: 'QtWidgets.QGraphicsSceneHoverEvent') -> None:
        global ENABLE_MOUSE
        if not ENABLE_MOUSE :
            event.accept()
            return
        # 在圆内移动，仅保持手形光标，不做其他处理
        self.setCursor(QtCore.Qt.OpenHandCursor)
        super().hoverMoveEvent(event)

    def set_temp_highlight(self, enabled: bool) -> None:
        # Reference-counted highlight so list-hover与鼠标悬停可叠加
        if enabled:
            if self._hl_refcount == 0:
                self._hl_prev_pen = QtGui.QPen(self.pen())
                self._hl_prev_brush = QtGui.QBrush(self.brush())
                pen = QtGui.QPen(QtGui.QColor('#ff1744'))
                pen.setWidth(5)
                self.setPen(pen)
                # 置为1，避免 hoverMove 反复叠加
                self._hl_refcount = 1
            else:
                if self._hl_refcount > 0:
                    # 直接清零，确保一次取消即可恢复
                    self._hl_refcount = 0
                    if self._hl_prev_pen is not None:
                        self.setPen(self._hl_prev_pen)
                    if self._hl_prev_brush is not None:
                        self.setBrush(self._hl_prev_brush)


class DifferenceRectItem(QtWidgets.QGraphicsRectItem):
    def __init__(self, diff: Difference, color: QtGui.QColor, on_change=None, is_up: bool = True):
        # keep local rect anchored at (0,0), use item position for top-left
        super().__init__(QtCore.QRectF(0, 0, diff.width, diff.height))
        self.diff = diff
        self.color = color
        self.is_up = is_up
        self._adjustingPosition = False
        self._initializing = True
        self._on_change_cb = on_change
        # rectangle: red stroke, semi-transparent fill
        self.setBrush(QtGui.QBrush(QtGui.QColor(255, 0, 0, 40)))
        pen = QtGui.QPen(QtGui.QColor('#c62828'))
        pen.setWidth(2)
        self.setPen(pen)
        # Allow default move; we'll clamp in itemChange
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
        # Disable Qt selection state to decouple"选中"与拖动/拉伸
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, False)
        self.setFlag(QtWidgets.QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(1)
        self.setAcceptedMouseButtons(QtCore.Qt.LeftButton)
        self.setAcceptHoverEvents(True)
        # drag state
        self._dragging = False
        self._drag_mode = 'none'  # 'move' or 'resize' or 'circle'
        self._resize_index = -1   # 0 tl,1 tr,2 br,3 bl
        self._move_start_pos_scene = QtCore.QPointF(0, 0)
        self._item_start_pos = QtCore.QPointF(0, 0)
        self._anchor_scene = QtCore.QPointF(0, 0)
        self._suppress_sync = False
        self._edge_index: str = ''  # 'L','R','T','B' 
        self._edge_thresh: float = 8.0
        # handles at corners: tl, tr, br, bl
        self.handles = [HandleItem(9.0, owner=self, index=i) for i in range(4)]
        for h in self.handles:
            h.setParentItem(self)

        # hint circle (inscribed)
        self.circle_item = CircleItem(owner=self)
        self.circle_item.setParentItem(self)
        self.circle_item.setBrush(QtGui.QBrush(QtCore.Qt.transparent))
        circle_pen = QtGui.QPen(QtGui.QColor('#00c853'))
        circle_pen.setWidth(3)
        self.circle_item.setPen(circle_pen)
        # ensure circle draws above rectangle fill
        self.circle_item.setZValue(3)
        # text drawn inside circle, colored by category
        self.circle_text = QtWidgets.QGraphicsTextItem("", self)
        self.circle_text.setZValue(5)
        self.circle_text.setFlag(QtWidgets.QGraphicsItem.ItemIgnoresTransformations, False)
        self.circle_text.setAcceptHoverEvents(False)
        self.circle_text.setAcceptedMouseButtons(QtCore.Qt.NoButton)
        self.update_handles()

        # set initial position to top-left (after handles exist to avoid early callbacks)
        self.setPos(self.diff.x, self.diff.y)
        self._initializing = False

        self._hl_refcount: int = 0
        self._hl_prev_pen: Optional[QtGui.QPen] = None
        self._hl_prev_brush: Optional[QtGui.QBrush] = None
        self._hl_prev_circle_pen: Optional[QtGui.QPen] = None
        self._hl_prev_opacity: float = self.opacity()
        self._hl_prev_z: float = self.zValue()

    def _layout_circle_text(self, cx: float, cy: float, radius: float) -> None:
        # 使用 QGraphicsTextItem 支持自动换行；字号自适应到最大；必要时省略号
        cr = self.circle_item.rect()
        cx, cy = cr.center().x(), cr.center().y()
        radius = cr.width() / 2.0
        text = (self.diff.label or "").strip()
        if not text:
            self.circle_text.setVisible(False)
            return
        max_w = radius * 2.0 * 0.92
        max_h = radius * 2.0 * 0.9
        self.circle_text.setTextWidth(max_w)
        font = QtGui.QFont(self.circle_text.font())
        min_pt = 10
        max_pt = int(radius * 0.9) if radius > 12 else 14
        if max_pt < min_pt:
            max_pt = min_pt
        best_pt = min_pt
        # 递增找到能放下的最大字号
        for pt in range(min_pt, max_pt + 1):
            font.setPointSize(pt)
            self.circle_text.setFont(font)
            self.circle_text.setPlainText(text)
            br = self.circle_text.boundingRect()
            if br.height() <= max_h:
                best_pt = pt
            else:
                break
        font.setPointSize(best_pt)
        self.circle_text.setFont(font)
        self.circle_text.setPlainText(text)
        br = self.circle_text.boundingRect()
        # 若最小字号仍超高，则截断并加省略号
        if br.height() > max_h:
            s = text
            while len(s) > 1:
                s = s[:-1]
                self.circle_text.setPlainText(s + "…")
                br = self.circle_text.boundingRect()
                if br.height() <= max_h:
                    break
        # 居中并作细微上移矫正（基线偏差）
        dy = -0.05 * br.height()
        self.circle_text.setPos(cx - br.width() / 2.0, cy - br.height() / 2.0 + dy)
        color_cat = CATEGORY_COLOR_MAP.get(self.diff.category, QtGui.QColor('#ff0000'))
        self.circle_text.setDefaultTextColor(color_cat)

    def set_label_visible(self, visible: bool) -> None:
        isup = self.diff.section == 'up'
        vis = isup == self.is_up
        self.circle_text.setVisible(visible and vis)

    def update_label(self) -> None:
        cr = self.circle_item.rect()
        cx, cy = cr.center().x(), cr.center().y()
        radius = cr.width() / 2.0
        self._layout_circle_text(cx, cy, radius)

    def update_handles(self) -> None:
        r = self.rect()
        # handles may not be ready during very early lifecycle
        if not hasattr(self, 'handles') or not self.handles:
            return
        self.handles[0].setPos(r.topLeft())
        self.handles[1].setPos(r.topRight())
        self.handles[2].setPos(r.bottomRight())
        self.handles[3].setPos(r.bottomLeft())

        # update hint circle using discrete radius levels (auto), place center (draggable)
        size = min(max(1.0, r.width()), max(1.0, r.height()))
        max_half = size / 2.0
        # auto: pick largest discrete radius that fits; fallback to max_half when too small
        allowed = [lvl for lvl in RADIUS_LEVELS if lvl <= max_half-10]
        is_up = True  # this item draws both views; choose fields based on section when used
        # choose state fields per section by checking owner context via label (section not stored on item)
        # Infer by default using diff.section for label text placement
        if allowed:
            radius = float(allowed[-1])
            self.diff.hint_level = len(allowed)
        else:
            radius = max_half
            self.diff.hint_level = 1

        # center: use stored local center if any, else rect center; clamp into rect with current radius
        cx = self.diff.cx if self.diff.cx >= 0 else r.width() / 2.0
        cy = self.diff.cy if self.diff.cy >= 0 else r.height() / 2.0
        cx = max(radius, min(cx, r.width() - radius))
        cy = max(radius, min(cy, r.height() - radius))
        self.diff.cx = cx
        self.diff.cy = cy
        self.circle_item.setRect(cx - radius, cy - radius, radius * 2.0, radius * 2.0)
        # update label
        self._layout_circle_text(cx, cy, radius)

    def mousePressEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        global ENABLE_MOUSE
        if not ENABLE_MOUSE :
            event.accept()
            return
        pos = event.pos()
        r = self.rect()
        corners = [r.topLeft(), r.topRight(), r.bottomRight(), r.bottomLeft()]
        thresh = 14.0
        clicked_corner = -1
        for idx, c in enumerate(corners):
            if QtCore.QLineF(pos, c).length() <= thresh:
                clicked_corner = idx
                break

        # 如果启用了可选中，才执行"先选中后操作"的逻辑；当前已禁用选择，此分支不会触发
        if (self.flags() & QtWidgets.QGraphicsItem.ItemIsSelectable) and event.button() == QtCore.Qt.LeftButton and self.scene() is not None and not self.isSelected():
            self.scene().clearSelection()
            self.setSelected(True)
            event.accept()
            return

        # circle drag detection first（convert pos to circle's local coords）
        p_in_circle = self.circle_item.mapFromItem(self, pos)
        if self.circle_item.shape().contains(p_in_circle):
            self._dragging = True
            self._drag_mode = 'circle'
            self._suppress_sync = True
            # avoid rectangle moving while dragging circle
            self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, False)
            event.accept()
            return

        if clicked_corner >= 0:
            self._dragging = True
            self._drag_mode = 'resize'
            self._resize_index = clicked_corner
            opp = corners[(clicked_corner + 2) % 4]
            self._anchor_scene = self.mapToScene(opp)
            self._suppress_sync = True
            # prevent default item move while resizing via handle
            self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, False)
            event.accept()
            return

        # edge drag detection (resize one side)
        edge_thresh = self._edge_thresh
        left_hit = 0 <= pos.y() <= r.height() and abs(pos.x() - 0.0) <= edge_thresh
        right_hit = 0 <= pos.y() <= r.height() and abs(pos.x() - r.width()) <= edge_thresh
        top_hit = 0 <= pos.x() <= r.width() and abs(pos.y() - 0.0) <= edge_thresh
        bottom_hit = 0 <= pos.x() <= r.width() and abs(pos.y() - r.height()) <= edge_thresh
        if left_hit or right_hit or top_hit or bottom_hit:
            self._dragging = True
            self._drag_mode = 'edge'
            self._edge_index = 'L' if left_hit else ('R' if right_hit else ('T' if top_hit else 'B'))
            self._suppress_sync = True
            self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, False)
            event.accept()
            return

        # default move handled by QGraphicsView; don't intercept to keep it smooth
        self._dragging = False
        self._drag_mode = 'none'
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        global ENABLE_MOUSE
        if not ENABLE_MOUSE :
            event.accept()
            return

        if not self._dragging:
            return super().mouseMoveEvent(event)
        scene_rect = self.scene().sceneRect() if self.scene() else QtCore.QRectF(0, 0, 1e6, 1e6)
        # Default move path is handled by base class; only handle resize/circle below
        if self._drag_mode == 'resize':
            p_scene = event.scenePos()
            p_scene.setX(max(scene_rect.left(), min(p_scene.x(), scene_rect.right())))
            p_scene.setY(max(scene_rect.top(), min(p_scene.y(), scene_rect.bottom())))
            tl = QtCore.QPointF(min(self._anchor_scene.x(), p_scene.x()),
                                min(self._anchor_scene.y(), p_scene.y()))
            br = QtCore.QPointF(max(self._anchor_scene.x(), p_scene.x()),
                                max(self._anchor_scene.y(), p_scene.y()))
            new_w = max(MIN_RECT_SIZE, br.x() - tl.x())
            new_h = max(MIN_RECT_SIZE, br.y() - tl.y())
            self.setPos(tl)
            self.setRect(QtCore.QRectF(0, 0, new_w, new_h))
            self._update_diff_from_item()
            self.update_handles()
            event.accept()
            return

        if self._drag_mode == 'edge':
            # resize only one side based on which edge is grabbed
            r = QtCore.QRectF(self.rect())
            p_local = event.pos()
            new_r = QtCore.QRectF(r)
            if self._edge_index == 'L':
                new_r.setLeft(p_local.x())
            elif self._edge_index == 'R':
                new_r.setRight(p_local.x())
            elif self._edge_index == 'T':
                new_r.setTop(p_local.y())
            elif self._edge_index == 'B':
                new_r.setBottom(p_local.y())

            new_left = min(new_r.left(), new_r.right())
            new_top = min(new_r.top(), new_r.bottom())
            new_w = max(MIN_RECT_SIZE, abs(new_r.width()))
            new_h = max(MIN_RECT_SIZE, abs(new_r.height()))
            scene_rect = self.scene().sceneRect() if self.scene() else QtCore.QRectF(-1e6, -1e6, 2e6, 2e6)
            proposed_pos = self.pos() + QtCore.QPointF(new_left, new_top)
            clamped_x = max(scene_rect.left(), min(proposed_pos.x(), scene_rect.right()))
            clamped_y = max(scene_rect.top(), min(proposed_pos.y(), scene_rect.bottom()))
            max_w = max(MIN_RECT_SIZE, scene_rect.right() - clamped_x)
            max_h = max(MIN_RECT_SIZE, scene_rect.bottom() - clamped_y)
            new_w = min(new_w, max_w)
            new_h = min(new_h, max_h)
            self.setPos(QtCore.QPointF(clamped_x, clamped_y))
            self.setRect(QtCore.QRectF(0, 0, new_w, new_h))
            self._update_diff_from_item()
            self.update_handles()
            event.accept()
            return

        if self._drag_mode == 'circle':
            r = self.rect()
            cr = self.circle_item.rect()
            radius = cr.width() / 2.0
            local = event.pos()
            cx = max(radius, min(local.x(), r.width() - radius))
            cy = max(radius, min(local.y(), r.height() - radius))
            self.diff.cx = cx
            self.diff.cy = cy
            self.circle_item.setRect(cx - radius, cy - radius, radius * 2.0, radius * 2.0)
            # move label with circle
            self._layout_circle_text(cx, cy, radius)
            # 仅更新本项的标签布局，不立即全局同步
            event.accept()
            return

    def mouseReleaseEvent(self, event: QtWidgets.QGraphicsSceneMouseEvent) -> None:
        global ENABLE_MOUSE
        if not ENABLE_MOUSE :
            event.accept()
            return
        self._dragging = False
        self._drag_mode = 'none'
        self._resize_index = -1
        # after finishing resize, sync once
        if self._suppress_sync:
            self._suppress_sync = False
            # 在释放时同步一次（另一侧矩形、列表、AI overlay）
            self._notify_change()
        # restore default movable flag
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, True)
        return super().mouseReleaseEvent(event)

    def hoverMoveEvent(self, event: 'QtWidgets.QGraphicsSceneHoverEvent') -> None:
        global ENABLE_MOUSE
        if not ENABLE_MOUSE :
            event.accept()
            return
        # set cursors only when矩形区域可见；否则使用默认箭头，不改变笔刷（避免出现外框）
        r = self.rect()
        pos = event.pos()
        edge_thresh = self._edge_thresh
        # 当区域隐藏时，避免高亮与光标提示
        if not self.acceptHoverEvents() or self.pen().style() == QtCore.Qt.NoPen:
            self.unsetCursor()
            return super().hoverMoveEvent(event)
        # 禁用矩形 hover 高亮
        # corners
        near_tl = QtCore.QLineF(pos, r.topLeft()).length() <= 12
        near_tr = QtCore.QLineF(pos, r.topRight()).length() <= 12
        near_br = QtCore.QLineF(pos, r.bottomRight()).length() <= 12
        near_bl = QtCore.QLineF(pos, r.bottomLeft()).length() <= 12
        if near_tl or near_br:
            self.setCursor(QtCore.Qt.SizeFDiagCursor)
        elif near_tr or near_bl:
            self.setCursor(QtCore.Qt.SizeBDiagCursor)
        elif abs(pos.x() - 0.0) <= edge_thresh or abs(pos.x() - r.width()) <= edge_thresh:
            self.setCursor(QtCore.Qt.SizeHorCursor)
        elif abs(pos.y() - 0.0) <= edge_thresh or abs(pos.y() - r.height()) <= edge_thresh:
            self.setCursor(QtCore.Qt.SizeVerCursor)
        else:
            self.setCursor(QtCore.Qt.OpenHandCursor)
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event: 'QtWidgets.QGraphicsSceneHoverEvent') -> None:
        global ENABLE_MOUSE
        if not ENABLE_MOUSE :
            event.accept()
            return
        # 仅当当前笔可见时才恢复细边框；否则保持 NoPen
        if self.pen().style() != QtCore.Qt.NoPen:
            self._hover_in_circle = False
            self.set_temp_highlight(False)
        self.unsetCursor()
        super().hoverLeaveEvent(event)

    def itemChange(self, change: 'QtWidgets.QGraphicsItem.GraphicsItemChange', value):
        # Clamp movement within scene rect and sync geometry after movement
        if change == QtWidgets.QGraphicsItem.ItemPositionChange and self.scene() is not None:
            new_pos: QtCore.QPointF = value
            r = self.rect()
            scene_rect = self.scene().sceneRect()
            new_x = max(scene_rect.left(), min(new_pos.x(), scene_rect.right() - r.width()))
            new_y = max(scene_rect.top(), min(new_pos.y(), scene_rect.bottom() - r.height()))
            return QtCore.QPointF(new_x, new_y)
        if self._initializing:
            return super().itemChange(change, value)
        if change == QtWidgets.QGraphicsItem.ItemPositionHasChanged and not self._adjustingPosition:
            self._update_diff_from_item()
            self.update_handles()
            if not getattr(self, '_suppress_sync', False):
                self._notify_change()
        return super().itemChange(change, value)

    def _notify_change(self) -> None:
        if callable(self._on_change_cb):
            try:
                self._on_change_cb(self.diff.id)
            except Exception:
                pass

    def _update_diff_from_item(self) -> None:
        # absolute top-left is item position + local rect top-left (kept at 0,0)
        r = self.rect()
        p = self.pos()
        self.diff.x = p.x() + r.x()
        self.diff.y = p.y() + r.y()
        self.diff.width = r.width()
        self.diff.height = r.height()

    def on_handle_moved(self, idx: int, p: QtCore.QPointF) -> None:
        global ENABLE_MOUSE
        if not ENABLE_MOUSE :
            return
        # allow dragging outward: do not clamp p; normalize later
        r = QtCore.QRectF(self.rect())
        if idx == 0:  # tl
            r.setTopLeft(p)
        elif idx == 1:  # tr
            r.setTopRight(p)
        elif idx == 2:  # br
            r.setBottomRight(p)
        elif idx == 3:  # bl
            r.setBottomLeft(p)
        # normalize
        new_left = min(r.left(), r.right())
        new_top = min(r.top(), r.bottom())
        new_w = max(MIN_RECT_SIZE, abs(r.width()))
        new_h = max(MIN_RECT_SIZE, abs(r.height()))
        # clamp against scene bounds to avoid huge jumps when pulling outward
        scene_rect = self.scene().sceneRect() if self.scene() else QtCore.QRectF(-1e6, -1e6, 2e6, 2e6)
        proposed_pos = self.pos() + QtCore.QPointF(new_left, new_top)
        clamped_x = max(scene_rect.left(), min(proposed_pos.x(), scene_rect.right()))
        clamped_y = max(scene_rect.top(), min(proposed_pos.y(), scene_rect.bottom()))
        max_w = max(MIN_RECT_SIZE, scene_rect.right() - clamped_x)
        max_h = max(MIN_RECT_SIZE, scene_rect.bottom() - clamped_y)
        new_w = min(new_w, max_w)
        new_h = min(new_h, max_h)
        # apply
        self.setPos(QtCore.QPointF(clamped_x, clamped_y))
        self.setRect(QtCore.QRectF(0, 0, new_w, new_h))
        self._update_diff_from_item()
        self.update_handles()
        # notify so另一侧矩形与UI同步
        self._notify_change()

    def set_temp_highlight(self, enabled: bool) -> None:
        # Reference-counted highlight so list-hover与鼠标悬停可叠加
        if enabled:
            if self._hl_refcount == 0:
                self._hl_prev_pen = QtGui.QPen(self.pen())
                self._hl_prev_brush = QtGui.QBrush(self.brush())
                pen = QtGui.QPen(QtGui.QColor('#ff1744'))
                pen.setWidth(5)
                self.setPen(pen)
                # 置为1，避免 hoverMove 反复叠加
                self._hl_refcount = 1
            else:
                if self._hl_refcount > 0:
                    # 直接清零，确保一次取消即可恢复
                    self._hl_refcount = 0
                    if self._hl_prev_pen is not None:
                        self.setPen(self._hl_prev_pen)
                    if self._hl_prev_brush is not None:
                        self.setBrush(self._hl_prev_brush)

    def hoverEnterEvent(self, event: 'QtWidgets.QGraphicsSceneHoverEvent') -> None:
        # 禁用进入时的矩形高亮，仅设置光标
        global ENABLE_MOUSE
        if not ENABLE_MOUSE :
            event.accept()
            return
        if self.pen().style() != QtCore.Qt.NoPen:
            self.setCursor(QtCore.Qt.OpenHandCursor)
        super().hoverEnterEvent(event)


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


class DifferenceEditorWindow(QtWidgets.QMainWindow):
    def _set_completed_ui_disabled(self, disabled: bool):
        # 禁用保存、AI处理按钮
        global ENABLE_MOUSE
        self.btn_save.setEnabled(not disabled)
        self.btn_submit.setEnabled(not disabled)
        self.up_side.setEnabled(not disabled)
        self.down_side.setEnabled(not disabled)
        ENABLE_MOUSE = not disabled

    def __init__(self, pair, config_dir: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.pair = pair
        self.config_dir = config_dir
        self.setWindowTitle(f"不同点编辑器 - {self.pair.name}")
        self.resize(1600, 1080)
        self._add_btns = list()

        # load images
        up_pix = QtGui.QPixmap(self.pair.up_image_path)
        down_pix = QtGui.QPixmap(self.pair.down_image_path)
        if up_pix.isNull() or down_pix.isNull():
            QtWidgets.QMessageBox.critical(self, "加载失败", "无法加载图片")
            self.close()
            return

        # UI layout
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        vbox_root = QtWidgets.QVBoxLayout(root)
        # Use uniform small margins so the bottom toolbar reaches the window bottom
        vbox_root.setContentsMargins(8, 8, 8, 8)
        vbox_root.setSpacing(8)

        # Two sections (up/down)
        self.up_scene = ImageScene(up_pix)
        self.down_scene = ImageScene(down_pix)
        self.up_view = ImageView(self.up_scene)
        self.down_view = ImageView(self.down_scene)

        self.toggle_regions = QtWidgets.QCheckBox("显示点击区域")
        self.toggle_regions.setChecked(True)
        self.toggle_hints = QtWidgets.QCheckBox("显示绿圈")
        self.toggle_hints.setChecked(True)
        self.toggle_labels = QtWidgets.QCheckBox("显示茬点文本")
        self.toggle_labels.setChecked(True)
        self.toggle_ai_preview = QtWidgets.QCheckBox("AI预览")
        self.toggle_ai_preview.setChecked(False)
        self.toggle_ai_preview.setEnabled(False)

        # Up row
        up_row = QtWidgets.QHBoxLayout()
        up_left = QtWidgets.QVBoxLayout()
        up_left.addWidget(self.up_view, 1)
        up_row.addLayout(up_left, 1)
        self.up_side = self._build_side_panel(section='up')
        up_row.addWidget(self.up_side, 0)
        vbox_root.addLayout(up_row, 1)

        # Down row
        down_row = QtWidgets.QHBoxLayout()
        down_left = QtWidgets.QVBoxLayout()
        down_left.addWidget(self.down_view, 1)
        down_row.addLayout(down_left, 1)
        self.down_side = self._build_side_panel(section='down')
        down_row.addWidget(self.down_side, 0)
        vbox_root.addLayout(down_row, 1)

        # Bottom toolbar
        bottom = QtWidgets.QWidget()
        # Remove the top border line for a cleaner look
        bottom.setStyleSheet("QWidget#bottomWidget { background: #fff; }")
        # 需要给bottom widget设置objectName
        bottom.setObjectName("bottomWidget")
        bottom.setMinimumHeight(56)
        bottom_layout = QtWidgets.QHBoxLayout(bottom)
        bottom_layout.setContentsMargins(16, 10, 16, 10)
        bottom_layout.setSpacing(12)
        self.total_count = QtWidgets.QLabel("茬点总计：0")
        self.btn_save = QtWidgets.QPushButton("保存")
        self.btn_submit = QtWidgets.QPushButton("AI处理")
        self.btn_close = QtWidgets.QPushButton("关闭")
        bottom_layout.addWidget(self.total_count)
        bottom_layout.addWidget(self.btn_save)
        bottom_layout.addWidget(self.btn_submit)
        bottom_layout.addWidget(self.btn_close)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self.toggle_regions)
        bottom_layout.addWidget(self.toggle_hints)
        bottom_layout.addWidget(self.toggle_labels)
        bottom_layout.addWidget(self.toggle_ai_preview)
        vbox_root.addWidget(bottom, 0)

        # Ensure vertical centering of buttons and controls
        for w in [self.total_count, self.btn_save, self.btn_submit, self.btn_close,
                  self.toggle_regions, self.toggle_hints, self.toggle_labels, self.toggle_ai_preview]:
            bottom_layout.setAlignment(w, QtCore.Qt.AlignVCenter)

        # Status bar at the very bottom to reflect meta.json status
        self.status_bar = QtWidgets.QStatusBar(self)
        self.status_bar.setSizeGripEnabled(False)
        self.setStatusBar(self.status_bar)
        # AI progress widgets on status bar
        self.ai_spinner = QtWidgets.QLabel()
        self.ai_spinner.setStyleSheet("color:#666; padding-right:6px;")
        self.ai_spinner.setVisible(False)
        self.ai_progress = QtWidgets.QProgressBar()
        self.ai_progress.setFixedWidth(240)
        self.ai_progress.setTextVisible(False)
        self.ai_progress.setVisible(False)
        self.status_bar.addPermanentWidget(self.ai_spinner)
        self.status_bar.addPermanentWidget(self.ai_progress)
        # default: use dialog-style progress (user preferred)
        self.progress_use_dialog: bool = True

        # data
        self.differences: List[Difference] = []
        self.rect_items_up: Dict[str, DifferenceRectItem] = {}
        self.rect_items_down: Dict[str, DifferenceRectItem] = {}
        # AI 预览覆盖图层（仅当勾选 AI预览 时显示）
        self.ai_overlays_up: Dict[str, QtWidgets.QGraphicsPixmapItem] = {}
        self.ai_overlays_down: Dict[str, QtWidgets.QGraphicsPixmapItem] = {}
        self._syncing_rect_update: bool = False
        self._syncing_selection: bool = False
        self._suppress_scene_selection: bool = False
        self.meta_status: str = 'unsaved'
        # dirty state for title asterisk
        self._is_dirty: bool = False
        # 默认延迟磁盘写入，仅在显式保存或AI阶段落盘
        self._defer_disk_writes: bool = True

        # wire
        self.btn_save.clicked.connect(self.on_save_clicked)
        self.btn_submit.clicked.connect(self.on_ai_process)
        self.btn_close.clicked.connect(self.close)
        self.toggle_regions.toggled.connect(self.refresh_visibility)
        self.toggle_hints.toggled.connect(self.refresh_visibility)
        self.toggle_labels.toggled.connect(self.refresh_visibility)
        self.toggle_ai_preview.toggled.connect(self.on_toggle_ai_preview)

        # style buttons
        self.btn_save.setStyleSheet("QPushButton{background:#0d6efd;color:#fff;padding:6px 14px;border-radius:6px;border:1px solid #0d6efd;} QPushButton:hover{background:#0b5ed7;border-color:#0b5ed7;}")
        self.btn_submit.setStyleSheet("QPushButton{background:#28a745;color:#fff;padding:6px 14px;border-radius:6px;border:1px solid #28a745;} QPushButton:hover{background:#1e7e34;border-color:#1e7e34;}")
        self.btn_close.setStyleSheet("QPushButton{background:#6c757d;color:#fff;padding:6px 14px;border-radius:6px;border:1px solid #6c757d;} QPushButton:hover{background:#545b62;border-color:#545b62;}")
        self.total_count.setStyleSheet("color:#333;font-weight:500;")

        # selection syncing between scenes and list
        # Use bound methods (QObject slots) so they auto-disconnect when window is destroyed
        self.up_scene.selectionChanged.connect(self._on_up_selection_changed)
        self.down_scene.selectionChanged.connect(self._on_down_selection_changed)

        # initialize scenes/view
        QtCore.QTimer.singleShot(0, lambda: self.up_view.fitInView(self.up_scene.sceneRect(), QtCore.Qt.KeepAspectRatio))
        QtCore.QTimer.singleShot(0, lambda: self.down_view.fitInView(self.down_scene.sceneRect(), QtCore.Qt.KeepAspectRatio))
        # viewport hover tracking to取消高亮
        for view, scene in ((self.up_view, self.up_scene), (self.down_view, self.down_scene)):
            view.setMouseTracking(True)
            view.viewport().setMouseTracking(True)
            def make_leave(v):
                def _leave(ev):
                    self._apply_list_hover_highlight(-1)
                return _leave
            view.viewport().leaveEvent = make_leave(view)

        # load existing config if exists
        self.load_existing_config()

        # initial count
        self.update_total_count()
        # initial status bar display
        self._update_status_bar()
        self._update_window_title()
        # 若初始状态为完成，禁用交互
        # if getattr(self, 'meta_status', None) == 'completed':
        #     self._set_completed_ui_disabled(True)

        # Shortcut: Cmd/Ctrl+S 保存
        self._sc_save = QtGui.QShortcut(QtGui.QKeySequence.Save, self)
        self._sc_save.activated.connect(self.on_save_clicked)

    def _update_window_title(self) -> None:
        mark = "*" if getattr(self, '_is_dirty', False) else ""
        self.setWindowTitle(f"不同点编辑器 - {self.pair.name}{mark}")

    def _update_status_bar(self, status: Optional[str] = None) -> None:
        if status is not None:
            self.meta_status = status
        text_map = {
            'unsaved': '未保存',
            'saved': '待AI处理',
            'aiPending': 'AI处理中',
            'completed': '完成',
            'hasError': 'AI处理存在错误',
        }
        human = text_map.get(getattr(self, 'meta_status', 'unsaved'), self.meta_status)
        if hasattr(self, 'status_bar') and self.status_bar is not None:
            self.status_bar.showMessage(f"状态：{human}")

        #关闭ai预览和关闭勾选框
        if self.meta_status != 'completed':
            self.toggle_ai_preview.setChecked(False)
            self._remove_ai_overlays()
            self.toggle_ai_preview.setEnabled(False)
        else:
            self.toggle_ai_preview.setEnabled(True)

        # 状态为完成时禁用相关交互，否则恢复
        # self._set_completed_ui_disabled(self.meta_status == 'completed')

    # --- AI status bar progress helpers ---
    def _ai_progress_start(self, total: int) -> None:
        frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self._ai_frame_idx = 0
        # cleanup any existing timer first
        try:
            old = getattr(self, '_ai_timer', None)
            if old is not None:
                old.stop()
                old.deleteLater()
        except Exception:
            pass
        self._ai_timer = QtCore.QTimer(self)
        if self.progress_use_dialog:
            # Use a modal-looking progress dialog (non-blocking updates via signals)
            self._ai_dlg = QtWidgets.QProgressDialog("正在上传AI处理...", None, 0, max(1, int(total)), self)
            self._ai_dlg.setWindowModality(QtCore.Qt.ApplicationModal)
            self._ai_dlg.setMinimumDuration(0)
            self._ai_dlg.setAutoClose(False)
            self._ai_dlg.setAutoReset(False)
            self._ai_dlg.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
            self._ai_dlg.setValue(0)
            self._ai_dlg.show()
            def tick():
                self._ai_frame_idx += 1
                self._ai_dlg.setLabelText(f"{frames[self._ai_frame_idx % len(frames)]} 正在上传AI处理... {self._ai_dlg.value()}/{self._ai_dlg.maximum()} ")
            self._ai_timer.timeout.connect(tick)
            self._ai_timer.start(120)
            # hide status bar widgets during dialog mode
            self.ai_spinner.setVisible(False)
            self.ai_progress.setVisible(False)
        else:
            # status bar widgets
            self.ai_progress.setMaximum(max(1, int(total)))
            self.ai_progress.setValue(0)
            self.ai_progress.setVisible(True)
            self.ai_spinner.setVisible(True)
            def tick():
                self._ai_frame_idx += 1
                self.ai_spinner.setText(frames[self._ai_frame_idx % len(frames)])
            self._ai_timer.timeout.connect(tick)
            self._ai_timer.start(120)

    def _ai_progress_end(self) -> None:
        try:
            if getattr(self, '_ai_timer', None) is not None:
                self._ai_timer.stop()
                self._ai_timer.deleteLater()
        except Exception:
            pass
        # close dialog if used
        if self.progress_use_dialog and getattr(self, '_ai_dlg', None) is not None:
            try:
                self._ai_dlg.close()
            except Exception:
                pass
            self._ai_dlg = None
        # hide status bar widgets
        self.ai_spinner.clear()
        self.ai_spinner.setVisible(False)
        self.ai_progress.setVisible(False)

    def _cleanup_ai_thread(self) -> None:
        """Safely stop and delete the worker thread objects from the GUI thread."""
        try:
            if getattr(self, '_ai_thread', None) is not None:
                try:
                    if self._ai_thread.isRunning():
                        self._ai_thread.quit()
                        self._ai_thread.wait()
                except Exception:
                    pass
        finally:
            try:
                if getattr(self, '_ai_worker', None) is not None:
                    self._ai_worker.deleteLater()
            except Exception:
                pass
            try:
                if getattr(self, '_ai_thread', None) is not None:
                    self._ai_thread.deleteLater()
            except Exception:
                pass
            self._ai_worker = None
            self._ai_thread = None

    @QtCore.Slot(int, int)
    def _ai_slot_progress(self, step: int, total: int) -> None:
        if self.progress_use_dialog and getattr(self, '_ai_dlg', None) is not None:
            self._ai_dlg.setMaximum(max(1, int(total)))
            self._ai_dlg.setValue(int(step))
        else:
            self.ai_progress.setMaximum(max(1, int(total)))
            self.ai_progress.setValue(int(step))

    @QtCore.Slot(list)
    def _ai_slot_finished(self, failed: list) -> None:
        self._ai_progress_end()
        self._cleanup_ai_thread()
        for d in self.differences:
            d.enabled = False
        self._write_config_snapshot()
        self.rebuild_lists()
        self.update_total_count()
        if failed:
            self._write_meta_status('hasError', persist=True)
        else:
            self._write_meta_status('completed', persist=True)
            self.export_pin_mosaic()

        QtWidgets.QMessageBox.information(self, "AI处理", "AI已完成处理")

    @QtCore.Slot(str)
    def _ai_slot_error(self, msg: str) -> None:
        self._ai_progress_end()
        self._cleanup_ai_thread()
        QtWidgets.QMessageBox.critical(self, "AI处理失败", msg)
        self._write_meta_status('hasError', persist=True)
        self.toggle_ai_preview.setChecked(False)
        self.toggle_ai_preview.setEnabled(False)
        self._remove_ai_overlays()

    # Side panel with tag buttons and list
    def _build_side_panel(self, section: str) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        panel.setFixedWidth(350)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # tag buttons
        tag_grid = QtWidgets.QGridLayout()
        tag_grid.setSpacing(6)
        tags = ["情感", "颜色", "增强", "置换", "修改"]
        for i, tag in enumerate(tags):
            btn = QtWidgets.QPushButton(tag)
            btn.setObjectName(f"tag_{tag}")
            btn.clicked.connect(lambda _=False, s=section, t=tag: self.add_difference(s, t))
            color = CATEGORY_COLOR_MAP.get(tag, QtGui.QColor('#ff0000'))
            btn.setStyleSheet(f"QPushButton {{ color: #fff; border:none; border-radius:14px; padding:6px 8px; background:{color.name()}; }}")
            tag_grid.addWidget(btn, 0, i)
        layout.addLayout(tag_grid)

        # list
        list_widget = QtWidgets.QListWidget()
        list_widget.setObjectName(f"list_{section}")
        # 支持单选用于高亮
        list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        list_widget.itemSelectionChanged.connect(self.on_list_selection_changed)
        # 取消 hover 同步高亮：仅保留选中高亮
        list_widget.setMouseTracking(False)
        list_widget.viewport().setMouseTracking(False)
        list_widget.itemEntered.connect(lambda it, s=section, lw=list_widget: self._on_list_item_entered(s, lw, it))
        list_widget.viewport().leaveEvent = lambda e, s=section: self._on_list_hover_leave(s)
        layout.addWidget(list_widget, 1)

        return panel

    def current_list(self, section: str) -> QtWidgets.QListWidget:
        return self.findChild(QtWidgets.QListWidget, f"list_{section}")

    def add_difference(self, section: str, category: str) -> None:
        # default rectangle at image center (natural coords)
        scene = self.up_scene if section == 'up' else self.down_scene
        r = scene.sceneRect()
        size = min(r.width(), r.height()) * 0.2
        size = max(MIN_RECT_SIZE, size)
        rect = QtCore.QRectF(r.center().x() - size / 2, r.center().y() - size / 2, size, size)
        diff = Difference(
            id=now_id(),
            name=f"不同点 {len(self.differences) + 1}",
            section=section,
            category=category or "",
            label="",
            enabled=True,
            visible=True,
            x=rect.x(),
            y=rect.y(),
            width=rect.width(),
            height=rect.height(),
        )
        self.differences.append(diff)
        self._add_rect_items(diff)
        self.rebuild_lists()
        self._make_dirty()
        self.update_total_count()

    def _add_rect_items(self, diff: Difference) -> None:
        color = CATEGORY_COLOR_MAP.get(diff.category, QtGui.QColor('#ff0000'))
        item_up = DifferenceRectItem(diff, color, on_change=self.on_geometry_changed, is_up=True)
        item_down = DifferenceRectItem(diff, color, on_change=self.on_geometry_changed, is_up=False)
        self.up_scene.addItem(item_up)
        self.down_scene.addItem(item_down)
        self.rect_items_up[diff.id] = item_up
        self.rect_items_down[diff.id] = item_down

        # style when disabled
        self._apply_enabled_style(diff)

        # no Qt signals; callbacks are triggered in itemChange/mouseMoveEvent

        # context menu (right-click delete)
        def add_ctx(item: DifferenceRectItem):
            item.setFlag(QtWidgets.QGraphicsItem.ItemIsFocusable, True)
            item.setAcceptedMouseButtons(QtCore.Qt.AllButtons)
            item.contextMenuEvent = lambda ev, _id=diff.id: self._on_item_context_menu(ev, _id)

        add_ctx(item_up)
        add_ctx(item_down)

    def _apply_enabled_style(self, diff: Difference) -> None:
        opacity = 1.0 if diff.visible else 0.35
        self.rect_items_up[diff.id].setOpacity(opacity)
        self.rect_items_down[diff.id].setOpacity(opacity)
        # Sync visibility as well when enabled state changes
        self._sync_item_visibility(diff)

    def _set_rect_graphics_visible(self, item: DifferenceRectItem, visible_rect: bool) -> None:
        # 控制仅"矩形外观"和"拖拽句柄"的可见性，不影响圆和文本
        if visible_rect:
            pen = QtGui.QPen(QtGui.QColor('#ff0000'))
            pen.setWidth(2)
            item.setPen(pen)
            # 统一填充透明度，不随选中状态改变
            item.setBrush(QtGui.QBrush(QtGui.QColor(255, 0, 0, 40)))
        else:
            item.setPen(QtGui.QPen(QtCore.Qt.NoPen))
            item.setBrush(QtGui.QBrush(QtCore.Qt.transparent))
        # handles 跟随矩形外观
        try:
            for h in getattr(item, 'handles', []) or []:
                h.setVisible(visible_rect)
        except Exception:
            pass

    def _sync_item_visibility(self, diff: Difference) -> None:
        show_regions = self.toggle_regions.isChecked()
        show_hints = self.toggle_hints.isChecked()
        show_labels = self.toggle_labels.isChecked()
        for items in (self.rect_items_up, self.rect_items_down):
            item = items.get(diff.id)
            if not item:
                continue
            # 父项是否可见仅由 enabled 决定
            item.setVisible(diff.visible)
            # 仅控制矩形外观
            self._set_rect_graphics_visible(item, visible_rect=(show_regions and diff.visible))
            # 圆与文本单独控制
            item.circle_item.setVisible(show_hints and diff.visible)
            vis_label = show_labels and diff.visible and bool((diff.label or '').strip())
            item.set_label_visible(vis_label)

    def on_geometry_changed(self, changed_id: Optional[str] = None) -> None:
        # items already updated their diff; sync the counterpart geometry and labels
        if self._syncing_rect_update:
            return
        try:
            self._syncing_rect_update = True
            self._make_dirty()
            diffs_to_update = self.differences if not changed_id else [next((d for d in self.differences if d.id == changed_id), None)]
            diffs_to_update = [d for d in diffs_to_update if d is not None]
            for diff in diffs_to_update:
                u = self.rect_items_up.get(diff.id)
                d = self.rect_items_down.get(diff.id)
                # --- 强制同步圆心 ---
                if u:
                    u.setPos(diff.x, diff.y)
                    if u.rect().width() != diff.width or u.rect().height() != diff.height:
                        u.setRect(QtCore.QRectF(0, 0, diff.width, diff.height))
                    u.update_handles()  # update_handles会用diff.cx/cy
                if d:
                    d.setPos(diff.x, diff.y)
                    if d.rect().width() != diff.width or d.rect().height() != diff.height:
                        d.setRect(QtCore.QRectF(0, 0, diff.width, diff.height))
                    d.update_handles()  # update_handles会用diff.cx/cy
                # --- END ---
                self._update_radius_value_for_label(diff)

        finally:
            self._syncing_rect_update = False

    def rebuild_lists(self) -> None:
        for section in ('up', 'down'):
            lw = self.current_list(section)
            lw.clear()
        global_idx = 1
        for diff in self.differences:
            lw = self.current_list(diff.section)
            color = CATEGORY_COLOR_MAP.get(diff.category, QtGui.QColor('#ff0000'))
            item = QtWidgets.QListWidgetItem()
            item.setData(QtCore.Qt.UserRole, diff.id)
            w = QtWidgets.QWidget()
            gl = QtWidgets.QGridLayout(w)
            gl.setContentsMargins(6, 4, 6, 4)
            gl.setHorizontalSpacing(6)
            title = QtWidgets.QLabel(f"茬点{global_idx}")
            title.setStyleSheet(f"color:{color.name()}; font-size:12px; font-weight:600;")
            edit = QtWidgets.QLineEdit()
            edit.setText(diff.label)
            edit.textChanged.connect(lambda text, _id=diff.id: self.on_label_changed(_id, text))
            enabled = QtWidgets.QCheckBox()
            enabled.setChecked(diff.enabled)
            enabled.stateChanged.connect(lambda _state, _id=diff.id: self.on_enabled_toggled(_id))
            visibled = QtWidgets.QCheckBox()
            visibled.setChecked(diff.visible)
            visibled.stateChanged.connect(lambda _state, _id=diff.id: self.on_visibled_toggled(_id))
            # radius display label (read-only)
            radius_label = QtWidgets.QLabel()
            radius_label.setObjectName(f"radius_{diff.id}")
            radius_label.setStyleSheet("color:#666;font-size:11px;")
            # store for later updates
            if not hasattr(self, 'radius_labels'):
                self.radius_labels = {}
            self.radius_labels[diff.id] = radius_label
            self._update_radius_value_for_label(diff)
            # per-item delete icon button (X)
            btn_delete = QtWidgets.QToolButton()
            btn_delete.setToolTip("删除该茬点")
            btn_delete.setAutoRaise(True)
            btn_delete.setFixedSize(24, 24)
            try:
                btn_delete.setText("X")
                btn_delete.setIconSize(QtCore.QSize(14, 14))
            except Exception:
                btn_delete.setText("X")
            btn_delete.setStyleSheet("QToolButton{border:none;background:transparent;} QToolButton:hover{background:rgba(220,53,69,0.12);border-radius:4px;}")
            btn_delete.clicked.connect(lambda _=False, _id=diff.id: self.delete_diff_by_id(_id))

            gl.addWidget(visibled, 0, 0)
            gl.addWidget(title, 0, 1)
            gl.addWidget(edit, 0, 2)
            gl.addWidget(radius_label, 0, 3)
            gl.addWidget(enabled, 0, 4)
            gl.addWidget(btn_delete, 0, 5)
            gl.setColumnStretch(1, 1)
            w.setLayout(gl)
            item.setSizeHint(w.sizeHint())
            lw.addItem(item)
            lw.setItemWidget(item, w)
            global_idx += 1
        self.update_total_count()

        # 维持当前选中高亮
        if hasattr(self, '_selected_diff_id') and self._selected_diff_id:
            self._set_selected_diff(self._selected_diff_id)

    def on_label_changed(self, diff_id: str, text: str) -> None:
        diff = next((d for d in self.differences if d.id == diff_id), None)
        if not diff:
            return
        diff.label = text
        u = self.rect_items_up.get(diff.id)
        d = self.rect_items_down.get(diff.id)
        self._make_dirty()
        if u:
            u.update_label()
        if d:
            d.update_label()


    def _make_dirty(self) -> None:
        self._is_dirty = True
        self._update_window_title()
        self._update_status_bar('unsaved')

    def _update_radius_value_for_label(self, diff: Difference) -> None:
        label = getattr(self, 'radius_labels', {}).get(diff.id)
        if not label:
            return
        # show actual radius px (use 'up' fields for展示)
        size_min = max(1.0, min(diff.width, diff.height))
        half = size_min / 2.0
        lvl = diff.hint_level
        if 1 <= lvl <= len(RADIUS_LEVELS):
            radius_px = min(float(RADIUS_LEVELS[lvl - 1]), half)
        else:
            radius_px = half
        label.setText(f"半径: {int(round(radius_px))}")

    def on_enabled_toggled(self, diff_id: str) -> None:
        diff = next((d for d in self.differences if d.id == diff_id), None)
        if not diff:
            return
        diff.enabled = not diff.enabled
        self._make_dirty()
        self.update_total_count()

    def on_visibled_toggled(self, diff_id: str) -> None:
        diff = next((d for d in self.differences if d.id == diff_id), None)
        if not diff:
            return
        diff.visible = not diff.visible
        self._apply_enabled_style(diff)
        print("visible toggled")
        self._sync_item_visibility(diff)

    def on_list_selection_changed(self) -> None:
        # reflect list selection to scene items (both up/down)
        if self._syncing_selection:
            return
        lw = self.sender()
        if not isinstance(lw, QtWidgets.QListWidget):
            return
        item = lw.currentItem()
        diff_id = item.data(QtCore.Qt.UserRole) if item else None
        self._set_selected_diff(diff_id)

    def _set_selected_diff(self, diff_id: Optional[str]) -> None:
        self._selected_diff_id = diff_id
        # 同步另一个列表的当前行
        self._syncing_selection = True
        try:
            for section in ('up', 'down'):
                lw = self.current_list(section)
                found = False
                for i in range(lw.count()):
                    it = lw.item(i)
                    if diff_id is not None and it and it.data(QtCore.Qt.UserRole) == diff_id:
                        lw.setCurrentRow(i)
                        found = True
                        break
                if not found and diff_id is None:
                    lw.clearSelection()
        finally:
            self._syncing_selection = False
        # 应用透明度高亮
        self._apply_selected_opacity()

    def _apply_selected_opacity(self) -> None:
        # 使用填充颜色突出选中项：选中为蓝色填充，其余为红色填充；透明度固定
        selected_id = getattr(self, '_selected_diff_id', None)
        for mapping in (self.rect_items_up, self.rect_items_down):
            for did, item in mapping.items():
                try:
                    # 隐藏时不修改以免意外显现
                    if item.pen().style() == QtCore.Qt.NoPen:
                        continue
                    fill = QtGui.QColor('#0d6efd') if (selected_id is not None and did == selected_id) else QtGui.QColor('#ff0000')
                    fill.setAlpha(40)
                    item.setBrush(QtGui.QBrush(fill))
                    # 保持描边为红色
                    pen = item.pen()
                    if pen.style() != QtCore.Qt.NoPen:
                        pen.setColor(QtGui.QColor('#ff0000'))
                        pen.setWidth(2)
                        item.setPen(pen)
                except Exception:
                    pass

    def delete_selected(self, section: str) -> None:
        lw = self.current_list(section)
        it = lw.currentItem()
        if not it:
            QtWidgets.QMessageBox.information(self, "提示", "请先在列表选择一个不同点")
            return
        diff_id = it.data(QtCore.Qt.UserRole)
        self.delete_diff_by_id(diff_id)

    def delete_diff_by_id(self, diff_id: str) -> None:
        idx = next((i for i, d in enumerate(self.differences) if d.id == diff_id), -1)
        if idx < 0:
            return
        # confirm delete
        try:
            resp = QtWidgets.QMessageBox.question(
                self,
                "确认删除",
                f"确定删除茬点{idx + 1}吗？",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if resp != QtWidgets.QMessageBox.Yes:
                return
        except Exception:
            pass
        # capture indices/count before mutation for file renaming
        deleted_index = idx + 1
        old_count = len(self.differences)

        d = self.differences.pop(idx)
        u = self.rect_items_up.pop(d.id, None)
        dn = self.rect_items_down.pop(d.id, None)

        # also remove AI overlays for this diff
        ou = self.ai_overlays_up.pop(d.id, None)
        od = self.ai_overlays_down.pop(d.id, None)
        if ou:
            try:
                self.up_scene.removeItem(ou)
            except Exception:
                pass
        if od:
            try:
                self.down_scene.removeItem(od)
            except Exception:
                pass
        if u:
            self.up_scene.removeItem(u)
        if dn:
            self.down_scene.removeItem(dn)
        # 1) 删除对应 AI 输出图片，并重命名后续序号
        try:
            level_dir = self.level_dir()
            # 删除 region{deleted_index}.png
            victim = os.path.join(level_dir, f"region{deleted_index}.png")
            if os.path.isfile(victim):
                os.remove(victim)
            # 将 region{i}.png -> region{i-1}.png (i 从 deleted_index+1 到 old_count)
            for i in range(deleted_index + 1, old_count + 1):
                src = os.path.join(level_dir, f"region{i}.png")
                dst = os.path.join(level_dir, f"region{i-1}.png")
                if os.path.isfile(src):
                    # 若目标已存在（理论上不该发生），先移除目标以避免跨平台报错
                    if os.path.isfile(dst):
                        os.remove(dst)
                    shutil.move(src, dst)
        except Exception:
            # 静默处理文件系统异常，避免影响UI流
            pass

        # 2) 立即持久化当前配置与元信息（不做校验，避免未填写文本阻塞）
        try:
            self._write_config_snapshot()
        except Exception:
            pass
        try:
            # 状态置为未保存，写入 meta（包含 needAI 重排）
            self._write_meta_status('unsaved', persist=True)
        except Exception:
            pass

        # 3) reindex titles by rebuilding lists
        self.rebuild_lists()
        self._make_dirty()
        if getattr(self, '_selected_diff_id', None) == diff_id:
            self._set_selected_diff(None)

    def refresh_visibility(self) -> None:
        for diff in self.differences:
            self._sync_item_visibility(diff)

    # === AI 预览覆盖 ===
    def on_toggle_ai_preview(self) -> None:
        if self.toggle_ai_preview.isChecked():
            self.refresh_ai_overlays()
        else:
            self._remove_ai_overlays()

    def ai_result_path(self, index: int) -> str:
        return os.path.join(self.level_dir(), f"region{index}.png")

    def _remove_ai_overlays(self) -> None:
        # remove from scenes and clear
        for item in list(self.ai_overlays_up.values()):
            try:
                self.up_scene.removeItem(item)
            except Exception:
                pass
        for item in list(self.ai_overlays_down.values()):
            try:
                self.down_scene.removeItem(item)
            except Exception:
                pass
        self.ai_overlays_up.clear()
        self.ai_overlays_down.clear()

    def _scale_center_ai_overlay(self, pm: QtGui.QPixmap, w: int, h: int) -> tuple[QtGui.QPixmap, float, float]:
        """按比例缩放并计算把缩放后图像居中贴入 w*h 框的 dx/dy 偏移。"""
        try:
            # 规一 HiDPI，避免 DPR 导致的1px偏差
            if pm.devicePixelRatio() != 1:
                img = pm.toImage()
                img.setDevicePixelRatio(1.0)
                pm = QtGui.QPixmap.fromImage(img)
        except Exception:
            pass
        scaled = pm.scaled(w, h, QtCore.Qt.KeepAspectRatio, QtCore.Qt.SmoothTransformation)
        dx = (w - scaled.width()) * 0.5
        dy = (h - scaled.height()) * 0.5
        return scaled, dx, dy

    def refresh_ai_overlays(self) -> None:
        # rebuild overlays from disk according to current differences order
        self._remove_ai_overlays()
        if not self.toggle_ai_preview.isChecked():
            return
        for idx, d in enumerate(self.differences, start=1):
            path = self.ai_result_path(idx)
            if not os.path.isfile(path):
                continue
            pm = QtGui.QPixmap(path)
            if pm.isNull():
                continue
            pm.setDevicePixelRatio(1.0)  # 避免结果图自带 DPR 干扰
            rect = self._ai_crop_rect_px(d)
            scaled = pm.scaled(rect.width(), rect.height(),
                   QtCore.Qt.KeepAspectRatio,
                   QtCore.Qt.SmoothTransformation)

            item = QtWidgets.QGraphicsPixmapItem(scaled)
            # ——用中心锚点，彻底规避奇偶像素误差——
            item.setOffset(-scaled.width() / 2.0, -scaled.height() / 2.0)
            item.setPos(rect.center().x(), rect.center().y())
            item.setZValue(0.5)

            if d.section == 'up':
                self.up_scene.addItem(item)
                self.ai_overlays_up[d.id] = item
            else:
                self.down_scene.addItem(item)
                self.ai_overlays_down[d.id] = item

    def _ai_crop_rect_px(self, d: Difference) -> QtCore.QRect:
        # 用整数真实像素，并限制在对应场景边界
        x = max(0, int(round(d.x)))
        y = max(0, int(round(d.y)))
        w = max(1, int(round(d.width)))
        h = max(1, int(round(d.height)))
        scene = self.up_scene if d.section == 'up' else self.down_scene
        bounds = QtCore.QRect(0, 0, int(scene.width()), int(scene.height()))
        return QtCore.QRect(x, y, w, h).intersected(bounds)

    def _update_ai_overlay_geometry(self) -> None:
        if not self.toggle_ai_preview.isChecked():
            return
        for d in self.differences:
            item = (self.ai_overlays_up if d.section == 'up' else self.ai_overlays_down).get(d.id)
            if not item:
                continue
            try:
                rect = self._ai_crop_rect_px(d)
                # 重新对齐中心
                item.setPos(rect.center().x(), rect.center().y())
                cur = item.pixmap()
                if not cur.isNull() and (cur.width() != rect.width() or cur.height() != rect.height()):
                    scaled = cur.scaled(rect.width(), rect.height(),
                                        QtCore.Qt.KeepAspectRatio,
                                        QtCore.Qt.SmoothTransformation)
                    item.setPixmap(scaled)
                    item.setOffset(-scaled.width() / 2.0, -scaled.height() / 2.0)
                else:
                    # 保证中心锚点存在
                    item.setOffset(-cur.width() / 2.0, -cur.height() / 2.0)
            except Exception:
                pass

    def update_total_count(self) -> None:
        count = sum(1 for d in self.differences if d.enabled)
        self.total_count.setText(f"茬点总计：{len(self.differences)}, 已勾选AI处理:{count}项")
        # 没有茬点时，不允许进行AI处理
        try:
            self.btn_submit.setEnabled(count > 0)
        except Exception:
            pass

    # removed spin count UI

    # === Save/Load ===
    def level_dir(self) -> str:
        # directory for this level
        return os.path.join(self.config_dir, f"{self.pair.name}")

    def config_json_path(self) -> str:
        return os.path.join(self.level_dir(), "config.json")

    def meta_json_path(self) -> str:
        return os.path.join(self.level_dir(), "meta.json")

    def validate_before_save(self) -> Tuple[bool, Optional[str]]:
        # 1) labels required (all points)
        missing = [str(i + 1) for i, d in enumerate(self.differences) if not (d.label or "").strip()]
        if missing:
            return False, f"以下茬点未填写文本：{', '.join(missing)}"

        # 2) circle overlap <= 10%
        # compute circle geometries in the same (natural) coordinate space
        circles = []
        for d in self.differences:
            r_w, r_h = d.width, d.height
            cx_local = d.cx if d.cx >= 0 else r_w / 2.0
            cy_local = d.cy if d.cy >= 0 else r_h / 2.0
            lvl = d.hint_level
            if 1 <= lvl <= len(RADIUS_LEVELS):
                radius_px = float(RADIUS_LEVELS[lvl - 1])
            else:
                radius_px = min(r_w, r_h) / 2.0
            # absolute center in natural coordinates
            cx_abs = d.x + cx_local
            cy_abs = d.y + cy_local
            circles.append((cx_abs, cy_abs, radius_px))

        def circle_overlap_ratio(c1, c2) -> float:
            # return overlap area divided by smaller circle area
            (x1, y1, r1) = c1
            (x2, y2, r2) = c2
            dx = x1 - x2
            dy = y1 - y2
            d = max(0.0, (dx * dx + dy * dy) ** 0.5)
            if d >= r1 + r2:
                return 0.0
            if d <= abs(r1 - r2):
                inter_area = 3.141592653589793 * min(r1, r2) ** 2
            else:
                # circle-circle intersection area formula
                import math
                alpha = math.acos((r1 * r1 + d * d - r2 * r2) / (2 * r1 * d))
                beta = math.acos((r2 * r2 + d * d - r1 * r1) / (2 * r2 * d))
                inter_area = r1 * r1 * alpha + r2 * r2 * beta - 0.5 * math.sin(2 * alpha) * r1 * r1 - 0.5 * math.sin(2 * beta) * r2 * r2
            small_area = 3.141592653589793 * min(r1, r2) ** 2
            return inter_area / max(1.0, small_area)

        n = len(circles)
        violations: List[str] = []
        for i in range(n):
            for j in range(i + 1, n):
                ratio = circle_overlap_ratio(circles[i], circles[j])
                if ratio > 0.10:
                    violations.append(f"茬点{i + 1} 与 茬点{j + 1} 重叠 {ratio * 100:.1f}% (>10%)")
        if violations:
            return False, "存在圆形区域重叠超过10%的情况：\n" + "\n".join(violations[:10])

        return True, None

    def on_save_clicked(self) -> None:
        # Save now also performs pre-save validation
        ok, msg = self.validate_before_save()
        if not ok:
            QtWidgets.QMessageBox.warning(self, "校验失败", msg or "校验失败")
            return
        self._write_meta_status('saved', persist=True)
        self.save_config()
        self._is_dirty = False
        self._update_window_title()

    def on_ai_process(self) -> None:
        # 保存前置：未保存则禁止处理
        if getattr(self, '_is_dirty', False) or self.meta_status == 'unsaved':
            QtWidgets.QMessageBox.information(self, "提示", "当前修改尚未保存，请先保存后再进行AI处理。")
            return
        # Step 1: alidation
        # 限制茬点数量：仅当启用的茬点数为 15/20/25 时允许AI处理
        allowed_counts = {15, 20, 25}
        enabled_count = sum(1 for d in self.differences if d.enabled)
        if enabled_count not in allowed_counts:
            QtWidgets.QMessageBox.information(
                self,
                "AI处理",
                f"当前启用的茬点数为 {enabled_count}，AI处理仅支持 15、20 或 25 个，请调整后重试。"
            )
            return

        # Step 2: 从 meta.json 中读取待处理索引（若存在），否则根据 needAI 计算
        targets: List[int] = []
        for idx, d in enumerate(self.differences, start=1):
            if d.enabled:
                targets.append(idx)
        if not targets:
            QtWidgets.QMessageBox.information(self, "AI处理", "未勾选茬点，请勾选要处理的茬点")
            return
        try:
            self._write_meta_status('aiPending', persist=True)
        except Exception:
            pass
        # Step 3: 在后台线程执行AI，并显示非阻塞进度对话框
        # 准备 origin 路径
        level_dir = self.level_dir()
        origin = None
        for ext in ['.png', '.jpg', '.jpeg']:
            p = os.path.join(level_dir, f'origin{ext}')
            if os.path.isfile(p):
                origin = p
                break
        if origin is None:
            origin = self.pair.up_image_path

        # 状态栏进度
        self._ai_progress_start(len(targets))

        # 后台线程
        self._ai_thread = QtCore.QThread(self)
        self._ai_worker = AIWorker(level_dir, origin, self.differences, targets)
        self._ai_worker.moveToThread(self._ai_thread)
        self._ai_thread.started.connect(self._ai_worker.run)
        # Ensure slots execute on GUI thread
        self._ai_worker.progressed.connect(self._ai_slot_progress, QtCore.Qt.QueuedConnection)
        self._ai_worker.finished.connect(self._ai_slot_finished, QtCore.Qt.QueuedConnection)
        self._ai_worker.error.connect(self._ai_slot_error, QtCore.Qt.QueuedConnection)
        self._ai_thread.finished.connect(self._ai_thread.deleteLater)
        self._ai_thread.start()

    def _write_meta_status(self, status: str, persist: bool = False) -> None:
        self.meta_status = status
        # merge or write meta.json
        meta_path = self.meta_json_path()
        # build needAI array by current differences order
        meta = {
            "imageName": os.path.basename(self.pair.up_image_path),
            "imageSize": [int(self.up_scene.width()), int(self.up_scene.height())],
            "status": status,
        }
        
        # 仅在持久化请求或未启用延迟时写盘
        if persist or not getattr(self, '_defer_disk_writes', False):
            os.makedirs(self.level_dir(), exist_ok=True)
            try:
                with open(meta_path, 'w', encoding='utf-8') as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        self._update_status_bar(status)

    def save_config(self, show_message: bool = True) -> None:
        self._write_config_snapshot()

        # copy original image into level dir and rename as origin.{ext}
        try:
            src_img = self.pair.up_image_path
            if os.path.isfile(src_img):
                import shutil
                _, ext = os.path.splitext(src_img)
                if not ext:
                    ext = '.png'
                dst_img = os.path.join(self.level_dir(), f'origin{ext}')
                if not os.path.exists(dst_img):
                    shutil.copy2(src_img, dst_img)
        except Exception:
            pass

        QtWidgets.QMessageBox.information(self, "成功", f"配置保存成功\n")

        self._update_status_bar()

    def _write_config_snapshot(self) -> None:
        """Write current differences to config.json without validation or UI side-effects.
        Keeps the on-disk config in sync after deletions/renames.
        """
        # natural size = scene size
        w = self.up_scene.width()
        h = self.up_scene.height()

        def to_percent_y_bottom(y_px: float) -> float:
            return 1.0 - (y_px / h)

        def to_percent_x(x_px: float) -> float:
            return x_px / w

        data = {
            "differenceCount": len(self.differences),
            "differences": []
        }
        for d in self.differences:
            points = [
                {"x": to_percent_x(d.x), "y": to_percent_y_bottom(d.y)},
                {"x": to_percent_x(d.x + d.width), "y": to_percent_y_bottom(d.y)},
                {"x": to_percent_x(d.x + d.width), "y": to_percent_y_bottom(d.y + d.height)},
                {"x": to_percent_x(d.x), "y": to_percent_y_bottom(d.y + d.height)},
            ]
            # compute hint circle from stored local center and radius
            # local center -> absolute
            cx = to_percent_x(d.x + d.cx)
            cy = to_percent_y_bottom(d.y + d.cy)

            lvl = d.hint_level
            # 从 hint level 获取半径（修正list越界问题）
            if isinstance(lvl, int) and 1 <= lvl <= len(RADIUS_LEVELS):
                radius = RADIUS_LEVELS[lvl - 1]
            else:
                radius = 0

            data["differences"].append({
                "id": d.id,
                "name": d.name,
                "section": ('down' if d.section == 'down' else 'up'),
                "category": d.category or "",
                "label": d.label or "",
                "enabled": bool(d.enabled),
                "points": points,
                "hintLevel": int(lvl),
                "circleCenter": {"x": cx, "y": cy},
                "circleRadius": radius
            })

        os.makedirs(self.level_dir(), exist_ok=True)
        cfg_path = self.config_json_path()
        with open(cfg_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def load_existing_config(self) -> None:
        dir_path = self.level_dir()
        if not os.path.isdir(dir_path):
            return
        self._load_from_dir(dir_path)

    def _clear_all_items(self) -> None:
        # remove existing rect items from scenes
        self._suppress_scene_selection = True
        for item in list(self.rect_items_up.values()):
            try:
                self.up_scene.removeItem(item)
            except Exception:
                pass
        for item in list(self.rect_items_down.values()):
            try:
                self.down_scene.removeItem(item)
            except Exception:
                pass
        self.rect_items_up.clear()
        self.rect_items_down.clear()
        self._suppress_scene_selection = False

    def _load_from_dir(self, dir_path: str) -> None:
        # read config.json (gracefully handle missing file as new level)
        cfg_path = os.path.join(dir_path, 'config.json')
        if not os.path.isfile(cfg_path):
            # treat as a new blank level
            self._clear_all_items()
            self.differences.clear()
            self.rebuild_lists()
            self.update_total_count()
            self.toggle_ai_preview.setEnabled(False)
            self.toggle_ai_preview.setChecked(False)
            self._remove_ai_overlays()
            self._is_dirty = False
            self._update_status_bar('unsaved')
            self._update_window_title()
            return
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "加载失败", str(exc))
            return

        # natural size = scene size
        w = self.up_scene.width()
        h = self.up_scene.height()

        def from_percent_x(px: float) -> float:
            return px * w

        def from_percent_y_bottom(py: float) -> float:
            return (1.0 - py) * h

        self._clear_all_items()
        self.differences.clear()
        for diff in cfg.get('differences', []):
            points = diff.get('points', [])
            if len(points) < 4:
                continue
            xs = [from_percent_x(p['x']) for p in points]
            ys = [from_percent_y_bottom(p['y']) for p in points]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            c_x = float(diff.get('circleCenter', {}).get('x', -1))
            c_y = float(diff.get('circleCenter', {}).get('y', -1))
            cpx = from_percent_x(c_x)
            cpy = from_percent_y_bottom(c_y)
            d = Difference(
                id=str(diff.get('id', now_id())),
                name=str(diff.get('name', f"不同点 {len(self.differences) + 1}")),
                section=('down' if diff.get('section') == 'down' else 'up'),
                category=str(diff.get('category', "")),
                label=str(diff.get('label', "")),
                enabled=bool(diff.get('enabled', True)),
                visible=True,
                x=min_x,
                y=min_y,
                width=max(MIN_RECT_SIZE, max_x - min_x),
                height=max(MIN_RECT_SIZE, max_y - min_y),
                hint_level = int(diff.get('hintLevel', 0)),
                cx=cpx,
                cy=cpy,
            )
            self.differences.append(d)
            self._add_rect_items(d)

        self.rebuild_lists()
        self.update_total_count()

        # refresh AI overlays based on current differences
        if self.toggle_ai_preview.isChecked():
            self.refresh_ai_overlays()

        # read meta.json if exists
        try:
            with open(os.path.join(dir_path, 'meta.json'), 'r', encoding='utf-8') as f:
                meta = json.load(f)
                self.meta_status = meta.get('status', 'unsaved')
                # 根据历史状态设置AI预览开关可用性
                if self.meta_status == 'completed':
                    self.toggle_ai_preview.setEnabled(True)
                else:
                    self.toggle_ai_preview.setEnabled(False)
                    self.toggle_ai_preview.setChecked(False)
                    self._remove_ai_overlays()
                # 读取 needAI 数组（与 differences 顺序对应）
                
                self._update_status_bar(self.meta_status)
        except Exception:
            self.meta_status = 'unsaved'
            self.toggle_ai_preview.setEnabled(False)
            self.toggle_ai_preview.setChecked(False)
            self._remove_ai_overlays()
            self._update_status_bar(self.meta_status)

    # selection changed slots
    def _on_up_selection_changed(self) -> None:
        self.on_scene_selection_changed('up')

    def _on_down_selection_changed(self) -> None:
        self.on_scene_selection_changed('down')

    def on_scene_selection_changed(self, section: str) -> None:
        if self._syncing_selection or self._suppress_scene_selection:
            return
        mapping = self.rect_items_up if section == 'up' else self.rect_items_down
        selected_id: Optional[str] = None
        for diff_id, item in list(mapping.items()):
            try:
                # skip deleted Qt objects
                if shiboken6 is not None and not shiboken6.isValid(item):
                    mapping.pop(diff_id, None)
                    continue
                if item.isSelected():
                    selected_id = diff_id
                    break
            except RuntimeError:
                # underlying C++ object is gone
                mapping.pop(diff_id, None)
                continue
        if selected_id:
            self._select_diff_items(selected_id)

    def _on_item_context_menu(self, event: QtWidgets.QGraphicsSceneContextMenuEvent, diff_id: str) -> None:
        menu = QtWidgets.QMenu()
        act_delete = menu.addAction("删除该茬点")
        action = menu.exec_(event.screenPos())
        if action == act_delete:
            self.delete_diff_by_id(diff_id)

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        if event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            # try delete selected rect if any
            for items in (self.rect_items_up, self.rect_items_down):
                for rid, item in list(items.items()):
                    if item.isSelected():
                        self.delete_diff_by_id(rid)
                        return
        super().keyPressEvent(event)

    # removed spin count UI

    def _on_list_hover_leave(self, section: str) -> None:
        self._apply_list_hover_highlight(-1)

    def _apply_list_hover_highlight(self, hovered_row: int) -> None:
        # 同步高亮两侧场景的对应矩形
        ids: List[str] = []
        for section in ('up', 'down'):
            lw = self.current_list(section)
            if hovered_row is None or hovered_row < 0:
                target_id = None
            else:
                it = lw.item(hovered_row)
                target_id = it.data(QtCore.Qt.UserRole) if it else None
            ids.append(target_id)
        # 清除所有临时高亮
        for mapping in (self.rect_items_up, self.rect_items_down):
            for it in mapping.values():
                it.set_temp_highlight(False)
        # 清除右侧 hover 背景
        for section in ('up', 'down'):
            lw = self.current_list(section)
            for i in range(lw.count()):
                it = lw.item(i)
                it.setBackground(QtGui.QBrush(QtCore.Qt.transparent))
        # 同步高亮目标（hovered_row 对应的 id 在两个列表中相同顺序）
        if hovered_row is not None and hovered_row >= 0:
            # 找到一个 id 即可
            for section in ('up', 'down'):
                lw = self.current_list(section)
                it = lw.item(hovered_row)
                if not it:
                    continue
                did = it.data(QtCore.Qt.UserRole)
                if did:
                    if did in self.rect_items_up:
                        self.rect_items_up[did].set_temp_highlight(True)
                    if did in self.rect_items_down:
                        self.rect_items_down[did].set_temp_highlight(True)
                    # 设置右侧列表 hover 背景
                    it.setBackground(QtGui.QBrush(QtGui.QColor(0, 123, 255, 40)))
                    break

    def _on_list_item_entered(self, section: str, lw: QtWidgets.QListWidget, item: QtWidgets.QListWidgetItem) -> None:
        row = lw.row(item)
        self._apply_list_hover_highlight(row)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        # 未保存时提示
        if getattr(self, '_is_dirty', False) or self.meta_status == 'unsaved':
            ret = QtWidgets.QMessageBox.question(
                self,
                "未保存",
                "当前修改尚未保存，是否保存后再关闭？",
                QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard | QtWidgets.QMessageBox.Cancel,
                QtWidgets.QMessageBox.Save,
            )
            if ret == QtWidgets.QMessageBox.Save:
                self.on_save_clicked()
                if getattr(self, '_is_dirty', False):
                    event.ignore()
                    return
                event.accept()
                return
            if ret == QtWidgets.QMessageBox.Cancel:
                event.ignore()
                return
            # Discard
            event.accept()
            return
        event.accept()


    # --- ADD: 导出三张贴回结果（不缩放） ---
    def export_composites_no_resize(self,
                                    out_up: Optional[str] = None,
                                    out_down: Optional[str] = None) -> Tuple[str, str]:
        """
        使用 level_dir 下 AI 产物 region{i}.png（按 differences 顺序）贴回到 origin 大图。
        小图原尺寸不缩放；以矩形左上角 (d.x, d.y) 为锚点；越界自动裁剪。
        生成：
        - origin 拷贝
        - 仅贴 up 区域的图
        - 仅贴 down 区域的图
        """
        # 1) 找 origin
        level_dir = self.level_dir()
        origin_path = None
        for ext in ['.png', '.jpg', '.jpeg']:
            p = os.path.join(level_dir, f'origin{ext}')
            if os.path.isfile(p):
                origin_path = p
                break
        if origin_path is None:
            origin_path = self.pair.up_image_path  # 兜底

        big = cv2.imread(origin_path, cv2.IMREAD_UNCHANGED)
        if big is None:
            raise RuntimeError(f"无法读取大图：{origin_path}")

        up_img = big.copy()
        down_img = big.copy()

        H, W = big.shape[:2]

        # 2) 逐差异贴回（使用 region{i}.png）
        for idx, d in enumerate(self.differences, start=1):
            region_path = self.ai_result_path(idx)  # {level_dir}/region{idx}.png
            if not os.path.isfile(region_path):
                continue
            small = cv2.imread(region_path, cv2.IMREAD_UNCHANGED)  # 可能 BGRA
            if small is None:
                continue

            # 左上角锚点（自然像素）
            l = int(round(d.x))
            t = int(round(d.y))

            if d.section == 'up':
                _alpha_paste_no_resize_cv(small, up_img, l, t)
            elif d.section == 'down':
                _alpha_paste_no_resize_cv(small, down_img, l, t)
            else:
                # 未知 section，忽略
                pass

        # 3) 写盘
        out_up     = out_up     or os.path.join(level_dir, "composite_up.png")
        out_down   = out_down   or os.path.join(level_dir, "composite_down.png")

        cv2.imwrite(out_up, up_img)
        cv2.imwrite(out_down, down_img)

        return  out_up, out_down

    def export_pin_mosaic(self, out_path: Optional[str] = None,
                      margin: int = 40, gap: int = 24) -> None:
        """
        先确保有三张图（origin/up/down），再合成“品”字形拼图。
        如果还没导出过，则调用 export_composites_no_resize() 生成它们。
        """
        level_dir = self.level_dir()
        out_path = out_path or os.path.join(level_dir, "apreview.png")

        # 先找现成的三张图
        origin_path = None
        for ext in ['.png', '.jpg', '.jpeg']:
            p = os.path.join(level_dir, f'origin{ext}')
            if os.path.isfile(p):
                origin_path = p
                break
        if origin_path is None:
            origin_path = self.pair.up_image_path  # 兜底

        up_path = os.path.join(level_dir, "composite_up.png")
        down_path = os.path.join(level_dir, "composite_down.png")

        # 若 up/down 不存在，就现做一次
        need_build = (not os.path.isfile(up_path)) or (not os.path.isfile(down_path))
        if need_build:
            try:
                self.export_composites_no_resize(out_up=up_path, out_down=down_path)
            except Exception:
                # 如果 export 失败，就直接用 origin 占位，以免中断
                if not os.path.isfile(up_path):
                    up_path = origin_path
                if not os.path.isfile(down_path):
                    down_path = origin_path

        # 读图并合成
        o = cv2.imread(origin_path, cv2.IMREAD_COLOR)
        u = cv2.imread(up_path, cv2.IMREAD_COLOR)
        d = cv2.imread(down_path, cv2.IMREAD_COLOR)
        if o is None or u is None or d is None:
            raise RuntimeError("读取 origin/up/down 失败，请检查路径。")

        compose_pin_layout(o, u, d, out_path, margin=margin, gap=gap, bg_bgr=(255, 255, 255))
        os.remove(up_path)
        os.remove(down_path)

class AIWorker(QtCore.QObject):
    progressed = QtCore.Signal(int, int)  # step, total
    finished = QtCore.Signal(list)        # failed indices
    error = QtCore.Signal(str)

    def __init__(self, level_dir: str, origin_path: str, differences: List[Difference], target_indices: List[int]):
        super().__init__()
        self.level_dir = level_dir
        self.origin_path = origin_path
        self.differences = differences
        self.target_indices = target_indices

    @QtCore.Slot()
    def run(self) -> None:
        try:
            img = QtGui.QImage(self.origin_path)
            if img.isNull():
                raise RuntimeError('无法打开 origin 图像')

            total = len(self.target_indices)
            step = 0
            import shutil, time
            for idx in self.target_indices:
                d = self.differences[idx - 1]
                # 裁剪并调用AI
                x = max(0, int(round(d.x)))
                y = max(0, int(round(d.y)))
                w = max(1, int(round(d.width)))
                h = max(1, int(round(d.height)))
                rect = QtCore.QRect(x, y, w, h).intersected(img.rect())
                if rect.isEmpty():
                    # count as failed
                    step += 1
                    self.progressed.emit(step, total)
                    continue
                tmp_path = os.path.join(self.level_dir, f'__tmp_region{idx}.png')
                img.copy(rect).save(tmp_path)
                try:
                    req = ImageEditRequester(tmp_path, (d.label or '').strip())
                    req.send_request()
                    out_path = tmp_path.replace('.png', '_result.png')
                    for _ in range(10):
                        if os.path.isfile(out_path):
                            break
                        time.sleep(0.2)
                    if os.path.isfile(out_path):
                        dst = os.path.join(self.level_dir, f'region{idx}.png')
                        shutil.move(out_path, dst)
                    else:
                        # mark failure by keeping tmp missing
                        pass
                except Exception:
                    pass
                finally:
                    try:
                        if os.path.isfile(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass
                step += 1
                self.progressed.emit(step, total)
            # compute failures: region files not present
            failed = []
            for idx in self.target_indices:
                dst = os.path.join(self.level_dir, f'region{idx}.png')
                if not os.path.isfile(dst):
                    failed.append(idx)
            self.finished.emit(failed)
        except Exception as exc:
            self.error.emit(str(exc))



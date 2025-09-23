import os, math
from typing import List, Tuple
from PySide6 import QtCore, QtGui
from models import Difference, RADIUS_LEVELS
import img_rc

QImage = QtGui.QImage

def _round_half_up(x: float) -> int:
    # 与 round() 的 bankers rounding 不同，这里 0.5 -> 1，1.5 -> 2，更稳定
    return int(math.floor(x + 0.5))

def quantize_roi(x: float, y: float, w: float, h: float, W: int, H: int):
    l = max(0, min(W-1, _round_half_up(x)))
    t = max(0, min(H-1, _round_half_up(y)))
    qw = max(1, min(W - l, _round_half_up(w)))
    qh = max(1, min(H - t, _round_half_up(h)))
    return l, t, qw, qh

def _radius_for(lvl: int) -> int:
    idx  = max(0, min(len(RADIUS_LEVELS) - 1, int(lvl)))
    return int(RADIUS_LEVELS[idx])

def  _qimage_from_path(path: str) -> QImage:
    r = QtGui.QImageReader(path)
    r.setAutoTransform(False)
    img = r.read()
    if img.isNull():
        print(f"无法读取图片:{path}")
    return img

def _to_premultiplied(img:QImage) -> QImage:
    return img if img.format() == QImage.Format_ARGB32_Premultiplied \
               else img.convertToFormat(QImage.Format_ARGB32_Premultiplied)

def compose_result(level_dir: str, name: str, ext: str, differences: List[Difference], margin: int = 40, gap: int = 24) -> QImage :
    origin_path = os.path.join(level_dir, f"{name}_origin{ext}")
    base = _qimage_from_path(origin_path)
    up_img, down_img = _render_regions_to_origin(base, differences, level_dir, name)
    up_img.save(os.path.join(level_dir, "composite_up.png"))
    down_img.save(os.path.join(level_dir, "composite_down.png"))

    up_ov, down_ov = _render_circle_over_image(up_img, down_img, differences)

    result = _compose_four_grid(up_img, down_img, up_ov, down_ov)

    result.save(os.path.join(level_dir, "apreview.png"))

def _render_regions_to_origin(base: QtGui.QImage, differences: List[Difference], level_dir: str, name: str) -> Tuple[QImage, QImage]:
    up_img = _to_premultiplied(base).copy()
    down_img = up_img.copy()
    W, H = base.width(), base.height()
    bounds = QtCore.QRect(0, 0, W, H)

    for idx, d in enumerate(differences, start = 1):
        rpath = os.path.join(level_dir, f"{name}_region{idx}.png")
        if not os.path.isfile(rpath):
            continue
        small = _qimage_from_path(rpath)
        if small.isNull():
            continue
        l, t, _, _ = quantize_roi(d.x, d.y, d.width, d.height, W, H)
        sec = d.section
        if sec == "up":
            _draw_to_image(up_img, small, l, t, bounds)
        elif sec == "down":
            _draw_to_image(down_img, small, l, t, bounds)

    return up_img, down_img

def _render_circle_over_image(up_img: QImage, down_img: QImage, differences:List[Difference]) -> Tuple[QImage, QImage]:
    u = up_img.copy()
    d = down_img.copy()
    W, H = u.width(), u.height()
    bounds = QtCore.QRect(0, 0, W, H)

    for diff in differences:
        cx = diff.cx + diff.x
        cy = diff.cy + diff.y
        lvl = diff.hint_level
        cpath = f":/img/c{lvl}.png"
        circle = _qimage_from_path(cpath)
        circle = _to_premultiplied(circle)
        r = _radius_for(lvl)

        cx1, cx2 = _round_half_up(cx) - r, _round_half_up(cy) - r

        _draw_to_image(u, circle, cx1, cx2, bounds)
        _draw_to_image(d, circle, cx1, cx2, bounds)
    return u, d

def _compose_four_grid(up_img: QImage, down_img: QImage, overlay_up:QImage, overlay_down: QImage, margin: int = 40, gap: int = 24)-> QImage:
    w, h = up_img.width(), up_img.height()

    W = margin + w + gap + w + margin
    H = margin + h + gap + h + margin

    canvas = QImage(W, H, QImage.Format_ARGB32_Premultiplied)
    canvas.fill(QtGui.QColor(255, 255, 255))

    p = QtGui.QPainter(canvas)
    p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
    p.setRenderHints(QtGui.QPainter.RenderHint(0))

    x1 = margin
    x2 = margin + w + gap
    y1 = margin
    y2 = margin + h + gap

    # 上排：左=up，右=down
    p.drawImage(QtCore.QPoint(x1, y1), up_img)
    p.drawImage(QtCore.QPoint(x2, y1), down_img)
    # 下排：左=up_overlay，右=down_overlay
    p.drawImage(QtCore.QPoint(x1, y2), overlay_up)
    p.drawImage(QtCore.QPoint(x2, y2), overlay_down)

    p.end()
    return canvas

def _draw_to_image(target: QImage, small: QImage, l:int, t: int, bounds: QtCore.QRect):
    if small.isNull():
        return
    small = _to_premultiplied(small)
    sw, sh = small.width(), small.height()
    dest = QtCore.QRect(l, t, sw, sh)
    inter = dest.intersected(bounds)
    if inter.isEmpty():
        return
    src = QtCore.QRect(
        inter.left() - dest.left(),
        inter.top() - dest.top(),
        inter.width(), inter.height()
    )

    p = QtGui.QPainter(target)
    p.setCompositionMode(QtGui.QPainter.CompositionMode_SourceOver)
    p.setRenderHints(QtGui.QPainter.RenderHint(0))
    p.drawImage(inter.topLeft(), small, src)
    p.end()
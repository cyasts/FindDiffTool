import os
from typing import List

from PySide6 import QtCore, QtGui

from ai_client import (
    A81ImageEditClient,
    GeminiImageEditClient,
)
from models import (
    CANVAS_H,
    CANVAS_W,
    Difference,
)
from utils import quantize_roi


def _make_rect_feather_alpha8(w: int, h: int, f: int) -> QtGui.QImage:
    """生成矩形四周羽化的 Alpha8 掩膜，中心=255，边缘渐变到0"""
    f = max(1, min(f, (w // 2) - 1 if w >= 4 else 1, (h // 2) - 1 if h >= 4 else 1))
    alpha = QtGui.QImage(w, h, QtGui.QImage.Format_Alpha8)
    alpha.fill(0)

    p = QtGui.QPainter(alpha)
    p.setPen(QtCore.Qt.NoPen)

    # 中心不透明
    inner = QtCore.QRect(f, f, max(0, w - 2 * f), max(0, h - 2 * f))
    if inner.width() > 0 and inner.height() > 0:
        p.fillRect(inner, QtGui.QColor(0, 0, 0, 255))

    # 四边线性渐变（在 Alpha8 下，颜色的 alpha 用作像素值）
    # Top
    g = QtGui.QLinearGradient(0, 0, 0, f)
    g.setColorAt(0.0, QtGui.QColor(0, 0, 0, 0))
    g.setColorAt(1.0, QtGui.QColor(0, 0, 0, 255))
    p.fillRect(QtCore.QRect(0, 0, w, f), QtGui.QBrush(g))
    # Bottom
    g = QtGui.QLinearGradient(0, h - f, 0, h)
    g.setColorAt(0.0, QtGui.QColor(0, 0, 0, 255))
    g.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
    p.fillRect(QtCore.QRect(0, h - f, w, f), QtGui.QBrush(g))
    # Left
    g = QtGui.QLinearGradient(0, 0, f, 0)
    g.setColorAt(0.0, QtGui.QColor(0, 0, 0, 0))
    g.setColorAt(1.0, QtGui.QColor(0, 0, 0, 255))
    p.fillRect(QtCore.QRect(0, 0, f, h), QtGui.QBrush(g))
    # Right
    g = QtGui.QLinearGradient(w - f, 0, w, 0)
    g.setColorAt(0.0, QtGui.QColor(0, 0, 0, 255))
    g.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
    p.fillRect(QtCore.QRect(w - f, 0, f, h), QtGui.QBrush(g))

    # 四角径向渐变补齐
    for cx, cy in ((f, f), (w - f, f), (f, h - f), (w - f, h - f)):
        rg = QtGui.QRadialGradient(cx, cy, f)
        rg.setColorAt(0.0, QtGui.QColor(0, 0, 0, 255))
        rg.setColorAt(1.0, QtGui.QColor(0, 0, 0, 0))
        p.setBrush(QtGui.QBrush(rg))
        p.drawEllipse(QtCore.QRect(cx - f, cy - f, 2 * f, 2 * f))

    p.end()
    return alpha


def _apply_alpha_straight(patch: QtGui.QImage, alpha8: QtGui.QImage) -> QtGui.QImage:
    """
    只替换 Alpha 通道，RGB 不变（避免“发灰”）。
    要求 patch 和 alpha8 同宽高。
    """
    w, h = patch.width(), patch.height()
    # 使用非预乘格式，保证 RGB 不被乘暗
    out = patch.convertToFormat(QtGui.QImage.Format_ARGB32)

    # 逐像素写 alpha（简洁版，足够快，因为 ROI 一般不大）
    for y in range(h):
        a_ptr = alpha8.constScanLine(y)
        # ARGB32 在小端内存是 BGRA 顺序
        px = out.scanLine(y)
        # 将 memoryview 转成 bytearray 便于写 alpha
        row = bytearray(px)  # BGRA BGRA ...
        arow = bytes(a_ptr)
        # 每4字节一像素，把第4个字节(索引3)替换为 alpha
        for x in range(w):
            row[4 * x + 3] = arow[x]
        # 回写
        mv = memoryview(px)
        mv[: len(row)] = row

    return out


class AIWorker(QtCore.QObject):
    progressed = QtCore.Signal(int, int)  # step, total
    finished = QtCore.Signal(list)  # failed indices
    error = QtCore.Signal(str)

    def __init__(
        self,
        level_dir: str,
        name: str,
        ext: str,
        differences: List[Difference],
        target_indices: List[int],
    ):
        super().__init__()
        self.level_dir = level_dir
        self.name = name
        self.differences = differences
        self.target_indices = target_indices
        self.ext = ext
        self.origin_path = os.path.normpath(os.path.join(level_dir, f"A", f"{self.name}_origin{self.ext}"))

    def setClient(self, client: str, api: str) -> None:
        if client == "A8":
            self.client = A81ImageEditClient(api)
        else:
            self.client = GeminiImageEditClient()

    @QtCore.Slot()
    def run(self) -> None:
        print(f"Worker run: {self.level_dir=}, {self.name=}, {self.origin_path=}")
        try:
            reader = QtGui.QImageReader(self.origin_path)
            reader.setAutoTransform(False)  # 与 _imread_any 保持一致
            img = reader.read()
            if img.isNull():
                raise RuntimeError("无法打开 origin 图像")

            total = len(self.target_indices)
            W, H = img.width(), img.height()
            step = 0
            for idx in self.target_indices:
                d = self.differences[idx - 1]
                l, t, w, h = quantize_roi(d.x, d.y, d.width, d.height, W, H)
                rect = QtCore.QRect(l, t, w, h)
                subimg = img.copy(rect)  # 裁剪原图

                # 1) 输入图
                canvas = QtGui.QImage(CANVAS_W, CANVAS_H, QtGui.QImage.Format_ARGB32)
                canvas.fill(QtGui.QColor(255, 255, 255, 255))  # 外部为纯白不透明
                ox = (CANVAS_W - w) // 2
                oy = (CANVAS_H - h) // 2
                p = QtGui.QPainter(canvas)
                p.drawImage(ox, oy, subimg)  # ROI 贴中间
                p.end()

                buf = QtCore.QBuffer()
                buf.open(QtCore.QIODevice.ReadWrite)
                canvas.save(buf, "PNG")
                png_bytes = bytes(buf.data())
                buf.close()

                # 2) 掩膜：外部不透明，ROI 清成透明
                mask = QtGui.QImage(CANVAS_W, CANVAS_H, QtGui.QImage.Format_ARGB32)
                mask.fill(QtGui.QColor(255, 255, 255, 255))
                mp = QtGui.QPainter(mask)
                mp.setCompositionMode(QtGui.QPainter.CompositionMode_Clear)
                mp.fillRect(QtCore.QRect(ox, oy, w, h), QtCore.Qt.transparent)
                mp.end()

                bufm = QtCore.QBuffer()
                bufm.open(QtCore.QIODevice.ReadWrite)
                mask.save(bufm, "PNG")
                mask_bytes = bytes(bufm.data())
                bufm.close()

                # 调试：保存看看
                # QtGui.QImage.fromData(png_bytes).save(os.path.join(self.level_dir, "dbg_input.png"))
                # QtGui.QImage.fromData(mask_bytes).save(os.path.join(self.level_dir, "dbg_mask.png"))

                # 获取 AI 返回的图像字节
                img_bytes = self.client.send_request(
                    image_bytes=png_bytes, mask_bytes=mask_bytes, prompt=d.label
                )
                patch = QtGui.QImage.fromData(img_bytes).copy(ox, oy, w, h)
                # 羽化宽度：短边的 6%（可按需调整/做成参数）
                feather_px = max(4, int(min(w, h) * 0.06))
                alpha8 = _make_rect_feather_alpha8(w, h, feather_px)

                # 直通 Alpha：避免预乘导致的整体泛灰
                patch_feathered = _apply_alpha_straight(patch, alpha8)
                final_path = os.path.normpath(
                    os.path.join(self.level_dir, f"A", f"{self.name}_region{idx}.png")
                )
                patch_feathered.save(final_path)
                print(f"Save patch image: {final_path=}")

                step += 1
                self.progressed.emit(step, total)
            # compute failures: region files not present
            failed = []
            for idx in self.target_indices:
                dst = os.path.join(self.level_dir, f"A", f"{self.name}_region{idx}.png")
                if not os.path.isfile(dst):
                    failed.append(idx)
            self.finished.emit(failed)
        except Exception as exc:
            self.error.emit(str(exc))

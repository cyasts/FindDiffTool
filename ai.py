import os, requests, base64, io
from typing import List
from PySide6 import QtCore, QtGui

from models import Difference, CANVAS_W, CANVAS_H
from utils import quantize_roi

BASE_URL = "https://ai.t8star.cn/"
BASE_URL_HK = "https://hk-api.gptbest.vip"
BASE_URL_AM = "https://api.gptbest.vip"

class ImageEditRequester:
    def __init__(self, image_path: str, image_bytes: bytes, mask_bytes:bytes, prompt: str):
        self.image_path = image_path
        self.image_bytes = image_bytes
        self.mask_bytes = mask_bytes
        self.prompt = prompt
        self.BASE_URL = BASE_URL_HK
        # 建议在系统环境变量中设置 BANANA_API_KEY，避免把密钥写入代码库
        self.API_KEY = "sk-RX5FUdtuNTfQvr3LAOsDsL7OdkJZxf7DIhQ73Gfqj7yq50ZO"
        self.MODEL = "nano-banana"
        self.url = f"{self.BASE_URL}/v1/images/edits"
        self.headers = {
            'Authorization': f'Bearer {self.API_KEY}'
        }

    def send_request(self) -> bytes:
        # 记录原始图片尺寸
        files = [
            ('image', ('input.png', io.BytesIO(self.image_bytes), 'image/png')),
            ('mask', ('mask.png', io.BytesIO(self.mask_bytes), "image/png")),
        ]
        prop = (
            "任务设定：我从一张大图裁出 ROI 放到固定画布中央；"
            "画布的非编辑区已是纯白(255,255,255,255)。\n"
            "遮罩规范（务必遵守）：\n"
            "• mask 仅用作选择：透明像素=允许编辑；不透明像素=禁止编辑；不得对 mask 本身做任何绘制或改动。\n"
            "• 只在『image』图像中与 mask 透明区域对应的像素内进行修改；"
            "严禁改变位置/大小/边界对齐；禁止外扩或羽化边缘。\n"
            "• 非编辑区必须与输入像素完全一致（保持纯白 255,255,255,255），"
            "不得新增阴影/纹理/噪声/描边。\n"
            "• 保持几何与透视不变，仅做必要内容替换或修饰，尽量保持原有结构线条。\n"
            "输出要求：返回整张 PNG；非编辑区需为不透明白色(Alpha=255)；禁止透明背景与棋盘格效果。\n"
            f"任务内容：{self.prompt}"
        )
        payload = {
            'model': self.MODEL,
            'prompt': prop,
            'response_format': 'b64_json',
            # 'aspect_ratio': '4:3',
            'size': f"{CANVAS_W}x{CANVAS_H}",
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

        elif 'url' in data0:
            img_url = data0['url']
            img_resp = requests.get(img_url)
            img_bytes = img_resp.content
        else:
            raise RuntimeError("AI未返回b64或url")

        return img_bytes
        # # 保证输出尺寸一致
        # with Image.open(out_path) as out_img:
        #     if out_img.size != (width, height):
        #         out_img = out_img.resize((width, height), Image.LANCZOS)
        #         out_img.save(out_path)


def _make_rect_feather_alpha8(w: int, h: int, f: int) -> QtGui.QImage:
    """生成矩形四周羽化的 Alpha8 掩膜，中心=255，边缘渐变到0"""
    f = max(1, min(f, (w // 2) - 1 if w >= 4 else 1, (h // 2) - 1 if h >= 4 else 1))
    alpha = QtGui.QImage(w, h, QtGui.QImage.Format_Alpha8)
    alpha.fill(0)

    p = QtGui.QPainter(alpha)
    p.setPen(QtCore.Qt.NoPen)

    # 中心不透明
    inner = QtCore.QRect(f, f, max(0, w - 2*f), max(0, h - 2*f))
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
        p.drawEllipse(QtCore.QRect(cx - f, cy - f, 2*f, 2*f))

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
            row[4*x + 3] = arow[x]
        # 回写
        mv = memoryview(px)
        mv[:len(row)] = row

    return out

class AIWorker(QtCore.QObject):
    progressed = QtCore.Signal(int, int)  # step, total
    finished = QtCore.Signal(list)        # failed indices
    error = QtCore.Signal(str)

    def __init__(self, level_dir: str, name: str, ext: str, differences: List[Difference], target_indices: List[int]):
        super().__init__()
        self.level_dir = level_dir
        self.name = name
        self.differences = differences
        self.target_indices = target_indices
        self.ext = ext
        self.origin_path = os.path.join(level_dir, f"{self.name}_origin{self.ext}")

    @QtCore.Slot()
    def run(self) -> None:
        try:
            reader = QtGui.QImageReader(self.origin_path)
            reader.setAutoTransform(False)  # 与 _imread_any 保持一致
            img = reader.read()
            if img.isNull():
                raise RuntimeError('无法打开 origin 图像')

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
                canvas.fill(QtGui.QColor(255,255,255,255))             # 外部为纯白不透明
                ox = (CANVAS_W - w) // 2
                oy = (CANVAS_H - h) // 2
                p = QtGui.QPainter(canvas)
                p.drawImage(ox, oy, subimg)                            # ROI 贴中间
                p.end()

                buf = QtCore.QBuffer(); buf.open(QtCore.QIODevice.ReadWrite)
                canvas.save(buf, "PNG"); png_bytes = bytes(buf.data()); buf.close()

                # 2) 掩膜：外部不透明，ROI 清成透明
                mask = QtGui.QImage(CANVAS_W, CANVAS_H, QtGui.QImage.Format_ARGB32)
                mask.fill(QtGui.QColor(255,255,255,255))
                mp = QtGui.QPainter(mask)
                mp.setCompositionMode(QtGui.QPainter.CompositionMode_Clear)
                mp.fillRect(QtCore.QRect(ox, oy, w, h), QtCore.Qt.transparent)
                mp.end()

                bufm = QtCore.QBuffer(); bufm.open(QtCore.QIODevice.ReadWrite)
                mask.save(bufm, "PNG"); mask_bytes = bytes(bufm.data()); bufm.close()

                # 调试：保存看看
                # QtGui.QImage.fromData(png_bytes).save(os.path.join(self.level_dir, "dbg_input.png"))
                # QtGui.QImage.fromData(mask_bytes).save(os.path.join(self.level_dir, "dbg_mask.png"))

                req = ImageEditRequester("input", png_bytes, mask_bytes, d.label)
                # 获取 AI 返回的图像字节
                img_bytes = req.send_request()
                patch = QtGui.QImage.fromData(img_bytes).copy(ox, oy, w, h)
                # 羽化宽度：短边的 6%（可按需调整/做成参数）
                feather_px = max(4, int(min(w, h) * 0.06))
                alpha8 = _make_rect_feather_alpha8(w, h, feather_px)

                # 直通 Alpha：避免预乘导致的整体泛灰
                patch_feathered = _apply_alpha_straight(patch, alpha8)
                final_path = os.path.join(self.level_dir, f"{self.name}_region{idx}.png")
                patch_feathered.save(final_path)

                step += 1
                self.progressed.emit(step, total)
            # compute failures: region files not present
            failed = []
            for idx in self.target_indices:
                dst = os.path.join(self.level_dir, f"{self.name}_region{idx}.png")
                if not os.path.isfile(dst):
                    failed.append(idx)
            self.finished.emit(failed)
        except Exception as exc:
            self.error.emit(str(exc))

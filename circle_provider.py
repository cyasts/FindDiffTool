# circle_provider.py
# 全局共享，不缩放：只按 level 加载一张 QPixmap（来自 qrc）
from PySide6 import QtGui
import img_rc


class CirclePixmapProvider:
    _inst = None

    @staticmethod
    def instance():
        if CirclePixmapProvider._inst is None:
            CirclePixmapProvider._inst = CirclePixmapProvider()
        return CirclePixmapProvider._inst

    def __init__(self):
        # level -> QPixmap
        self._base: dict[int, QtGui.QPixmap] = {}
        # 路径解析（: /img/c{level}.png）
        self._path_fn = lambda lvl: f":/img/c{int(lvl)}.png"

    def set_path_resolver(self, fn):
        """可选：自定义资源路径解析函数 fn(level)->str"""
        self._path_fn = fn or self._path_fn

    def preload_base(self) -> None:
        """一次性加载所有级别对应的 pixmap；失败也缓存空，避免反复 I/O。"""
        for lvl in range(1, 16):
            pm = QtGui.QPixmap(self._path_fn(lvl))
            self._base[lvl] = pm  # 即便是 null，也缓存，避免重复加载

    def get(self, level: int) -> QtGui.QPixmap | None:
        """取缓存 Pixmap；如未加载则立即加载一次（不缩放）。"""
        lvl = int(level)
        pm = self._base.get(lvl)
        if pm is None:
            pm = QtGui.QPixmap(self._path_fn(lvl))
            self._base[lvl] = pm
        return None if pm.isNull() else pm

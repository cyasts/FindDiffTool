from dataclasses import dataclass
from typing import Dict, List

from PySide6 import QtGui

# Discrete hint-circle radius levels (in natural pixels)
RADIUS_LEVELS: List[int] = [53, 59, 65, 71, 76, 81, 85, 90, 95, 100, 105, 110, 117, 124, 129]
# Min rectangle size (natural pixels)
MIN_RECT_SIZE: float = 110

CANVAS_W, CANVAS_H = 1024, 1024  # 4:3

CATEGORY_COLOR_MAP: Dict[str, QtGui.QColor] = {
    "增加茬点": QtGui.QColor("#ff7f50"),
    "情感": QtGui.QColor("#ff7f50"),
    "颜色": QtGui.QColor("#28a745"),
    "增强": QtGui.QColor("#6f42c1"),
    "置换": QtGui.QColor("#6c63ff"),
    "修改": QtGui.QColor("#ff9800"),
}


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
    hint_level: int = 1
    cx: float = -1.0
    cy: float = -1.0
    # click area
    click_customized: bool = False
    ccx: float = -1.0
    ccy: float = -1.0
    ca: float = 0.0  # 长轴（rect为半宽，ellipse为长轴）
    cb: float = 0.0  # 短轴（rect为半高，ellipse为短轴）
    cshape: str = "rect"  # 'rect' | 'ellipse' | 'None'

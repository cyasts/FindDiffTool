from dataclasses import dataclass

# Min rectangle size (natural pixels)
MIN_RECT_SIZE: float = 110

@dataclass
class Cat:
    id: str
    name: str
    enabled: bool
    visible: bool
    # rectangle stored in natural pixel coordinates
    x: float
    y: float
    width: float
    height: float
    # click area
    click_customized: bool = False
    ccx: float = -1.0
    ccy: float = -1.0
    ca: float = 0.0 #长轴（rect为半宽，ellipse为长轴）
    cb: float = 0.0 #短轴（rect为半高，ellipse为短轴）

    def set_rect(self, x, y, w, h) :
        self.x = x
        self.y = y
        self.width = w
        self.height = h

    def set_click_center(self, cx, cy):
        self.ccx = cx
        self.ccy = cy

    def set_click_axes(self, a, b):
        self.ca = a
        self.cb = b

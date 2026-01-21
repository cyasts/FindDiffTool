import os, json
import shutil, uuid
from typing import Dict, List, Optional, Tuple
from PySide6 import QtCore, QtGui, QtWidgets

from utils import compose_result, quantize_roi
from version import version
from models import Cat, MIN_RECT_SIZE
from scenes import ImageScene, ImageView
from graphics import CatItem

def now_id() -> str:
    return uuid.uuid4().hex

class EditorWindow(QtWidgets.QMainWindow):
    def __init__(self, pair, config_dir: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.pair = pair
        self.config_dir = config_dir
        self.setWindowTitle(f"不同点编辑器 - {self.pair.name} [v{version}]")
        self.resize(1600, 1080)
        self._add_btns = list()
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)

        # load images
        self.up_pix = QtGui.QPixmap(self.pair.image_path_a)
        self.down_pix = QtGui.QPixmap(self.pair.image_path_b)
        self.name = self.pair.name
        self.ext = os.path.splitext(os.path.basename(self.pair.image_path_a))[1]

        if self.up_pix.isNull() or self.down_pix.isNull():
            QtWidgets.QMessageBox.critical(self, "加载失败", "无法加载 A/B 图片")
            self.close()
            return

        if self.up_pix.size() != self.down_pix.size():
            QtWidgets.QMessageBox.critical(self, "加载失败", "A/B 图片尺寸不一致，无法编辑")
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
        self.up_scene = ImageScene(self.up_pix)
        self.down_scene = ImageScene(self.down_pix)
        self.up_view = ImageView(self.up_scene)
        self.down_view = ImageView(self.down_scene)

        self.toggle_click_region = QtWidgets.QCheckBox("显示点击区域")
        self.toggle_click_region.setChecked(False)
        self.toggle_regions = QtWidgets.QCheckBox("显示猫区域")
        self.toggle_regions.setChecked(True)

        # 主体：左侧上下图，右侧单一侧栏（仅用于下图添加）
        main_split = QtWidgets.QHBoxLayout()
        main_split.setContentsMargins(0, 0, 0, 0)
        main_split.setSpacing(8)

        left_col = QtWidgets.QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(8)
        left_col.addWidget(self.up_view, 1)
        left_col.addWidget(self.down_view, 1)

        left_wrap = QtWidgets.QWidget()
        left_wrap.setLayout(left_col)
        main_split.addWidget(left_wrap, 1)

        self.side_panel = self._build_side_panel(section='down')
        main_split.addWidget(self.side_panel, 0)
        vbox_root.addLayout(main_split, 1)

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
        self.total_count = QtWidgets.QLabel("猫总计：0")
        self.btn_save = QtWidgets.QPushButton("保存")
        self.btn_close = QtWidgets.QPushButton("关闭")

        self.btn_gen_click_region = QtWidgets.QPushButton("生成点击区域")
        self.btn_regen_circle = QtWidgets.QPushButton("生成")

        bottom_layout.addWidget(self.total_count)
        bottom_layout.addWidget(self.btn_save)
        bottom_layout.addWidget(self.btn_close)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self.btn_gen_click_region)
        bottom_layout.addWidget(self.btn_regen_circle)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self.toggle_click_region)
        bottom_layout.addWidget(self.toggle_regions)
        vbox_root.addWidget(bottom, 0)

        # Ensure vertical centering of buttons and controls
        for w in [self.total_count, self.btn_save, self.btn_close, self.btn_gen_click_region, self.btn_regen_circle,
                  self.toggle_click_region, self.toggle_regions]:
            bottom_layout.setAlignment(w, QtCore.Qt.AlignVCenter)

        self.status_bar = QtWidgets.QStatusBar(self)
        self.status_bar.setSizeGripEnabled(False)
        self.setStatusBar(self.status_bar)

        # data
        self.cats: List[Cat] = []
        self.rect_items_up: Dict[str, CatItem] = {}
        self.rect_items_down: Dict[str, CatItem] = {}
        self._syncing_rect_update: bool = False
        self.status: str = 'saved'
        # dirty state for title asterisk
        self._is_dirty: bool = False

        # wire
        self.btn_save.clicked.connect(self.on_save_clicked)
        self.btn_close.clicked.connect(self.close)

        self.btn_gen_click_region.clicked.connect(self.on_generate_click_regions)
        self.btn_regen_circle.clicked.connect(self.on_regen_circles)

        self.toggle_click_region.toggled.connect(self.refresh_visibility)
        self.toggle_regions.toggled.connect(self.refresh_visibility)

        # style buttons
        self.btn_save.setStyleSheet("QPushButton{background:#0d6efd;color:#fff;padding:6px 14px;border-radius:6px;border:1px solid #0d6efd;} QPushButton:hover{background:#0b5ed7;border-color:#0b5ed7;}")
        self.btn_close.setStyleSheet("QPushButton{background:#6c757d;color:#fff;padding:6px 14px;border-radius:6px;border:1px solid #6c757d;} QPushButton:hover{background:#545b62;border-color:#545b62;}")
        self.btn_gen_click_region.setStyleSheet("QPushButton{background:#17a2b8;color:#fff;padding:6px 14px;border-radius:6px;border:1px solid #17a2b8;} QPushButton:hover{background:#138496;border-color:#138496;}")
        self.btn_regen_circle.setStyleSheet("QPushButton{background:#17a2b8;color:#fff;padding:6px 14px;border-radius:6px;border:1px solid #17a2b8;} QPushButton:hover{background:#138496;border-color:#138496;}")
        self.total_count.setStyleSheet("color:#333;font-weight:500;")

        # initialize scenes/view
        QtCore.QTimer.singleShot(0, lambda: self.up_view.fitInView(self.up_scene.sceneRect(), QtCore.Qt.KeepAspectRatio))
        QtCore.QTimer.singleShot(0, lambda: self.down_view.fitInView(self.down_scene.sceneRect(), QtCore.Qt.KeepAspectRatio))

        # load existing config if exists
        self.load_existing_config()

        # initial count
        self.update_total_count()
        # initial status bar display
        self._update_window_title()
        # 若初始状态为完成，禁用交互
        # if getattr(self, 'status', None) == 'completed':
        #     self._set_completed_ui_disabled(True)

        # Shortcut: Cmd/Ctrl+S 保存
        self._sc_save = QtGui.QShortcut(QtGui.QKeySequence.Save, self)
        self._sc_save.activated.connect(self.on_save_clicked)

    def _update_window_title(self) -> None:
        mark = "*" if getattr(self, '_is_dirty', False) else ""
        self.setWindowTitle(f"找猫编辑器 - {self.pair.name} [v{version}]{mark}")

    def _make_dirty(self) -> None:
        self._is_dirty = True
        self._update_window_title()
        self._update_status('unsaved')

    def _update_status(self, status: str) -> None:
        self.status = status
        text_map = {
            'unsaved': '未保存',
            'saved': '已保存',
        }
        human = text_map.get(self.status, '未保存')
        self.status_bar.showMessage(f"状态：{human}")

    def update_total_count(self) -> None:
        self.total_count.setText(f"猫总计：{len(self.cats)}")

    # Side panel with tag buttons and list
    def _build_side_panel(self, section: str) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        panel.setFixedWidth(350)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 单按钮：统一添加到下图，类别固定为“修改”
        add_btn = QtWidgets.QPushButton("增加猫框")
        add_btn.setObjectName("btn_add_cat")
        add_btn.clicked.connect(self.add_cat)
        color = QtGui.QColor('#ff9800')
        add_btn.setStyleSheet(f"QPushButton {{ color: #fff; border:none; border-radius:14px; padding:6px 8px; background:{color.name()}; }}")
        add_btn.setFixedHeight(34)
        layout.addWidget(add_btn, 0)

        # list
        list_widget = QtWidgets.QListWidget()
        list_widget.setObjectName("list_down")
        # 取消 hover 同步高亮：仅保留选中高亮
        list_widget.setMouseTracking(False)
        list_widget.viewport().setMouseTracking(False)
        self.list_widget = list_widget
        layout.addWidget(list_widget, 1)

        return panel

    def add_cat(self) -> None:
        # 统一添加到下图
        w = self.up_pix.width()
        h = self.up_pix.height()

        size = min(w, h) * 0.2
        size = max(MIN_RECT_SIZE, size)
        margin = max(6.0, size * 0.05)

        x = margin
        y = h - margin - size   # 下图底部（图片局部坐标）


        cat = Cat(
            id=now_id(),
            name=f"序号 {len(self.cats) + 1}",
            enabled=True,
            visible=True,
            x=x,
            y=y,
            width=size,
            height=size,
            click_customized=False,
            ccx=x + size/2,
            ccy=y + size/2,
            ca=size/2,
            cb=size/2,
            cshape='rect'
        )
        self.cats.append(cat)
        self._add_rect_items(cat)
        self.rebuild_lists()
        self._make_dirty()
        self.update_total_count()


    def _on_item_chaned(self, cat_id: str) -> None:
        if self._syncing_rect_update:
            return
        self._syncing_rect_update = True
        try:
            it_up = self.rect_items_up.get(cat_id)
            it_down = self.rect_items_down.get(cat_id)
            if it_up:
                it_up.sync_from_model()
            if it_down:
                it_down.sync_from_model()
        finally:
            self._syncing_rect_update = False
        self._make_dirty()

    def _add_rect_items(self, cat: Cat) -> None:
        item_up = CatItem(cat, on_change=self._on_item_chaned, is_up=True)
        item_down = CatItem(cat, on_change=self._on_item_chaned, is_up=False)
        self.up_scene.addItem(item_up)
        self.down_scene.addItem(item_down)
        self.rect_items_up[cat.id] = item_up
        self.rect_items_down[cat.id] = item_down
        self.refresh_visibility()

    def rebuild_lists(self) -> None:
        self._rebuilding = True
        blocker = QtCore.QSignalBlocker(self.list_widget)
        try:
            self.list_widget.clear()

            # 7列：可见label | 可见checkbox | 序号label | 启用label | 启用checkbox | 删除label | 删除按钮
            COL_FIXED = {
                0: 80,  # "序号:x"
                1: 34,  # "可见:"
                2: 22,  # checkbox
                3: 34,  # "启用:"
                4: 22,  # checkbox
                5: 34,  # "删除:"
                6: 28,  # delete button
            }
            HSP = 6
            MARG = (6, 4, 6, 4)

            for idx, cat in enumerate(self.cats, start=1):
                item = QtWidgets.QListWidgetItem()
                item.setData(QtCore.Qt.UserRole, cat.id)

                row = QtWidgets.QWidget()
                gl = QtWidgets.QGridLayout(row)
                gl.setContentsMargins(*MARG)
                gl.setHorizontalSpacing(HSP)
                gl.setVerticalSpacing(0)

                # ---- controls (7) ----
                visibleLabel = QtWidgets.QLabel("可见:")
                visibleLabel.setStyleSheet("font-size:12px; color:#333;")

                visibled = QtWidgets.QCheckBox()
                visibled.setChecked(bool(cat.visible))
                visibled.setToolTip("显示/隐藏红框")
                visibled.toggled.connect(lambda checked, _id=cat.id: self.on_visibled_toggled(_id, checked))

                title = QtWidgets.QLabel(f"序号:{idx}")
                title.setStyleSheet("font-size:12px; font-weight:600; color:#333;")

                enableLabel = QtWidgets.QLabel("启用:")
                enableLabel.setStyleSheet("font-size:12px; color:#333;")

                enabled_box = QtWidgets.QCheckBox()
                enabled_box.setChecked(bool(cat.enabled))
                enabled_box.setToolTip("开启后可拖动/调整红框")
                enabled_box.toggled.connect(lambda checked, _id=cat.id: self.on_enabled_toggled(_id, checked))

                delLabel = QtWidgets.QLabel("删除:")
                delLabel.setStyleSheet("font-size:12px; color:#333;")

                btn_delete = QtWidgets.QToolButton()
                btn_delete.setToolTip("删除该茬点")
                btn_delete.setAutoRaise(True)
                btn_delete.setFixedSize(24, 24)
                btn_delete.setText("X")
                btn_delete.setStyleSheet(
                    "QToolButton{border:none;background:transparent;}"
                    "QToolButton:hover{background:rgba(220,53,69,0.12);border-radius:4px;}"
                )
                btn_delete.clicked.connect(lambda _=False, _id=cat.id: self.delete_cat_by_id(_id))

                # ---- sizing ----
                title.setFixedWidth(COL_FIXED[0])
                visibleLabel.setFixedWidth(COL_FIXED[1])
                visibled.setFixedWidth(COL_FIXED[2])
                enableLabel.setFixedWidth(COL_FIXED[3])
                enabled_box.setFixedWidth(COL_FIXED[4])
                delLabel.setFixedWidth(COL_FIXED[5])
                btn_delete.setFixedWidth(COL_FIXED[6])

                for wid in (visibleLabel, visibled, title, enableLabel, enabled_box, delLabel, btn_delete):
                    wid.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

                # ---- layout placement (row 0, col 0..6) ----
                gl.addWidget(title,        0, 0)
                gl.addWidget(visibleLabel, 0, 1)
                gl.addWidget(visibled,     0, 2)
                gl.addWidget(enableLabel,  0, 3)
                gl.addWidget(enabled_box,  0, 4)
                gl.addWidget(delLabel,     0, 5)
                gl.addWidget(btn_delete,   0, 6)

                # ---- column widths & stretch ----
                for col, wpx in COL_FIXED.items():
                    gl.setColumnMinimumWidth(col, wpx)
                    gl.setColumnStretch(col, 0)

                # 让 title 这一列吃一点多余空间（可选）
                gl.setColumnStretch(2, 1)

                # ---- size hint ----
                row_min_w = sum(COL_FIXED.values()) + HSP * (len(COL_FIXED) - 1) + MARG[0] + MARG[2]
                row.setMinimumWidth(row_min_w)
                row.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

                item.setSizeHint(row.sizeHint())
                self.list_widget.addItem(item)
                self.list_widget.setItemWidget(item, row)

            self.update_total_count()

        finally:
            self._rebuilding = False

        self._update_ordinals()


    def on_visibled_toggled(self, cat_id: str, checked: bool) -> None:
        cat = next((d for d in self.cats if d.id == cat_id), None)
        if not cat:
            return
        new_visible = bool(checked)
        if cat.visible == new_visible:
            return
        cat.visible = new_visible
        u = self.rect_items_up.get(cat.id)
        d = self.rect_items_down.get(cat.id)
        if u:
            u.setVisible(cat.visible)
        if d:
            d.setVisible(cat.visible)

    def on_enabled_toggled(self, cat_id: str, checked: bool) -> None:
        cat = next((d for d in self.cats if d.id == cat_id), None)
        if not cat:
            return

        cat.enabled = checked
        print("on_enabled_toggled:", cat.id, cat.enabled)

        u = self.rect_items_up.get(cat.id)
        d = self.rect_items_down.get(cat.id)
        if u:
            u.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, bool(cat.enabled))
            u.update()
        if d:
            d.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, bool(cat.enabled))
            d.update()
        self._make_dirty()
        self.update_total_count()
        self.refresh_visibility()

    def _set_all_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        for cat in self.cats:
            cat.enabled = enabled

        for it in self.rect_items_up.values():
            it.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, enabled)
            it.update()
        for it in self.rect_items_down.values():
            it.setFlag(QtWidgets.QGraphicsItem.ItemIsMovable, enabled)
            it.update()

        self.rebuild_lists()
        self.refresh_visibility()
        self._make_dirty()

    def delete_cat_by_id(self, cat_id: str) -> None:
        idx = next((i for i, d in enumerate(self.cats) if d.id == cat_id), -1)
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
        old_count = len(self.cats)

        d = self.cats.pop(idx)
        u = self.rect_items_up.pop(d.id, None)
        dn = self.rect_items_down.pop(d.id, None)
        if u:
            self.up_scene.removeItem(u)
            u.deleteLater()
        if dn:
            self.down_scene.removeItem(dn)
            dn.deleteLater()

        # 1) 删除对应输出图片，并重命名后续序号
        try:
            level_dir = self.level_dir()
            # 删除 {self.name}_region{deleted_index}.png
            victim = os.path.join(level_dir, f"A", f"{self.name}_region{deleted_index}.png")
            if os.path.isfile(victim):
                os.remove(victim)
            # 将 {self.name}_region{i}.png -> {self.name}_region{i-1}.png (i 从 deleted_index+1 到 old_count)
            for i in range(deleted_index + 1, old_count + 1):
                src = os.path.join(level_dir, f"A", f"{self.name}_region{i}.png")
                dst = os.path.join(level_dir, f"A", f"{self.name}_region{i-1}.png")
                if os.path.isfile(src):
                    # 若目标已存在（理论上不该发生），先移除目标以避免跨平台报错
                    if os.path.isfile(dst):
                        os.remove(dst)
                    shutil.move(src, dst)
        except Exception:
            # 静默处理文件系统异常，避免影响UI流
            pass

        # 2) 立即持久化当前配置与元信息（不做校验，避免未填写文本阻塞）
        self._write_config_snapshot()

        # 3) reindex titles by rebuilding lists
        self.rebuild_lists()
        self._make_dirty()


    def refresh_visibility(self) -> None:
        show_regions = self.toggle_regions.isChecked()
        show_click_region = self.toggle_click_region.isChecked()
        for d in (self.rect_items_up, self.rect_items_down):
            for item in d.values():
                item.setVis(show_click_region, show_regions)
                # item.updateEnabledFlags()

    def level_dir(self) -> str:
        # directory for this level
        return os.path.join(self.config_dir, f"{self.name}")

    def config_json_path(self) -> str:
        return os.path.join(self.level_dir(), f"A", f"config.json")

    def on_save_clicked(self) -> None:
        # Save now also performs pre-save validation
        self.save_config()
        self._is_dirty = False
        self._update_window_title()

    def on_regen_circles(self) -> None:
        if not self.cats:
            QtWidgets.QMessageBox.information(self, "提示", "当前没有可用的茬点，请先添加茬点。")
            return
        allowed_counts = {25, 30, 35, 40, 50}
        if len(self.cats) not in allowed_counts:
            QtWidgets.QMessageBox.information(
                self,
                "提示",
                f"当前茬点数为 {len(self.cats)}，仅支持 25、30、35、40、50 个，请调整后再生成。"
            )
            return
        progress = QtWidgets.QProgressDialog("正在生成，请稍候...", None, 0, 0, self)
        progress.setWindowModality(QtCore.Qt.ApplicationModal)
        progress.setCancelButton(None)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 50)

        level_dir = self.level_dir()
        success = False
        try:
            os.makedirs(os.path.join(level_dir, "A"), exist_ok=True)
            os.makedirs(os.path.join(level_dir, "B"), exist_ok=True)

            # 统一从 B 图裁剪，作为最终区域图
            src_b_path = self.pair.image_path_b
            src_b = QtGui.QImage(src_b_path)
            if src_b.isNull():
                QtWidgets.QMessageBox.warning(self, "生成失败", f"无法读取 B 图：{src_b_path}")
                return

            progress.setLabelText("正在裁剪区域...")
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 50)

            for idx, d in enumerate(self.cats, start=1):
                W, H = src_b.width(), src_b.height()
                l, t, w, h = quantize_roi(d.x, d.y, d.width, d.height, W, H)
                cropped = src_b.copy(int(l), int(t), int(w), int(h))
                out_path = os.path.join(level_dir, "A", f"{self.name}_region{idx}.png")
                cropped.save(out_path)
                QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 50)

            progress.setLabelText("正在生成预览...")
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 50)

            # 使用裁剪结果生成预览/绿圈四宫格
            compose_result(level_dir, self.name, self.ext, self.cats)
            success = True
            QtWidgets.QMessageBox.information(self, "生成完成", "区域图与预览已生成")
        finally:
            progress.close()

        if success:
            self._set_all_enabled(False)

    def on_generate_click_regions(self) -> None:
        # 生成点击区域
        if not self.cats:
            QtWidgets.QMessageBox.information(self, "提示", "当前没有可用的茬点，请先添加茬点。")
            return

        # 生成点击区域逻辑
        for d in self.cats:
            if not d.click_customized:
                d.click_customized = True
                d.ccx = d.x + d.width / 2
                d.ccy = d.y + d.height / 2
                d.ca = d.width / 2
                d.cb = d.height / 2
                d.cshape = 'rect'

        cur = self.toggle_click_region.isChecked()
        self.toggle_click_region.setChecked(not cur)
        self.refresh_visibility()
        self.toggle_click_region.setChecked(cur)
        self.refresh_visibility()

        self._make_dirty()

    def save_config(self) -> None:

        self._update_status('saved')
        self._write_config_snapshot()
        file_name = self.name
        file_ext = self.ext or ".png"
        level_dir = self.level_dir()
        os.makedirs(os.path.join(level_dir, "B"), exist_ok=True)
        os.makedirs(os.path.join(level_dir, "A"), exist_ok=True)
        # copy A 图作为 origin
        try:
            src_img = self.pair.image_path_a
            if os.path.isfile(src_img):
                dst_img = os.path.join(level_dir, "A", f"{file_name}_origin{file_ext}")
                if not os.path.exists(dst_img):
                    shutil.copy2(src_img, dst_img)
        except Exception:
            pass

        QtWidgets.QMessageBox.information(self, "成功", f"配置保存成功\n")

    def _write_config_snapshot(self) -> None:
        """Write current cats to config.json without validation or UI side-effects.
        Keeps the on-disk config in sync after deletions/renames.
        """
        # natural size = scene size
        w = self.up_scene.width()
        h = self.up_scene.height()

        def to_percent_y_bottom(y_px: float) -> float:
            return 1.0 - (y_px / h)

        def to_percent_x(x_px: float) -> float:
            return x_px / w

        file_name = self.name
        file_ext = self.ext
        data = {
            "imageName": f"{file_name}_origin{file_ext}",
            "imageWidth":int(self.up_scene.width()),
            "imageHeight": int(self.up_scene.height()),
            "status": self.status,
            "caterenceCount": len(self.cats),
            "differences": []
        }
        for idx, d in enumerate(self.cats):
            points = [
                {"x": to_percent_x(d.x), "y": to_percent_y_bottom(d.y)},
                {"x": to_percent_x(d.x + d.width), "y": to_percent_y_bottom(d.y)},
                {"x": to_percent_x(d.x + d.width), "y": to_percent_y_bottom(d.y + d.height)},
                {"x": to_percent_x(d.x), "y": to_percent_y_bottom(d.y + d.height)},
            ]
            # compute hint circle from stored local center and radius
            # local center -> absolute
            ccx = to_percent_x(d.ccx)
            ccy = to_percent_y_bottom(d.ccy)
            ccx = max(0.0, min(1.0, ccx))
            ccy = max(0.0, min(1.0, ccy))


            entry = {
                "id": d.id,
                "name": d.name,
                "replaceImage": f"{file_name}_region{idx+1}.png",
                "enabled": bool(d.enabled),
                "points": points,
                "click_customized": bool(d.click_customized),  # 只存标记
            }
            if d.click_customized:
                entry.update({
                    "click_x": ccx,
                    "click_y": ccy,
                    "click_a": d.ca,
                    "click_b": d.cb,
                    "click_type": d.cshape
                })

            data["differences"].append(entry)

        os.makedirs(self.level_dir(), exist_ok=True)
        cfg_path = self.config_json_path()
        os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
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
        cfg_path = os.path.join(dir_path, "A", "config.json")
        if not os.path.isfile(cfg_path):
            # treat as a new blank level
            self._clear_all_items()
            self.cats.clear()
            self.rebuild_lists()
            self.update_total_count()
            self._is_dirty = False
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
        self._update_status(cfg.get('status', "saved"))
        self.cats.clear()
        for cat in cfg.get('differences', []):
            points = cat.get('points', [])
            if len(points) < 4:
                continue
            xs = [from_percent_x(p['x']) for p in points]
            ys = [from_percent_y_bottom(p['y']) for p in points]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)

            w_rect = max(MIN_RECT_SIZE, max_x - min_x)
            h_rect = max(MIN_RECT_SIZE, max_y - min_y)

            # 3) 二次点击区域（自定义 vs 回退）
            #    兼容两种判断：显式标记 或 字段存在即视为自定义
            click_customized = bool(cat.get('click_customized', False))
            has_click_fields = ('click_x' in cat and 'click_y' in cat and
                                'click_a' in cat and 'click_b' in cat)
            use_custom = click_customized and has_click_fields
            shape = str(cat.get('click_type', 'rect'))  # 'rect' | 'ellipse' | 'circle'(如有)

            if use_custom:
                # click_x/click_y 为百分比(0~1)，反归一化为像素；a/b 按当前写法为像素半轴
                ccx_abs = from_percent_x(float(cat.get('click_x', 0.0)))
                ccy_abs = from_percent_y_bottom(float(cat.get('click_y', 0.0)))
                ca = float(cat.get('click_a', 0.0))
                cb = float(cat.get('click_b', 0.0))
            else:
                ccx_abs = min_x + w_rect/2.0
                ccy_abs = min_y + h_rect/2.0
                ca = w_rect/2.0
                cb = h_rect/2.0

            d = Cat(
                id=str(cat.get('id', now_id())),
                name=str(cat.get('name', f"不同点 {len(self.cats) + 1}")),
                enabled=bool(cat.get('enabled', True)),
                visible=True,
                x=min_x,
                y=min_y,
                width=max(MIN_RECT_SIZE, max_x - min_x),
                height=max(MIN_RECT_SIZE, max_y - min_y),
                click_customized=use_custom,
                cshape=shape,
                ccx=float(ccx_abs),
                ccy=float(ccy_abs),
                ca=float(ca),
                cb=float(cb)
            )
            self.cats.append(d)
            self._add_rect_items(d)

        self.rebuild_lists()
        self.update_total_count()
        self._is_dirty = False
        self._update_window_title()


    def _update_ordinals(self) -> None:
        """按 self.cats 当前顺序为每个图元设置 1-based 序号。"""
        for idx, d in enumerate(self.cats, start=1):
            it_up   = self.rect_items_up.get(d.id)
            it_down = self.rect_items_down.get(d.id)
            if it_up:   it_up.setOrdinal(idx)
            if it_down: it_down.setOrdinal(idx)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        # 未保存时提示
        if getattr(self, '_is_dirty', False):
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

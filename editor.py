import os, json, math
import shutil, uuid
from typing import Dict, List, Optional, Tuple
from PySide6 import QtCore, QtGui, QtWidgets

from utils import compose_result, quantize_roi
from models import Difference, RADIUS_LEVELS, MIN_RECT_SIZE,CATEGORY_COLOR_MAP
from scenes import ImageScene, ImageView
from graphics import DifferenceItem

def now_id() -> str:
    return uuid.uuid4().hex

def clamp_level(level: int) -> int:
    """把 level 夹到 1..len(RADIUS_LEVELS)。"""
    n = len(RADIUS_LEVELS)
    try:
        lvl = int(level)
    except Exception:
        lvl = 1
    return 1 if lvl < 1 else (n if lvl > n else lvl)

class DifferenceEditorWindow(QtWidgets.QMainWindow):
    def __init__(self, pair, config_dir: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.pair = pair
        self.config_dir = config_dir
        self.setWindowTitle(f"不同点编辑器 - {self.pair.name}")
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
        self.toggle_regions = QtWidgets.QCheckBox("显示茬图区域")
        self.toggle_regions.setChecked(True)
        self.toggle_hints = QtWidgets.QCheckBox("显示绿圈")
        self.toggle_hints.setChecked(True)

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
        self.total_count = QtWidgets.QLabel("茬点总计：0")
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
        bottom_layout.addWidget(self.toggle_hints)
        vbox_root.addWidget(bottom, 0)

        # Ensure vertical centering of buttons and controls
        for w in [self.total_count, self.btn_save, self.btn_close, self.btn_gen_click_region, self.btn_regen_circle,
                  self.toggle_click_region, self.toggle_regions, self.toggle_hints]:
            bottom_layout.setAlignment(w, QtCore.Qt.AlignVCenter)

        self.status_bar = QtWidgets.QStatusBar(self)
        self.status_bar.setSizeGripEnabled(False)
        self.setStatusBar(self.status_bar)

        # data
        self.differences: List[Difference] = []
        self.rect_items_up: Dict[str, DifferenceItem] = {}
        self.rect_items_down: Dict[str, DifferenceItem] = {}
        self._syncing_rect_update: bool = False
        self._syncing_selection: bool = False
        self._suppress_scene_selection: bool = False
        self.status: str = 'unsaved'
        # dirty state for title asterisk
        self._is_dirty: bool = False
        self._selected_diff_id: Optional[str] = None

        # wire
        self.btn_save.clicked.connect(self.on_save_clicked)
        self.btn_close.clicked.connect(self.close)

        self.btn_gen_click_region.clicked.connect(self.on_generate_click_regions)
        self.btn_regen_circle.clicked.connect(self.on_regen_circles)

        self.toggle_click_region.toggled.connect(self.refresh_visibility)
        self.toggle_regions.toggled.connect(self.refresh_visibility)
        self.toggle_hints.toggled.connect(self.refresh_visibility)

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
        self.setWindowTitle(f"不同点编辑器 - {self.pair.name}{mark}")

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
        self.total_count.setText(f"茬点总计：{len(self.differences)}")

    # Side panel with tag buttons and list
    def _build_side_panel(self, section: str) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        panel.setFixedWidth(350)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 单按钮：统一添加到下图，类别固定为“修改”
        add_btn = QtWidgets.QPushButton("增加茬点")
        add_btn.setObjectName("btn_add_diff")
        add_btn.clicked.connect(lambda _=False: self.add_difference('down', '修改'))
        color = CATEGORY_COLOR_MAP.get('修改', QtGui.QColor('#ff0000'))
        add_btn.setStyleSheet(f"QPushButton {{ color: #fff; border:none; border-radius:14px; padding:6px 8px; background:{color.name()}; }}")
        add_btn.setFixedHeight(34)
        layout.addWidget(add_btn, 0)

        # list
        list_widget = QtWidgets.QListWidget()
        list_widget.setObjectName("list_down")
        # 支持单选用于高亮
        list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        list_widget.itemSelectionChanged.connect(self.on_list_selection_changed)
        # 取消 hover 同步高亮：仅保留选中高亮
        list_widget.setMouseTracking(False)
        list_widget.viewport().setMouseTracking(False)
        layout.addWidget(list_widget, 1)

        return panel

    def current_list(self, section: str) -> QtWidgets.QListWidget:
        # 统一使用下侧列表
        return self.findChild(QtWidgets.QListWidget, "list_down")

    def add_difference(self, section: str, category: str) -> None:
        # 统一添加到下图
        section = 'down'
        scene = self.down_scene
        r = scene.sceneRect()
        size = min(r.width(), r.height()) * 0.2
        size = max(MIN_RECT_SIZE, size)
        margin = max(6.0, size * 0.05)  # 给一点内边距，手感更好

        rect = QtCore.QRectF(r.left() + margin, r.bottom() - margin - size, size, size)
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
            cx=rect.center().x(),
            cy=rect.center().y(),
            hint_level=1,
            click_customized=False,
            ccx=rect.center().x(),
            ccy=rect.center().y(),
            ca=rect.width() / 2,
            cb=rect.height() / 2,
            cshape='rect'
        )
        self.differences.append(diff)
        self._add_rect_items(diff)
        self.rebuild_lists()
        self._make_dirty()
        self.update_total_count()


    def _on_item_chaned(self, diff_id: str)->None:
        self._make_dirty()

    def _add_rect_items(self, diff: Difference) -> None:
        color = CATEGORY_COLOR_MAP.get(diff.category, QtGui.QColor('#ff0000'))
        item_up = DifferenceItem(diff, color, on_change=self._on_item_chaned, is_up=True)
        item_down = DifferenceItem(diff, color, on_change=self._on_item_chaned, is_up=False)
        self.up_scene.addItem(item_up)
        self.down_scene.addItem(item_down)
        self.rect_items_up[diff.id] = item_up
        self.rect_items_down[diff.id] = item_down
        self.refresh_visibility()

    def rebuild_lists(self) -> None:
        down = self.current_list('down')

        # ==== 屏蔽信号 + 标记重建中 ====
        self._rebuilding = True
        block_down = QtCore.QSignalBlocker(down)
        try:
            # 1) 清空
            if down:
                down.clear()

            # === 列宽配置 ===
            # 顺序：可见 | 标题 | 等级 | 启用 | 删除
            COL_FIXED = {0: 22, 1: 60, 2: 64, 3: 22, 4: 28}
            HSP = 6
            MARG = (6, 4, 6, 4)

            global_idx = 1
            for diff in self.differences:
                lw = self.current_list('down')
                if lw is None:
                    break
                color = CATEGORY_COLOR_MAP.get(diff.category, QtGui.QColor('#ff0000'))

                item = QtWidgets.QListWidgetItem()
                item.setData(QtCore.Qt.UserRole, diff.id)

                w = QtWidgets.QWidget()
                gl = QtWidgets.QGridLayout(w)
                gl.setContentsMargins(*MARG)
                gl.setHorizontalSpacing(HSP)

                title = QtWidgets.QLabel(f"茬点{global_idx}")
                title.setStyleSheet(f"color:{color.name()}; font-size:12px; font-weight:600;")

                visibled = QtWidgets.QCheckBox()
                visibled.setChecked(diff.visible)
                visibled.setToolTip("显示/隐藏红框")
                visibled.toggled.connect(lambda checked, _id=diff.id: self.on_visibled_toggled(_id, checked))

                level_combo = QtWidgets.QComboBox()
                level_combo.setObjectName(f"level_{diff.id}")
                # 填入 1..len(RADIUS_LEVELS)
                for i in range(1, len(RADIUS_LEVELS) + 1):
                    level_combo.addItem(str(i), i)
                # 初始选中
                safe_level = clamp_level(diff.hint_level)
                level_combo.setCurrentIndex(safe_level - 1)
                # 联动：索引变 -> 级别 = index+1
                level_combo.currentIndexChanged.connect(
                    lambda idx, _id=diff.id: self.on_level_changed(_id, idx + 1)
                )
                # 简单样式（可要可不要）
                level_combo.setFixedWidth(64)
                level_combo.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

                enabled_box = QtWidgets.QCheckBox()
                enabled_box.setChecked(diff.enabled)
                enabled_box.setToolTip("开启后可拖动/调整红框")
                enabled_box.toggled.connect(lambda checked, _id=diff.id: self.on_enabled_toggled(_id, checked))

                btn_delete = QtWidgets.QToolButton()
                btn_delete.setToolTip("删除该茬点")
                btn_delete.setAutoRaise(True)
                btn_delete.setFixedSize(24, 24)
                try:
                    btn_delete.setText("X")
                    btn_delete.setIconSize(QtCore.QSize(14, 14))
                except Exception:
                    btn_delete.setText("X")
                btn_delete.setStyleSheet(
                    "QToolButton{border:none;background:transparent;}"
                    "QToolButton:hover{background:rgba(220,53,69,0.12);border-radius:4px;}"
                )
                btn_delete.clicked.connect(lambda _=False, _id=diff.id: self.delete_diff_by_id(_id))

                # ---- 尺寸策略 ----
                for wid in (visibled, enabled_box, btn_delete, title, level_combo):
                    wid.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)

                visibled.setFixedWidth(COL_FIXED[0])
                title.setFixedWidth(COL_FIXED[1])
                level_combo.setFixedWidth(COL_FIXED[2])
                enabled_box.setFixedWidth(COL_FIXED[3])
                btn_delete.setFixedWidth(24)

                for col, wpx in COL_FIXED.items():
                    gl.setColumnMinimumWidth(col, wpx)
                    gl.setColumnStretch(col, 0)
                gl.setColumnStretch(2, 1)

                gl.addWidget(visibled,     0, 0)
                gl.addWidget(title,        0, 1)
                gl.addWidget(level_combo,  0, 2)
                gl.addWidget(enabled_box,  0, 3)
                gl.addWidget(btn_delete,   0, 4)

                ncols = 5
                row_min_w = sum(COL_FIXED.values()) + HSP*(ncols-1) + MARG[0] + MARG[2]
                w.setMinimumWidth(row_min_w)
                w.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

                w.setLayout(gl)
                item.setSizeHint(w.sizeHint())
                lw.addItem(item)
                lw.setItemWidget(item, w)
                global_idx += 1

            self.update_total_count()

            # 重建后默认不选中任何行（避免触发回调后的选中联动）
            if down:
                down.clearSelection(); down.setCurrentRow(-1)

        finally:
            self._rebuilding = False
            # QSignalBlocker 离开作用域自动恢复信号

        # 如需恢复到之前的选中，放在解除屏蔽之后执行（可选）
        if self._selected_diff_id is not None:
            self._set_selected_diff(self._selected_diff_id)

        self._update_ordinals()


    def on_list_selection_changed(self) -> None:
        if getattr(self, '_rebuilding', False) or self._syncing_selection:
            return
        # reflect list selection to scene items (both up/down)
        if self._syncing_selection:
            return
        lw = self.sender()
        if not isinstance(lw, QtWidgets.QListWidget):
            return
        item = lw.currentItem()
        diff_id = item.data(QtCore.Qt.UserRole) if item else None
        self._set_selected_diff(diff_id)

    def on_visibled_toggled(self, diff_id: str, checked: bool) -> None:
        diff = next((d for d in self.differences if d.id == diff_id), None)
        if not diff:
            return
        new_visible = bool(checked)
        if diff.visible == new_visible:
            return
        diff.visible = new_visible
        u = self.rect_items_up.get(diff.id)
        d = self.rect_items_down.get(diff.id)
        if u:
            u.setVisible(diff.visible)
        if d:
            d.setVisible(diff.visible)

    def on_enabled_toggled(self, diff_id: str, checked: bool) -> None:
        diff = next((d for d in self.differences if d.id == diff_id), None)
        if not diff:
            return

        diff.enabled = checked
        print("on_enabled_toggled:", diff.id, diff.enabled)

        u = self.rect_items_up.get(diff.id)
        d = self.rect_items_down.get(diff.id)
        if u:
            u.model.data.enabled = diff.enabled
            u.updateEnabledFlags()
        if d:
            d.model.data.enabled = diff.enabled
            d.updateEnabledFlags()
        self._make_dirty()
        self.update_total_count()
        self.refresh_visibility()

    def _sync_diff_enabled_to_items(self, diff: Difference) -> None:
        """同步 enabled 状态到上下两个红框图元。"""
        for it in (self.rect_items_up.get(diff.id), self.rect_items_down.get(diff.id)):
            if it:
                it.model.data.enabled = bool(diff.enabled)
                it.updateEnabledFlags()

    def _set_all_enabled(self, enabled: bool) -> None:
        """批量开关所有红框的交互能力。"""
        changed = False
        for diff in self.differences:
            if diff.enabled != enabled:
                diff.enabled = enabled
                changed = True
            self._sync_diff_enabled_to_items(diff)
        if changed:
            self.rebuild_lists()
            self._make_dirty()

    def on_label_changed(self, diff_id: str, text: str) -> None:
        diff = next((d for d in self.differences if d.id == diff_id), None)
        if not diff:
            return
        diff.label = text
        u = self.rect_items_up.get(diff.id)
        d = self.rect_items_down.get(diff.id)
        self._make_dirty()
        if u:
            u.updateLabel()
        if d:
            d.updateLabel()

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
        if self._selected_diff_id == diff_id:
            self._set_selected_diff(None)

    def _set_selected_diff(self, diff_id: Optional[str]) -> None:
        """按照 diff.section 只在对应区域选中，并同步对应场景的高亮。
        diff_id 为 None 时清空两侧列表与场景高亮。
        """
        prev_id = self._selected_diff_id
        if prev_id == diff_id:
            return

        # 1) 取消旧选中态（两侧都清一次，安全）
        if prev_id is not None:
            for mapping in (self.rect_items_up, self.rect_items_down):
                it = mapping.get(prev_id)
                if it:
                    it.setExternalSelected(False, raise_z=False)

        # 2) 记录新选中
        self._selected_diff_id = diff_id

        # diff_id 为 None：清空两侧列表选中并返回
        if diff_id is None:
            self._syncing_selection = True
            try:
                for section in ("up", "down"):
                    lw = self.current_list(section)
                    if lw:
                        lw.clearSelection()
                        lw.setCurrentRow(-1)
            finally:
                self._syncing_selection = False
            return

        # 3) 查找 diff，拿到它的 section
        target_diff = next((d for d in self.differences if d.id == diff_id), None)
        target_section = target_diff.section if target_diff else None

        # 4) 同步左右两侧列表的选中行：只在目标 section 选中，另一侧清空
        self._syncing_selection = True
        try:
            for section in ("up", "down"):
                lw = self.current_list(section)
                if lw is None:
                    continue

                if section != target_section:
                    lw.clearSelection()
                    lw.setCurrentRow(-1)
                    continue

                # 在目标侧定位并选中对应行
                row = -1
                for i in range(lw.count()):
                    it = lw.item(i)
                    if it and it.data(QtCore.Qt.UserRole) == diff_id:
                        row = i
                        break
                lw.setCurrentRow(row)
        finally:
            self._syncing_selection = False

        # 5) 设置场景图元高亮：只在目标侧设置，另一侧保持未选
        if target_diff:
            mapping = self.rect_items_up if target_section == "up" else self.rect_items_down
            it = mapping.get(diff_id)
            if it:
                it.setExternalSelected(True, raise_z=True)


    def refresh_visibility(self) -> None:
        show_regions = self.toggle_regions.isChecked()
        show_hints = self.toggle_hints.isChecked()
        show_labels = False
        show_click_region = self.toggle_click_region.isChecked()
        for d in (self.rect_items_up, self.rect_items_down):
            for item in d.values():
                item.setVis(show_click_region, show_regions, show_hints, show_labels)
                item.updateEnabledFlags()

    def level_dir(self) -> str:
        # directory for this level
        return os.path.join(self.config_dir, f"{self.name}")

    def config_json_path(self) -> str:
        return os.path.join(self.level_dir(), f"A", f"config.json")


    def validate_before_save(self) -> Tuple[bool, Optional[str]]:
        # circle overlap <= 10%
        # compute circle geometries in the same (natural) coordinate space
        circles = []
        for d in self.differences:
            r_w, r_h = d.width, d.height
            cx_abs = d.cx if d.cx >= 0 else r_w / 2.0
            cy_abs = d.cy if d.cy >= 0 else r_h / 2.0
            lvl = d.hint_level
            radius_px = float(RADIUS_LEVELS[lvl - 1])
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
        self.save_config()
        self._is_dirty = False
        self._update_window_title()

    def on_level_changed(self, diff_id: str, new_level: int) -> None:
        """列表里切换 hint level（1..15）。更新 dataclass，并驱动场景图元重绘。"""
        diff = next((d for d in self.differences if d.id == diff_id), None)
        if not diff:
            return
        lvl = clamp_level(new_level)
        if diff.hint_level == lvl:
            return

        # 1) 写回 dataclass
        diff.hint_level = lvl

        # 2) 通知两个场景的对应 DifferenceItem 重绘（并尽量发信号以便其刷新边界/shape）
        for it in (self.rect_items_up.get(diff.id), self.rect_items_down.get(diff.id)):
            if not it:
                continue
            try:
                # 模型包的是同一个 dataclass，这里为了让视图可靠刷新，显式通知
                it.model.data.hint_level = lvl
                it.model.anyChanged.emit(self)   # 触发 _on_model_any_changed -> update()
            except Exception:
                it.update()

        # 3) UI 脏
        self._make_dirty()

    def on_regen_circles(self) -> None:
        if not self.differences:
            QtWidgets.QMessageBox.information(self, "提示", "当前没有可用的茬点，请先添加茬点。")
            return
        allowed_counts = {15, 20, 25, 30, 35}
        if len(self.differences) not in allowed_counts:
            QtWidgets.QMessageBox.information(
                self,
                "提示",
                f"当前茬点数为 {len(self.differences)}，仅支持 15、20、25、30、35 个，请调整后再生成。"
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

            for idx, d in enumerate(self.differences, start=1):
                W, H = src_b.width(), src_b.height()
                l, t, w, h = quantize_roi(d.x, d.y, d.width, d.height, W, H)
                cropped = src_b.copy(int(l), int(t), int(w), int(h))
                out_path = os.path.join(level_dir, "A", f"{self.name}_region{idx}.png")
                cropped.save(out_path)
                QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 50)

            progress.setLabelText("正在生成预览...")
            QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 50)

            # 使用裁剪结果生成预览/绿圈四宫格
            compose_result(level_dir, self.name, self.ext, self.differences)
            success = True
            QtWidgets.QMessageBox.information(self, "生成完成", "区域图与预览已生成")
        finally:
            progress.close()

        if success:
            self._set_all_enabled(False)

    def on_generate_click_regions(self) -> None:
        # 生成点击区域
        if not self.differences:
            QtWidgets.QMessageBox.information(self, "提示", "当前没有可用的茬点，请先添加茬点。")
            return

        # 生成点击区域逻辑
        for d in self.differences:
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

        file_name = self.name
        file_ext = self.ext
        data = {
            "imageName": f"{file_name}_origin{file_ext}",
            "imageWidth":int(self.up_scene.width()),
            "imageHeight": int(self.up_scene.height()),
            "status": self.status,
            "differenceCount": len(self.differences),
            "differences": []
        }
        for idx, d in enumerate(self.differences):
            points = [
                {"x": to_percent_x(d.x), "y": to_percent_y_bottom(d.y)},
                {"x": to_percent_x(d.x + d.width), "y": to_percent_y_bottom(d.y)},
                {"x": to_percent_x(d.x + d.width), "y": to_percent_y_bottom(d.y + d.height)},
                {"x": to_percent_x(d.x), "y": to_percent_y_bottom(d.y + d.height)},
            ]
            # compute hint circle from stored local center and radius
            # local center -> absolute
            cx = to_percent_x(d.cx)
            cy = to_percent_y_bottom(d.cy)
            cx = max(0.0, min(1.0, cx))
            cy = max(0.0, min(1.0, cy))

            ccx = to_percent_x(d.ccx)
            ccy = to_percent_y_bottom(d.ccy)
            ccx = max(0.0, min(1.0, ccx))
            ccy = max(0.0, min(1.0, ccy))

            lvl = d.hint_level
            # 从 hint level 获取半径（修正list越界问题）
            if isinstance(lvl, int) and 1 <= lvl <= len(RADIUS_LEVELS):
                radius = RADIUS_LEVELS[lvl - 1]
            else:
                radius = 0

            entry = {
                "id": d.id,
                "name": d.name,
                "section": ('down' if d.section == 'down' else 'up'),
                "category": d.category or "",
                "replaceImage": f"{file_name}_region{idx+1}.png",
                "enabled": bool(d.enabled),
                "points": points,
                "hintLevel": int(lvl),
                "circleCenter": {"x": cx, "y": cy},
                "circleRadius": radius,
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
            self.differences.clear()
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
        self._update_status(cfg.get('status', "unsaved"))
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
            
            w_rect = max(MIN_RECT_SIZE, max_x - min_x)
            h_rect = max(MIN_RECT_SIZE, max_y - min_y)
            raw_level = diff.get('hintLevel', 1)
            lvl = clamp_level(raw_level)

            # 3) 二次点击区域（自定义 vs 回退）
            #    兼容两种判断：显式标记 或 字段存在即视为自定义
            click_customized = bool(diff.get('click_customized', False))
            has_click_fields = ('click_x' in diff and 'click_y' in diff and
                                'click_a' in diff and 'click_b' in diff)
            use_custom = click_customized and has_click_fields
            shape = str(diff.get('click_type', 'rect'))  # 'rect' | 'ellipse' | 'circle'(如有)

            if use_custom:
                # click_x/click_y 为百分比(0~1)，反归一化为像素；a/b 按当前写法为像素半轴
                ccx_abs = from_percent_x(float(diff.get('click_x', 0.0)))
                ccy_abs = from_percent_y_bottom(float(diff.get('click_y', 0.0)))
                ca = float(diff.get('click_a', 0.0))
                cb = float(diff.get('click_b', 0.0))
            else:
                ccx_abs = min_x + w_rect/2.0
                ccy_abs = min_y + h_rect/2.0
                ca = w_rect/2.0
                cb = h_rect/2.0

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
                hint_level=int(lvl),
                cx=float(cpx),
                cy=float(cpy),
                click_customized=use_custom,
                cshape=shape,
                ccx=float(ccx_abs),
                ccy=float(ccy_abs),
                ca=float(ca),
                cb=float(cb)
            )
            self.differences.append(d)
            self._add_rect_items(d)

        self.rebuild_lists()
        self.update_total_count()


    def _update_ordinals(self) -> None:
        """按 self.differences 当前顺序为每个图元设置 1-based 序号。"""
        for idx, d in enumerate(self.differences, start=1):
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

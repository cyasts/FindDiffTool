import os, json, math
import shutil, time, uuid
from typing import Dict, List, Optional, Tuple
from PySide6 import QtCore, QtGui, QtWidgets

from utils import compose_result
from models import Difference, RADIUS_LEVELS, MIN_RECT_SIZE,CATEGORY_COLOR_MAP
from scenes import ImageScene, ImageView
from graphics import DifferenceItem
from ai import AIWorker

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
    def _set_completed_ui_disabled(self, disabled: bool):
        # 禁用保存、AI处理按钮
        self.btn_save.setEnabled(not disabled)
        self.btn_submit.setEnabled(not disabled)
        self.up_side.setEnabled(not disabled)
        self.down_side.setEnabled(not disabled)

    def __init__(self, pair, config_dir: str, parent: Optional[QtWidgets.QWidget] = None) -> None:
        super().__init__(parent)
        self.pair = pair
        self.config_dir = config_dir
        self.setWindowTitle(f"不同点编辑器 - {self.pair.name}")
        # self.resize(1600, 1080)
        self.resize(1200, 960)
        self._add_btns = list()
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)

        # load images
        self.up_pix = QtGui.QPixmap(self.pair.image_path)
        self.down_pix = QtGui.QPixmap(self.pair.image_path)
        self.name = self.pair.name
        self.ext = os.path.splitext(os.path.basename(self.pair.image_path))[1]

        if self.up_pix.isNull() or self.down_pix.isNull():
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
        self.toggle_labels = QtWidgets.QCheckBox("显示茬点文本")
        self.toggle_labels.setChecked(True)
        self.toggle_ai_preview = QtWidgets.QCheckBox("AI预览")
        self.toggle_ai_preview.setChecked(False)
        self.toggle_ai_preview.setEnabled(True)

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
        # vbox_root.addLayout(down_row, 1)

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

        self.btn_gen_click_region = QtWidgets.QPushButton("生成点击区域")
        self.btn_regen_circle = QtWidgets.QPushButton("重贴绿圈")

        self.api_combo = QtWidgets.QComboBox()
        self.api_combo.addItem("A81", "A81")
        self.api_combo.addItem("A82", "A82")
        self.api_combo.addItem("香港", "HK")
        self.api_combo.addItem("美国", "US")
        self.api_combo.setCurrentIndex(0)

        self.client_combo = QtWidgets.QComboBox()
        self.client_combo.addItem("A8", "A8")
        self.client_combo.addItem("Gemini", "Gemini")
        self.client_combo.setCurrentIndex(0)
        self.client_combo.currentIndexChanged.connect(self.on_client_changed)

        bottom_layout.addWidget(self.total_count)
        bottom_layout.addWidget(self.btn_save)
        bottom_layout.addWidget(self.btn_close)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self.client_combo)
        bottom_layout.addWidget(self.api_combo)
        bottom_layout.addWidget(self.btn_submit)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self.btn_gen_click_region)
        bottom_layout.addWidget(self.btn_regen_circle)
        bottom_layout.addStretch(1)
        bottom_layout.addWidget(self.toggle_click_region)
        bottom_layout.addWidget(self.toggle_regions)
        bottom_layout.addWidget(self.toggle_hints)
        bottom_layout.addWidget(self.toggle_labels)
        bottom_layout.addWidget(self.toggle_ai_preview)
        vbox_root.addWidget(bottom, 0)

        # Ensure vertical centering of buttons and controls
        for w in [self.total_count, self.btn_save, self.client_combo, self.api_combo, self.btn_submit, self.btn_close, self.btn_regen_circle,
                  self.toggle_click_region, self.toggle_regions, self.toggle_hints, self.toggle_labels, self.toggle_ai_preview]:
            bottom_layout.setAlignment(w, QtCore.Qt.AlignVCenter)

        self.status_bar = QtWidgets.QStatusBar(self)
        self.status_bar.setSizeGripEnabled(False)
        self.setStatusBar(self.status_bar)

        # data
        self.differences: List[Difference] = []
        self.rect_items_up: Dict[str, DifferenceItem] = {}
        self.rect_items_down: Dict[str, DifferenceItem] = {}
        # AI 预览覆盖图层（仅当勾选 AI预览 时显示）
        self.ai_overlays_up: QtWidgets.QGraphicsPixmapItem = QtWidgets.QGraphicsPixmapItem()
        self.ai_overlays_up.setZValue(1)
        self.ai_overlays_up.setOffset(0, 0)
        self.ai_overlays_up.setPos(0, 0)
        self.ai_overlays_up.setAcceptedMouseButtons(QtCore.Qt.NoButton) # 不拦截鼠标
        self.ai_overlays_up.setCacheMode(QtWidgets.QGraphicsItem.DeviceCoordinateCache)
        self.ai_overlays_up.setTransformationMode(QtCore.Qt.SmoothTransformation)
        self.ai_overlays_up.setVisible(False)
        self.up_scene.addItem(self.ai_overlays_up)
        
        self.ai_overlays_down: QtWidgets.QGraphicsPixmapItem = QtWidgets.QGraphicsPixmapItem()
        self.ai_overlays_down.setZValue(1)
        self.ai_overlays_down.setOffset(0, 0)
        self.ai_overlays_down.setPos(0, 0)
        self.ai_overlays_down.setAcceptedMouseButtons(QtCore.Qt.NoButton) # 不拦截鼠标
        self.ai_overlays_down.setCacheMode(QtWidgets.QGraphicsItem.DeviceCoordinateCache)
        self.ai_overlays_down.setTransformationMode(QtCore.Qt.SmoothTransformation)
        self.ai_overlays_down.setVisible(False)
        self.down_scene.addItem(self.ai_overlays_down)

        self._refresh_ai_overlays()
        self._syncing_rect_update: bool = False
        self._syncing_selection: bool = False
        self._suppress_scene_selection: bool = False
        self.status: str = 'unsaved'
        # dirty state for title asterisk
        self._is_dirty: bool = False
        self._selected_diff_id: Optional[str] = None

        # wire
        self.btn_save.clicked.connect(self.on_save_clicked)
        self.btn_submit.clicked.connect(self.on_ai_process)
        self.btn_close.clicked.connect(self.close)

        self.btn_gen_click_region.clicked.connect(self.on_generate_click_regions)
        self.btn_regen_circle.clicked.connect(self.on_regen_circles)

        self.toggle_click_region.toggled.connect(self.refresh_visibility)
        self.toggle_regions.toggled.connect(self.refresh_visibility)
        self.toggle_hints.toggled.connect(self.refresh_visibility)
        self.toggle_labels.toggled.connect(self.refresh_visibility)
        self.toggle_ai_preview.toggled.connect(self.on_toggle_ai_preview)

        # style buttons
        self.btn_save.setStyleSheet("QPushButton{background:#0d6efd;color:#fff;padding:6px 14px;border-radius:6px;border:1px solid #0d6efd;} QPushButton:hover{background:#0b5ed7;border-color:#0b5ed7;}")
        self.btn_submit.setStyleSheet("QPushButton{background:#28a745;color:#fff;padding:6px 14px;border-radius:6px;border:1px solid #28a745;} QPushButton:hover{background:#1e7e34;border-color:#1e7e34;}")
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
            'saved': '待AI处理',
            'aiPending': 'AI处理中',
            'completed': '完成',
            'hasError': 'AI处理存在错误',
        }
        human = text_map.get(self.status)
        self.status_bar.showMessage(f"状态：{human}")

    def update_total_count(self) -> None:
        count = sum(1 for d in self.differences if d.enabled)
        self.total_count.setText(f"茬点总计：{len(self.differences)}, 已勾选AI处理:{count}项")
        # 没有茬点时，不允许进行AI处理
        try:
            self.btn_submit.setEnabled(count > 0)
        except Exception:
            pass

    def on_client_changed(self, index: int) -> None:
        client = self.client_combo.itemData(index)
        if client == "Gemini":
            # Gemini 不支持 AI 预览
            self.api_combo.setEnabled(False)
        else:
            self.api_combo.setEnabled(True)

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
        # Use a modal-looking progress dialog (non-blocking updates via signals)
        self._ai_dlg = QtWidgets.QProgressDialog("正在上传AI处理...", None, 0, max(1, int(total)), self)
        self._ai_dlg.setWindowModality(QtCore.Qt.WindowModal)
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

    def _ai_progress_end(self) -> None:
        try:
            if getattr(self, '_ai_timer', None) is not None:
                self._ai_timer.stop()
                self._ai_timer.deleteLater()
        except Exception:
            pass
        # close dialog if used
        try:
            self._ai_dlg.close()
        except Exception:
            pass
        self._ai_dlg = None

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
        self._ai_dlg.setMaximum(max(1, int(total)))
        self._ai_dlg.setValue(int(step))

    @QtCore.Slot(list)
    def _ai_slot_finished(self, failed: list) -> None:
        self._ai_progress_end()
        self._cleanup_ai_thread()
        for d in self.differences:
            d.enabled = False
        self.rebuild_lists()
        self.refresh_visibility()
        self.update_total_count()

        compose_result(self.level_dir(), self.name, self.ext, self.differences)
        self._refresh_ai_overlays()
        if failed:
            self._update_status('hasError')
            QtWidgets.QMessageBox.information(self, "AI处理", "AI已完成处理, 但存在错误")
        else:
            self._update_status('completed')
            QtWidgets.QMessageBox.information(self, "AI处理", "AI已完成处理")
        self._write_config_snapshot()

        

    @QtCore.Slot(str)
    def _ai_slot_error(self, msg: str) -> None:
        self._ai_progress_end()
        self._cleanup_ai_thread()
        QtWidgets.QMessageBox.critical(self, "AI处理失败", msg)
        self._update_status('hasError')

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
        up = self.current_list('up')
        down = self.current_list('down')

        # ==== 屏蔽信号 + 标记重建中 ====
        self._rebuilding = True
        block_up = QtCore.QSignalBlocker(up)
        block_down = QtCore.QSignalBlocker(down)
        try:
            # 1) 清空
            for section in ('up', 'down'):
                lw = self.current_list(section)
                lw.clear()

            # === 列宽配置 ===
            COL_FIXED = {0: 18, 1: 40, 3: 64, 4: 18, 5: 15}
            EDIT_MIN = 100
            HSP = 6
            MARG = (6, 4, 6, 4)

            global_idx = 1
            for diff in self.differences:
                lw = self.current_list(diff.section)
                color = CATEGORY_COLOR_MAP.get(diff.category, QtGui.QColor('#ff0000'))

                item = QtWidgets.QListWidgetItem()
                item.setData(QtCore.Qt.UserRole, diff.id)

                w = QtWidgets.QWidget()
                gl = QtWidgets.QGridLayout(w)
                gl.setContentsMargins(*MARG)
                gl.setHorizontalSpacing(HSP)

                title = QtWidgets.QLabel(f"茬点{global_idx}")
                title.setStyleSheet(f"color:{color.name()}; font-size:12px; font-weight:600;")

                edit = QtWidgets.QLineEdit()
                edit.setText(diff.label)
                edit.textChanged.connect(lambda text, _id=diff.id: self.on_label_changed(_id, text))

                enabled = QtWidgets.QCheckBox()
                enabled.setChecked(diff.enabled)
                enabled.stateChanged.connect(lambda _state, _id=diff.id: self.on_enabled_toggled(_id, bool(_state)))

                visibled = QtWidgets.QCheckBox()
                visibled.setChecked(diff.visible)
                visibled.stateChanged.connect(lambda _state, _id=diff.id: self.on_visibled_toggled(_id))

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
                for wid in (visibled, enabled, btn_delete, title, level_combo):
                    wid.setSizePolicy(QtWidgets.QSizePolicy.Fixed, QtWidgets.QSizePolicy.Fixed)
                edit.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

                visibled.setFixedWidth(COL_FIXED[0])
                title.setFixedWidth(COL_FIXED[1])
                level_combo.setFixedWidth(COL_FIXED[3])
                enabled.setFixedWidth(COL_FIXED[4])
                btn_delete.setFixedWidth(COL_FIXED[5])

                for col, wpx in COL_FIXED.items():
                    gl.setColumnMinimumWidth(col, wpx)
                    gl.setColumnStretch(col, 0)
                gl.setColumnMinimumWidth(2, EDIT_MIN)
                gl.setColumnStretch(2, 1)

                gl.addWidget(visibled,     0, 0)
                gl.addWidget(title,        0, 1)
                gl.addWidget(edit,         0, 2)
                gl.addWidget(level_combo, 0, 3)
                gl.addWidget(enabled,      0, 4)
                gl.addWidget(btn_delete,   0, 5)

                ncols = 6
                row_min_w = sum(COL_FIXED.values()) + EDIT_MIN + HSP*(ncols-1) + MARG[0] + MARG[2]
                w.setMinimumWidth(row_min_w)
                w.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

                w.setLayout(gl)
                item.setSizeHint(w.sizeHint())
                lw.addItem(item)
                lw.setItemWidget(item, w)
                global_idx += 1

            self.update_total_count()

            # 重建后默认不选中任何行（避免触发回调后的选中联动）
            up.clearSelection();   up.setCurrentRow(-1)
            down.clearSelection(); down.setCurrentRow(-1)

        finally:
            self._rebuilding = False
            # QSignalBlocker 离开作用域自动恢复信号

        # 如需恢复到之前的选中，放在解除屏蔽之后执行（可选）
        if self._selected_diff_id is not None:
            self._set_selected_diff(self._selected_diff_id)

        self._update_ordinals()


    def _update_radius_value_for_label(self, diffid: str, r: float) -> None:
        label = getattr(self, 'radius_labels', {}).get(diffid)
        if not label:
            return
        val = int(math.floor(r + 0.5))   # 避免 round() 的银行家舍入
        label.setText(f"半径:{val}")

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

    def on_visibled_toggled(self, diff_id: str) -> None:
        diff = next((d for d in self.differences if d.id == diff_id), None)
        if not diff:
            return
        diff.visible = not diff.visible
        u = self.rect_items_up.get(diff.id)
        d = self.rect_items_down.get(diff.id)
        if u:
            u.setVisible(diff.visible)
        if d:
            d.setVisible(diff.visible)

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

        # 1) 删除对应 AI 输出图片，并重命名后续序号
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
        show_labels = self.toggle_labels.isChecked()
        show_click_region = self.toggle_click_region.isChecked()
        for d in (self.rect_items_up, self.rect_items_down):
            for item in d.values():
                item.setVis(show_click_region, show_regions, show_hints, show_labels)
                item.updateEnabledFlags()

    # === AI 预览覆盖 ===
    def on_toggle_ai_preview(self) -> None:
        if self.toggle_ai_preview.isChecked():
            self.ai_overlays_up.setVisible(True)
            self.ai_overlays_down.setVisible(True)
        else:
            self.ai_overlays_up.setVisible(False)
            self.ai_overlays_down.setVisible(False)

    def _refresh_ai_overlays(self) -> None:
        # rebuild overlays from disk according to current differences order
        level_dir = self.level_dir()
        up_path = os.path.join(level_dir, "B", "composite_up.png")
        down_path = os.path.join(level_dir, "B", "composite_down.png")

        # 若 up/down 不存在，就现做一次
        need_build = (not os.path.isfile(up_path)) or (not os.path.isfile(down_path))
        if need_build :
            w = self.up_pix.width()
            h = self.up_pix.height()
            pixup = QtGui.QPixmap(w, h)
            pixup.fill(QtCore.Qt.transparent)
            self.ai_overlays_up.setPixmap(pixup)
            pixdown = QtGui.QPixmap(w, h)
            pixdown.fill(QtCore.Qt.transparent)
            self.ai_overlays_down.setPixmap(pixdown)
        else:
            pixup = QtGui.QPixmap(up_path)
            pixdown = QtGui.QPixmap(down_path)

        self.ai_overlays_up.setPixmap(pixup)
        self.ai_overlays_down.setPixmap(pixdown)

    # removed spin count UI

    def level_dir(self) -> str:
        # directory for this level
        return os.path.join(self.config_dir, f"{self.name}")

    def config_json_path(self) -> str:
        return os.path.join(self.level_dir(), f"A", f"config.json")


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

    def on_ai_process(self) -> None:
        # 保存前置：未保存则禁止处理
        if getattr(self, '_is_dirty', False) or self.status == 'unsaved':
            QtWidgets.QMessageBox.information(self, "提示", "当前修改尚未保存，请先保存后再进行AI处理。")
            return
        # Step 1: alidation
        # 限制茬点数量：仅当茬点数为 15/20/25 时允许AI处理
        allowed_counts = {15, 20, 25, 30}
        current_count = len(self.differences)
        if current_count not in allowed_counts:
            QtWidgets.QMessageBox.information(
                self,
                "AI处理",
                f"当前茬点数为 {current_count}，仅支持 15、20、25 或 30 个，请调整后重试。"
            )
            return

        targets: List[int] = []
        for idx, d in enumerate(self.differences, start=1):
            if d.enabled:
                targets.append(idx)
        if not targets:
            QtWidgets.QMessageBox.information(self, "AI处理", "未勾选茬点，请勾选要处理的茬点")
            return
        self._update_status("aiPending")
        # Step 3: 在后台线程执行AI，并显示非阻塞进度对话框
        # 准备 origin 路径
        level_dir = self.level_dir()

        # 状态栏进度
        self._ai_progress_start(len(targets))

        # 后台线程
        self._ai_thread = QtCore.QThread(self)
        client = self.client_combo.currentData()
        self._ai_worker = AIWorker(level_dir, self.name, self.ext, self.differences, targets)
        api = self.api_combo.currentData()
        self._ai_worker.setClient(client, api)
        self._ai_worker.moveToThread(self._ai_thread)
        self._ai_thread.started.connect(self._ai_worker.run)
        # Ensure slots execute on GUI thread
        self._ai_worker.progressed.connect(self._ai_slot_progress, QtCore.Qt.QueuedConnection)
        self._ai_worker.finished.connect(self._ai_slot_finished, QtCore.Qt.QueuedConnection)
        self._ai_worker.error.connect(self._ai_slot_error, QtCore.Qt.QueuedConnection)
        self._ai_thread.finished.connect(self._ai_thread.deleteLater)
        self._ai_thread.start()

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
        # 重新生成圆形区域（未自定义的）
        if not self.differences:
            QtWidgets.QMessageBox.information(self, "提示", "当前没有可用的茬点，请先添加茬点。")
            return
        compose_result(self.level_dir(), self.name, self.ext, self.differences)
        QtWidgets.QMessageBox.information(self, "绿圈贴图", "重新贴图完成")

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
        file_ext = self.ext
        str = self.level_dir()
        os.makedirs(os.path.join(str, f"B"), exist_ok=True)
        os.makedirs(os.path.join(str, f"A"), exist_ok=True)
        # copy original image into level dir and rename as origin.{ext}
        try:
            src_img = self.pair.image_path
            if os.path.isfile(src_img):
                _, ext = os.path.splitext(src_img)
                if not ext:
                    ext = '.png'
                dst_img = os.path.join(self.level_dir(), f"A", f'{file_name}_origin{file_ext}')
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
                "label": d.label or "",
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
            self.toggle_ai_preview.setEnabled(False)
            self.toggle_ai_preview.setChecked(False)
            self.ai_overlays_up.setVisible(False)
            self.ai_overlays_down.setVisible(False)
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

        # refresh AI overlays based on current differences
        if self.toggle_ai_preview.isChecked():
            self._refresh_ai_overlays()

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





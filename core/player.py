"""主播放器模块：VeryGoodPlayer 主窗口（MusicPlayer 类）。

由原 main.py 按功能拆分而来，承载主窗口类；程序入口见 main.py。
"""
import sys
import os
import re
import json
import random
import time
from datetime import datetime

from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *

from core.utils import (
    app_data_dir,
    config_dir,
    mascot_dir,
    resource_path,
    image_path,
    reg_theme,
    _theme_color,
    _set_theme,
    _css_global,
    read_embedded_cover,
    _THEME_WIDGETS,
    NeteaseAPI,
    HAS_NETEASE,
    TOPLIST_IDS,
    HAS_PYGAME,
    HAS_REQUESTS,
    HAS_MUTAGEN,
    pygame,
    requests,
    MutagenFile,
)
from ui.widgets import (
    LoadingOverlay,
    ScrollLabel,
    MediaButton,
    ClickableSlider,
    _PlaylistListWidget,
    TablePlayButton,
    GenericThread,
    ColorSwatch,
)
from ui.mascot import MascotWindow
from ui.detail_panel import DetailPanel

class MusicPlayer(QMainWindow):
    def __init__(self):
        super().__init__()

        # ---------- 屏幕自适应 ----------
        screen = QApplication.primaryScreen()
        size = screen.availableGeometry()
        screen_w, screen_h = size.width(), size.height()
        base_w, base_h = 1920, 1080
        scale = min(screen_w / base_w, screen_h / base_h, 1.2)
        self.scale = max(scale, 0.6)

        # ---------- 窗口尺寸 ----------
        win_w = int(1000 * self.scale)
        win_h = int(700 * self.scale)
        x = int((screen_w - win_w) / 2)
        y = int((screen_h - win_h) / 2)
        self.setGeometry(x, y, win_w, win_h)
        self.setMinimumSize(int(860*self.scale), int(560*self.scale))
        self._resize_margin = int(6 * self.scale)  # 无边框窗口边缘缩放热区宽度
        self.setWindowTitle("VeryGoodPlayer")
        # iOS风格：无边框、透明背景（配合中央容器圆角）
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowMinimizeButtonHint | Qt.WindowSystemMenuHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # ---------- UI 偏好配置（如收藏列表排序方式、看板娘资源包、下载目录） ----------
        # 需先于 local_folder 初始化，因为下载目录可配置
        self.settings_file = os.path.join(config_dir(), "settings.json")
        self._favorites_sort = "time_desc"  # 收藏列表排序方式，默认收藏时间倒序
        self._local_sort = "name_asc"       # 本地下载列表排序方式，默认曲名正序
        self._playlist_sort = {}            # 各歌单的排序方式：{歌单名: 排序模式}
        self._card_sort = "mtime_desc"      # 卡片页歌单排序方式，默认修改时间倒序
        self._playlist_card_map = {}        # 重命名后自动滚动定位用：{歌单名: 卡片widget}
        self.volume = 80                    # 音量（0-100），默认 80，持久化
        self.last_menu = None               # 上次播放/浏览的列表键（session 恢复）
        self.last_playlist = []             # 上次列表快照（session 恢复）
        self.last_playing = None            # 上次播放的歌曲（session 恢复，不含进度）
        self.last_browse_menu = None        # 上次浏览的侧栏键（session 恢复）
        self.cache_max_songs = 20           # 缓存清理：最多保留歌曲缓存数
        self.cache_max_mb = 300             # 缓存清理：缓存总大小上限（MB）
        self.mascot_pack_id = "Celia"       # 看板娘资源包 id（mascot/<id>/）
        self.mascot_enabled = True          # 看板娘开关，默认开启
        self.splash_enabled = True          # 开屏画面开关，默认开启
        self.download_dir = os.path.join(app_data_dir(), "songs")  # 下载目录，可配置
        # 看板娘细节配置（持久化，与开关 mascot_enabled 互不冲突）
        self.mascot_pos = None              # 上次摆放位置 {x, y}；None 则默认右下角
        self.mascot_float = True            # 待机浮动，默认开启
        self.mascot_topmost = True          # 总在最前，默认开启
        self.mascot_controls = True         # 看板娘控制小组件开关，默认开启
        self.theme_color = "#EC4141"         # 主题色（全局），可在设置中更改
        self._pending_theme = None           # 设置页待应用的主题色（按“应用”后生效）
        self._themed_widgets = []            # 主题化控件登记表：(widget, css模板)
        self._migrate_legacy_config()        # 旧版根目录配置迁移到 config/ 子目录
        self._load_settings()

        # ---------- 固定本地文件夹（用户数据，打包后位于 exe 同级） ----------
        self.local_folder = self.download_dir
        # 一次性迁移：旧版 Downloads 目录重命名为 songs，避免已有下载数据丢失
        old_downloads = os.path.join(app_data_dir(), "Downloads")
        if (os.path.normpath(self.local_folder) != os.path.normpath(old_downloads)
                and not os.path.exists(self.local_folder)
                and os.path.isdir(old_downloads)):
            try:
                os.rename(old_downloads, self.local_folder)
                print(f"📁 已迁移旧 Downloads 目录 -> songs")
            except Exception as e:
                print(f"⚠️ 迁移 Downloads 目录失败：{e}")
        if not os.path.exists(self.local_folder):
            os.makedirs(self.local_folder)
            print(f"📁 已创建本地音乐文件夹：{self.local_folder}")

        # ---------- 资源文件夹（只读资源，打包后从 _MEIPASS 解压目录读取） ----------
        self.icons_folder = resource_path(os.path.join("resources", "icons"))
        if not os.path.exists(self.icons_folder):
            if getattr(sys, 'frozen', False):
                # 打包环境中资源目录为只读临时目录，不应创建，仅提示
                print(f"⚠️ 未找到图标资源文件夹：{self.icons_folder}（打包时需包含 resources/icons 资源）")
            else:
                os.makedirs(self.icons_folder)
                print(f"📁 已创建 resources/icons 文件夹，请放入图标资源")
        # 窗口图标（只读资源）
        icon_p = image_path("icon.png")
        if os.path.exists(icon_p):
            self.setWindowIcon(QIcon(icon_p))

        # ---------- 收藏管理器 ----------
        self.fav_file = os.path.join(config_dir(), "favorites.json")
        self._favorites = []
        self._load_favorites()

        # ---------- 自定义歌单 ----------
        self.playlist_file = os.path.join(config_dir(), "playlists.json")
        self._playlists = {}
        self._load_playlists()

        # ---------- 歌单数据 ----------
        self.playlist_data = {}
        self.current_menu = None
        self.current_playing_row = -1
        self._playing_menu = None
        self._playing_row = -1
        self._panel_queue = []  # 播放列表面板独立队列，不受 playlist_data 影响
        self._panel_queue_source = None  # 队列快照来源菜单名
        self._song_ready = False
        self._song_loading = False
        # 歌曲 + 封面并行加载协调器：两者都就绪后再一起呈现（前台不卡）
        self._pending_prepare = None
        self._recommended_loaded = False  # 猜你喜欢：是否已加载过推荐数据
        self._recommended_shown_ids = set()  # 猜你喜欢：上一批已推荐的歌曲 ID（换一批时优先排除）
        self._recommend_loading = False   # 猜你喜欢：推荐请求是否正在后台进行（防重复触发）
        self._search_loading = False     # 搜索：搜索请求是否正在后台进行（防重复触发）
        self.current_song_duration = 0
        self.current_start_pos = 0  # 播放起始偏移量（秒）
        self._trial_end_sec = 0     # 试听结束秒数（0 表示完整歌曲）
        self._slider_prev_sec = 0   # 滑块按下时实际播放位置（秒），用于越界回弹
        self.play_mode = 0          # 播放模式：0=列表循环 1=单曲循环 2=随机播放
        self._shuffle_queue = []    # 随机播放剩余索引队列
        self._skip_retry_count = 0  # 无版权自动跳过计数器
        self._toplist_viewing_songs = False  # 排行榜是否正在显示歌曲（而非列表）
        self._toplist_loading = False   # 排行榜：歌曲是否正在后台加载（防重复请求）
        self._download_loading = False  # 下载：是否正在后台下载（防重复）
        self._loading_owner = None      # 当前遮罩归属的加载器标识（search/toplist/recommend/download）
        self._page_scroll = {}          # 各侧栏页面保存的滚动位置（切出/切回时保留浏览位置）
        self._pending_scroll_restore = False  # 切回侧栏后是否待恢复滚动位置

        # ---------- 在线音乐缓存与 API ----------
        self.cache_folder = os.path.join(self.local_folder, ".cache")
        if HAS_NETEASE:
            try:
                self.online_api = NeteaseAPI(cache_dir=self.cache_folder)
                # 应用用户配置的缓存清理标准（设置页可改）
                self.online_api._MAX_CACHE_SONGS = self.cache_max_songs
                self.online_api._MAX_CACHE_MB = self.cache_max_mb
                print("✅ 网易云音乐 API 初始化成功")
            except Exception as e:
                self.online_api = None
                print(f"⚠️ 网易云 API 初始化失败：{e}")
        else:
            self.online_api = None

        # ---------- 初始化 pygame.mixer ----------
        if HAS_PYGAME:
            pygame.mixer.init()
            print("✅ pygame.mixer 初始化成功")

        # ---------- 主布局 ----------
        central_widget = QWidget()
        central_widget.setObjectName("centralContainer")
        central_widget.setStyleSheet(f"""
            #centralContainer {{
                background-color: #FFFFFF;
                border-radius: {int(12*self.scale)}px;
            }}
            /* 极简滚动条（细条、圆角、hover 显示） */
            QScrollBar:vertical {{
                width: 6px; margin: 0; border: none; background: transparent;
            }}
            QScrollBar::handle:vertical {{
                background: #D0D0D0; border-radius: 3px; min-height: 20px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: #AAAAAA;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QScrollBar:horizontal {{
                height: 6px; margin: 0; border: none; background: transparent;
            }}
            QScrollBar::handle:horizontal {{
                background: #D0D0D0; border-radius: 3px; min-width: 20px;
            }}
            QScrollBar::handle:horizontal:hover {{
                background: #AAAAAA;
            }}
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
                width: 0;
            }}
        """)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ---------- 自定义标题栏 ----------
        title_bar_h = int(32 * self.scale)
        self.title_bar = QWidget()
        self.title_bar.setFixedHeight(title_bar_h)
        self.title_bar.setStyleSheet(f"background-color: #EAEAEF; border-top-left-radius: {int(12*self.scale)}px; border-top-right-radius: {int(12*self.scale)}px; border: none;")
        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(int(12*self.scale), 0, int(8*self.scale), 0)
        # 拖拽区域占位（字号与主表格同步）
        self._drag_label = QLabel("VeryGoodPlayer")
        self._drag_label.setStyleSheet(f"""
            font-size: {int(14*self.scale)}px; color: #1A1A1A;
            background: transparent; border: none;
        """)
        title_layout.addWidget(self._drag_label)
        title_layout.addSpacing(int(12*self.scale))
        # 最小化按钮
        self._btn_min = QPushButton("—")
        self._btn_min.setFixedSize(int(22*self.scale), int(22*self.scale))
        self._btn_min.setCursor(Qt.PointingHandCursor)
        self._btn_min.setStyleSheet(f"""
            QPushButton {{ background: transparent; border: none;
                font-size: {int(12*self.scale)}px; color: #1A1A1A;
                border-radius: {int(11*self.scale)}px;
            }}
            QPushButton:hover {{ background-color: #E0E0E0; color: #1A1A1A; }}
        """)
        self._btn_min.clicked.connect(self._on_minimize_clicked)
        title_layout.addStretch(1)
        # 最大化/还原按钮（位于最小化与关闭之间，随窗口状态切换图标）
        self._btn_max = QPushButton()
        self._btn_max.setFixedSize(int(22*self.scale), int(22*self.scale))
        self._btn_max.setCursor(Qt.PointingHandCursor)
        self._btn_max.setStyleSheet(self._btn_min.styleSheet()
                                    + f"QPushButton {{ padding-top: {int(2*self.scale)}px; }}")
        self._btn_max.setIconSize(QSize(int(14*self.scale), int(14*self.scale)))
        self._sync_max_btn_icon()  # 初始为普通窗口状态：square 图标
        self._btn_max.clicked.connect(self._toggle_maximize)
        # 关闭按钮
        self._btn_close = QPushButton("✕")
        self._btn_close.setFixedSize(int(28*self.scale), int(28*self.scale))
        self._btn_close.setCursor(Qt.PointingHandCursor)
        reg_theme(self._btn_close, f"""
            QPushButton {{ background: transparent; border: none;
                font-size: {int(14*self.scale)}px; color: #1A1A1A;
                border-radius: {int(14*self.scale)}px;
            }}
            QPushButton:hover {{ background-color: #EC4141; color: #FFFFFF; }}
        """)
        self._btn_close.clicked.connect(self.close)
        title_layout.addWidget(self._btn_min)
        title_layout.addSpacing(int(8*self.scale))
        title_layout.addWidget(self._btn_max)
        # 关闭按钮盒子(28)比另两个(22)宽 6px 且图标居中，视觉空隙多 3px，
        # 间距补偿 (28-22)/2，使三按钮视觉间距一致
        title_layout.addSpacing(int(8*self.scale) - 3)
        title_layout.addWidget(self._btn_close)
        # 标题栏可拖拽 / 双击最大化还原
        self.title_bar.mousePressEvent = self._titlebar_mouse_press
        self.title_bar.mouseMoveEvent = self._titlebar_mouse_move
        self.title_bar.mouseDoubleClickEvent = self._titlebar_mouse_dblclick

        # ---------- 主体 ----------
        self.body_widget = QWidget()
        body_layout = QHBoxLayout(self.body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        # 左侧导航
        self.left_menu = QListWidget()
        left_width = int(200 * self.scale)
        self.left_menu.setFixedWidth(left_width)
        font_size_menu = int(15 * self.scale)
        padding_menu = int(12 * self.scale)
        reg_theme(self.left_menu, f"""
            QListWidget {{
                background-color: #1A1A1A;
                border: none;
                padding-top: {int(20*self.scale)}px;
                font-size: {font_size_menu}px;
                color: #B3B3B3;
                outline: 0;
            }}
            QListWidget::item {{
                padding: {padding_menu}px {int(20*self.scale)}px;
                border-left: {int(3*self.scale)}px solid transparent;
            }}
            QListWidget::item:hover {{
                background-color: #2A2A2A;
                color: #FFFFFF;
            }}
            QListWidget::item:selected {{
                background-color: #1A1A1A;
                color: #EC4141;
                border-left: {int(3*self.scale)}px solid #EC4141;
            }}
        """)
        # 侧栏菜单：显示文本去掉 emoji，改用 resources/icons/1-6.svg 图标；
        # 完整键（含 emoji）存入 Qt.UserRole，内部 playlist_data/逻辑判断不变
        # 排行榜置顶：作为默认侧栏（setCurrentRow(0) 默认打开第一项）
        self._menu_specs = [
            ("排行榜", "📊 排行榜", "5.svg"),
            ("发现音乐", "🎧 发现音乐", "1.svg"),
            ("猜你喜欢", "🎯 猜你喜欢", "2.svg"),
            ("我喜欢的音乐", "❤️ 我喜欢的音乐", "3.svg"),
            ("我的歌单", "📋 我的歌单", "4.svg"),
            ("本地下载", "📁 本地下载", "6.svg"),
            ("设置", "⚙ 设置", "7.svg"),
        ]
        self.left_menu.setIconSize(QSize(int(22 * self.scale), int(22 * self.scale)))
        for label, key, svg in self._menu_specs:
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, key)
            icon = self._render_menu_icon(svg)
            if icon is not None:
                it.setIcon(icon)
            self.left_menu.addItem(it)

        # 右侧面板（工具栏 + 歌曲表格）
        self.right_panel = QWidget()
        # 加载遮罩（覆盖整个右侧面板）
        self.loading_overlay = LoadingOverlay(self.right_panel)
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # ---------- 工具栏 ----------
        self.toolbar = QWidget()
        toolbar_height = int(44 * self.scale)
        self.toolbar.setFixedHeight(toolbar_height)
        self.toolbar.setStyleSheet("background-color: #F5F5F7; border-bottom: 1px solid #E5E5E5;")
        self.toolbar_layout = QHBoxLayout(self.toolbar)
        self.toolbar_layout.setContentsMargins(int(10*self.scale), 0, int(10*self.scale), 0)

        font_size_tool = int(13 * self.scale)
        # 搜索模式选择（歌名/歌手/专辑，默认歌名）——按钮文本天然居中
        self._search_mode = "song"
        self._search_seq = 0  # 搜索请求序号：切换模式自动重搜时，丢弃过期结果避免旧结果覆盖
        self.search_mode_btn = QPushButton("歌名")
        self.search_mode_btn.setFixedWidth(int(66 * self.scale))
        self.search_mode_btn.setCursor(Qt.PointingHandCursor)
        self.search_mode_btn.setToolTip("选择搜索字段")
        # 模式菜单：列表样式与应用内 QMenu 完全一致（去掉系统阴影）
        self._search_mode_menu = QMenu(self.toolbar)
        self._search_mode_menu.setWindowFlags(
            self._search_mode_menu.windowFlags() | Qt.FramelessWindowHint)
        reg_theme(self._search_mode_menu, f"""
            QMenu {{
                background: #FFFFFF; border: 1px solid #E0E0E0;
                border-radius: {int(6*self.scale)}px;
                padding: {int(5*self.scale)}px {int(6*self.scale)}px;
                font-size: {int(13*self.scale)}px;
            }}
            QMenu::item {{
                padding: {int(8*self.scale)}px {int(24*self.scale)}px;
                margin: {int(1*self.scale)}px 0;
                color: #1A1A1A;
                border-radius: {int(4*self.scale)}px;
            }}
            QMenu::item:hover, QMenu::item:selected {{
                background-color: #F0F0F2;
                color: #EC4141;
            }}
        """)
        for text, mode in [("歌名", "song"), ("歌手", "artist"), ("专辑", "album")]:
            act = self._search_mode_menu.addAction(text)
            act.triggered.connect(
                lambda checked, t=text, m=mode: self._set_search_mode(t, m))
        self.search_mode_btn.clicked.connect(self._show_search_mode_menu)
        self.toolbar_layout.addWidget(self.search_mode_btn)
        # 搜索框
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入歌曲名搜索...")
        self.search_input.returnPressed.connect(self.on_search)
        self.toolbar_layout.addWidget(self.search_input, 1)
        # 先给搜索框设置样式（padding 会计入尺寸），再取其真实高度
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid #DCDCDC;
                padding: {int(4*self.scale)}px {int(10*self.scale)}px;
                font-size: {font_size_tool}px;
                background-color: #FFFFFF;
            }}
        """)
        # 模式按钮与搜索框统一高度、统一圆角弧度。
        # 注意：QPushButton 在 2×border-radius > 控件高度 时，圆角会退化为方角，
        # 因此圆角半径取 min(设计值, 控件高度/2 - 1)，保证两者圆角一致且正常渲染。
        tool_h = self.search_input.sizeHint().height()
        tool_radius = min(int(15 * self.scale), tool_h // 2 - 1)
        self.search_mode_btn.setFixedHeight(tool_h)
        reg_theme(self.search_mode_btn, f"""
            QPushButton {{
                border: 1px solid #DCDCDC;
                border-radius: {tool_radius}px;
                padding: {int(4*self.scale)}px {int(10*self.scale)}px;
                font-size: {font_size_tool}px;
                background-color: #FFFFFF;
                color: #1A1A1A;
            }}
            QPushButton:hover {{
                background-color: #F0F0F2;
                color: #EC4141;
            }}
        """)
        self.search_input.setStyleSheet(f"""
            QLineEdit {{
                border: 1px solid #DCDCDC;
                border-radius: {tool_radius}px;
                padding: {int(4*self.scale)}px {int(10*self.scale)}px;
                font-size: {font_size_tool}px;
                background-color: #FFFFFF;
            }}
        """)
        # 搜索按钮：单击效果与回车一致（使用 resources/icons/search.svg 放大镜图标）
        self.search_btn = QPushButton()
        self.search_btn.setFixedSize(tool_h, tool_h)
        self.search_btn.setIcon(QIcon(os.path.join(self.icons_folder, "search.svg")))
        self.search_btn.setIconSize(QSize(int(22 * self.scale), int(22 * self.scale)))
        self.search_btn.setCursor(Qt.PointingHandCursor)
        self.search_btn.setToolTip("搜索")
        self.search_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                border-radius: {tool_radius}px;
            }}
            QPushButton:hover {{
                background-color: #F0F0F2;
            }}
            QPushButton:pressed {{
                background-color: #E0E0E0;
            }}
        """)
        self.search_btn.clicked.connect(self.on_search)
        self.toolbar_layout.addWidget(self.search_btn)
        # 工具栏初始隐藏，进入具体菜单时再按需显示
        self.toolbar.hide()

        # ---------- 歌曲表格 ----------
        self.song_table = QTableWidget()
        self.song_table.setColumnCount(7)
        self.song_table.setHorizontalHeaderLabels(["", "歌曲名", "歌手", "专辑", "时长", "", ""])

        # 列宽需容纳按钮(28px) + 两侧 item padding(8px) + 边框余量，
        # 否则 QTableWidget::item 的 padding 会压缩 cellWidget 可用区，导致 hover 圆形背景右侧被裁剪
        item_pad = int(8 * self.scale)          # 与下方 QSS item padding 保持一致
        btn_size = int(28 * self.scale)         # 与 _add_song_row 中按钮尺寸一致
        btn_col_width = btn_size + 2 * item_pad + 2
        self.song_table.setColumnWidth(0, btn_col_width)
        self.song_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.song_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.song_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.song_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.song_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Fixed)
        self.song_table.setColumnWidth(5, btn_col_width)
        self.song_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.Fixed)
        self.song_table.setColumnWidth(6, btn_col_width)

        self.song_table.verticalHeader().setDefaultAlignment(Qt.AlignCenter)
        font_size_table = int(14 * self.scale)
        # 表格样式：纯静态中性配色，不随主题换色，无需 reg_theme 登记
        table_css = f"""
            QTableWidget {{
                background-color: #FBFBFD;
                border: none;
                gridline-color: transparent;
                font-size: {font_size_table}px;
                color: #1A1A1A;
                outline: none;
                selection-background-color: #E9E9EF;
                selection-color: #1A1A1A;
            }}
            QTableWidget::item {{
                padding: {int(8*self.scale)}px;
                border: none;
                outline: none;
                border-bottom: 1px solid #F0F0F3;
            }}
            QTableWidget::item:hover {{
                background-color: #F1F1F5;
            }}
            QTableWidget::item:selected {{
                background-color: #E9E9EF;
            }}
            QHeaderView {{
                background-color: transparent;
                border: none;
            }}
            QHeaderView::section {{
                background-color: transparent;
                padding: {int(8*self.scale)}px;
                border: none;
                border-bottom: 1px solid #E4E4EA;
                font-weight: bold;
                color: #55555E;
                font-size: {int(13*self.scale)}px;
            }}
        """
        self.song_table.setStyleSheet(table_css)
        # 时长列表头与内容一致居中，其余表头默认左对齐与内容一致
        self.song_table.horizontalHeaderItem(4).setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
        self.song_table.setShowGrid(False)
        self.song_table.setFocusPolicy(Qt.NoFocus)
        # 表格图标尺寸与收藏图标渲染尺寸一致，避免 1 倍图被二次缩放变模糊
        self.song_table.setIconSize(QSize(int(16 * self.scale), int(16 * self.scale)))
        self.song_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.song_table.setMouseTracking(True)
        self.song_table.setAutoScroll(False)
        self.song_table.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.song_table.verticalHeader().setDefaultSectionSize(int(48*self.scale))
        self.song_table.horizontalHeader().setHighlightSections(False)
        self.song_table.setRowCount(0)

        right_layout.addWidget(self.toolbar, 0)
        right_layout.addWidget(self.song_table, 1)

        # ---------- 排行榜母列表：封面棋盘卡片容器（默认隐藏） ----------
        self._build_toplist_cards_widget()
        right_layout.addWidget(self._toplist_cards_widget, 1)
        self._toplist_cards_widget.hide()

        # ---------- 歌单母列表：封面棋盘卡片容器（默认隐藏） ----------
        self._build_playlist_cards_widget()
        right_layout.addWidget(self._playlist_page, 1)
        self._playlist_page.hide()

        # ---------- 设置面板（非表格，默认隐藏） ----------
        self._build_settings_panel()
        right_layout.addWidget(self.settings_panel, 1)
        self.settings_panel.hide()

        body_layout.addWidget(self.left_menu)
        body_layout.addWidget(self.right_panel, 1)

        # ---------- 底部控制栏 ----------
        self.bottom_bar = QWidget()
        self.bottom_bar.setObjectName("bottomBar")
        bar_height = int(80 * self.scale)
        self.bottom_bar.setFixedHeight(bar_height)
        # 必须用 QWidget#bottomBar 限定只作用于底栏自身：无选择器的样式表等价于
        # * { ... }，会传播到所有后代（包括以底栏按钮为 parent 弹出的 QMenu 及
        # 其菜单项），导致菜单每项上边缘多出 1px 灰色 border-top。
        self.bottom_bar.setStyleSheet(f"QWidget#bottomBar {{ background-color: #EAEAEF; border-top: 1px solid #E5E5E5; border-bottom-left-radius: {int(12*self.scale)}px; border-bottom-right-radius: {int(12*self.scale)}px; }}")

        bottom_layout = QHBoxLayout(self.bottom_bar)
        margin = int(20 * self.scale)
        bottom_layout.setContentsMargins(margin, int(10*self.scale), margin, int(10*self.scale))
        bottom_layout.setSpacing(int(10 * self.scale))

        # 封面
        self.cover_label = QLabel()
        cover_size = int(60 * self.scale)
        self.cover_label.setFixedSize(cover_size, cover_size)
        self.cover_label.setScaledContents(True)
        self.cover_label.setStyleSheet("border: 1px solid #E5E5E5; border-radius: 4px; background-color: #F5F5F7;")
        self.update_cover(None)

        # 歌曲信息（封面右侧）
        self.song_info_widget = QWidget()
        self.song_info_widget.setStyleSheet("background: transparent; border: none;")
        song_info_layout = QVBoxLayout(self.song_info_widget)
        song_info_layout.setContentsMargins(0, 0, 0, 0)
        song_info_layout.setSpacing(int(2 * self.scale))

        self.label_song_name = ScrollLabel("未播放")
        self.label_song_name.setStyleSheet(f"""
            font-size: {int(14 * self.scale)}px;
            font-weight: bold;
            color: #1A1A1A;
            border: none;
            background: transparent;
        """)
        self.label_song_name.setFixedWidth(int(160 * self.scale))

        self.label_song_artist = ScrollLabel("")
        self.label_song_artist.setStyleSheet(f"""
            font-size: {int(12 * self.scale)}px;
            color: #999999;
            border: none;
            background: transparent;
        """)
        self.label_song_artist.setFixedWidth(int(160 * self.scale))

        song_info_layout.addWidget(self.label_song_name)
        song_info_layout.addWidget(self.label_song_artist)
        song_info_layout.addStretch()

        # 点击封面/歌名打开详情
        self.cover_label.installEventFilter(self)
        self.song_info_widget.installEventFilter(self)
        self.cover_label.setCursor(Qt.PointingHandCursor)
        self.song_info_widget.setCursor(Qt.PointingHandCursor)

        btn_size_small = int(40 * self.scale)
        btn_size_big = int(50 * self.scale)

        self.btn_prev = MediaButton(MediaButton.ICON_PREV)
        self.btn_prev.setFixedSize(btn_size_small, btn_size_small)
        self.btn_prev.setStyleSheet(self._get_button_style())
        self.btn_prev.clicked.connect(lambda: self.prev_song(auto_advance=True))

        self.btn_play = MediaButton(MediaButton.ICON_PLAY)
        self.btn_play.setFixedSize(btn_size_big, btn_size_big)
        self.btn_play.setStyleSheet(self._get_button_style())
        self.btn_play.clicked.connect(self.toggle_play)

        self.btn_next = MediaButton(MediaButton.ICON_NEXT)
        self.btn_next.setFixedSize(btn_size_small, btn_size_small)
        self.btn_next.setStyleSheet(self._get_button_style())
        self.btn_next.clicked.connect(lambda: self.next_song(auto_advance=True))

        # 播放模式切换按钮（SVG 图标，颜色与播放/暂停同步：默认灰 #666666、hover 红 #EC4141）
        mode_sz = int(26 * self.scale)
        self._icon_loop = self._render_svg_icon("repeat.svg", "#666666", mode_sz)
        self._icon_loop_hover = self._render_svg_icon("repeat.svg", self.theme_color, mode_sz)
        self._icon_repeat = self._render_svg_icon("repeat-one.svg", "#666666", mode_sz)
        self._icon_repeat_hover = self._render_svg_icon("repeat-one.svg", self.theme_color, mode_sz)
        self._icon_random = self._render_svg_icon("shuffle.svg", "#666666", mode_sz)
        self._icon_random_hover = self._render_svg_icon("shuffle.svg", self.theme_color, mode_sz)
        self.mode_btn = QPushButton()
        self.mode_btn.setFixedSize(mode_sz, mode_sz)
        self.mode_btn.setIcon(self._icon_loop)
        self.mode_btn.setIconSize(QSize(mode_sz, mode_sz))
        self.mode_btn.setStyleSheet(self._get_button_style())
        self.mode_btn.setToolTip("播放模式：列表循环")
        self.mode_btn.installEventFilter(self)
        self.mode_btn.clicked.connect(self._toggle_play_mode)
        # hover 切换图标颜色（与播放/暂停同步，不受按钮焦点影响）
        self.mode_btn.enterEvent = lambda e: QPushButton.enterEvent(self.mode_btn, e) or self._update_mode_icon_hover(True)
        self.mode_btn.leaveEvent = lambda e: QPushButton.leaveEvent(self.mode_btn, e) or self._update_mode_icon_hover(False)
        # 在 mode_btn 之后安装 viewport 事件过滤器，确保 eventFilter 中能安全访问 mode_btn
        self.song_table.viewport().installEventFilter(self)

        # 进度条
        self.progress_bar = ClickableSlider(Qt.Horizontal)
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        progress_height = int(4 * self.scale)
        handle_size = int(12 * self.scale)
        slider_style = f"""
            QSlider {{
                border: none;
            }}
            QSlider::groove:horizontal {{
                border: none;
                height: {progress_height}px;
                background: #DCDCDC;
                border-radius: {int(progress_height/2)}px;
            }}
            QSlider::handle:horizontal {{
                border: none;
                background: #EC4141;
                width: {handle_size}px;
                height: {handle_size}px;
                margin: -{int(handle_size/2 - progress_height/2)}px 0;
                border-radius: {int(handle_size/2)}px;
            }}
            QSlider::sub-page:horizontal {{
                border: none;
                background: #EC4141;
                border-radius: {int(progress_height/2)}px;
            }}
        """
        reg_theme(self.progress_bar, slider_style)
        self.progress_bar.sliderPressed.connect(self.on_slider_pressed)
        self.progress_bar.sliderReleased.connect(self.on_slider_released)

        # ---- 时间标签（新增） ----
        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setFixedWidth(int(104 * self.scale))
        self.time_label.setAlignment(Qt.AlignCenter)
        self.time_label.setStyleSheet(f"font-size: {int(13*self.scale)}px; color: #666666; border: none; background: transparent;")

        # 播放列表面板按钮（自定义细三线图标）
        pl_sz = int(32 * self.scale)
        # 播放列表面板按钮改用 music-list.svg（颜色与播放/暂停同步：默认灰/hover 红）
        self._hamburger_icon = self._render_svg_icon("music-list.svg", "#666666", pl_sz)
        self._hamburger_icon_hover = self._render_svg_icon("music-list.svg", self.theme_color, pl_sz)
        self.playlist_btn = QPushButton()
        self.playlist_btn.setFixedSize(pl_sz, pl_sz)
        self.playlist_btn.setIcon(self._hamburger_icon)
        self.playlist_btn.setIconSize(QSize(pl_sz, pl_sz))
        self.playlist_btn.setCursor(Qt.PointingHandCursor)
        self.playlist_btn.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self.playlist_btn.enterEvent = lambda e: QPushButton.enterEvent(self.playlist_btn, e) or self.playlist_btn.setIcon(self._hamburger_icon_hover)
        self.playlist_btn.leaveEvent = lambda e: QPushButton.leaveEvent(self.playlist_btn, e) or self.playlist_btn.setIcon(self._hamburger_icon)
        self.playlist_btn.installEventFilter(self)
        self.playlist_btn.clicked.connect(self._toggle_playlist_panel)

        # 播放列表面板（初始隐藏，作为中央容器子控件）
        self._playlist_panel = QFrame(self.centralWidget())
        pp_w = int(300 * self.scale)
        pp_h = int(400 * self.scale)
        self._playlist_panel.setFixedSize(pp_w, pp_h)
        self._playlist_panel.setStyleSheet(f"""
            QFrame {{
                background-color: #FFFFFF; border: 1px solid #DCDCDC;
                border-radius: {int(10*self.scale)}px;
            }}
            QListWidget {{
                border: none; background: transparent;
                font-size: {int(14*self.scale)}px; color: #1A1A1A;
                outline: none;
            }}
            QListWidget::item:selected {{
                background: transparent; color: #1A1A1A;
            }}
        """)
        pp_layout = QVBoxLayout(self._playlist_panel)
        pp_layout.setContentsMargins(0, 0, 0, int(15 * self.scale))
        # 标题栏
        pp_title = QWidget()
        pp_title.setStyleSheet("background: transparent; border-bottom: 1px solid #EBEBEB;")
        pp_title_layout = QHBoxLayout(pp_title)
        pp_title_layout.setContentsMargins(int(12*self.scale), int(8*self.scale), int(12*self.scale), int(8*self.scale))
        pp_title_label = QLabel("当前播放")
        pp_title_label.setStyleSheet(f"font-size:{int(16*self.scale)}px; font-weight:bold; color:#1A1A1A; background:transparent; border:none;")
        self._pp_title_label = pp_title_label
        pp_title_layout.addWidget(pp_title_label)
        pp_title_layout.addStretch(1)
        # 一键清空按钮
        pp_clear = QPushButton("清空")
        pp_clear.setFixedSize(int(50*self.scale), int(24*self.scale))
        pp_clear.setCursor(Qt.PointingHandCursor)
        reg_theme(pp_clear, f"""
            QPushButton {{
                background: #F0F0F0; border: none; border-radius: {int(12*self.scale)}px;
                font-size: {int(12*self.scale)}px; color: #999999;
            }}
            QPushButton:hover {{ background: #E0E0E0; color: #EC4141; }}
        """)
        pp_clear.clicked.connect(self._clear_current_playlist)
        pp_title_layout.addWidget(pp_clear)
        pp_layout.addWidget(pp_title)
        # 歌曲列表（支持拖拽排序）
        self._playlist_list = _PlaylistListWidget()
        self._playlist_list._player_ref = self
        self._playlist_list.setDragDropMode(QAbstractItemView.InternalMove)
        self._playlist_list.setDefaultDropAction(Qt.MoveAction)
        self._playlist_list.setDragEnabled(True)
        self._playlist_list.setAcceptDrops(True)
        # 关闭 Qt 默认细线指示器：拖拽插入位置改用 _PlaylistListWidget 自绘
        # 的粗红指示线 + 目标行高亮（见其 paintEvent），视觉反馈更清晰
        self._playlist_list.setDropIndicatorShown(False)
        # 必须使用 SingleSelection（而非 NoSelection）：
        # Qt 拖拽启动要求 selectedDraggableIndexes() 非空，即至少有一个被选中的可拖拽项，
        # NoSelection 模式下没有任何选中项，InternalMove 拖拽将永远无法启动。
        self._playlist_list.setSelectionMode(QAbstractItemView.SingleSelection)
        # 使用像素级滚动：滚动单位是像素而非整行，才能精确贴底，
        # 保证播放到末尾时最后 b 首完整显示、无底部留白或部分遮挡。
        self._playlist_list.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        pp_layout.addWidget(self._playlist_list)
        # 空状态容器（与歌曲列表完全独立）
        self._playlist_empty_state = QWidget()
        self._playlist_empty_state.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        empty_layout = QVBoxLayout(self._playlist_empty_state)
        empty_layout.setContentsMargins(0, 0, 0, 0)
        empty_label = QLabel("暂无歌曲")
        empty_label.setAlignment(Qt.AlignCenter)
        empty_label.setStyleSheet(f"""
            QLabel {{
                color: #BBBBBB; font-size: {int(14*self.scale)}px;
                background: transparent; border: none;
            }}
        """)
        empty_layout.addStretch(1)
        empty_layout.addWidget(empty_label, 0, Qt.AlignCenter)
        empty_layout.addStretch(1)
        pp_layout.addWidget(self._playlist_empty_state)
        self._playlist_panel.hide()
        self._playlist_panel.move(-9999, -9999)  # 确保启动时不闪现
        # 面板滑入/滑出动画
        self._panel_anim = QPropertyAnimation(self._playlist_panel, b"pos")
        self._panel_anim.setDuration(200)
        self._panel_anim.setEasingCurve(QEasingCurve.OutCubic)
        # 点击面板外关闭
        QApplication.instance().installEventFilter(self)

        # 音量
        self.volume_slider = ClickableSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(self.volume)
        vol_width = int(80 * self.scale)
        self.volume_slider.setFixedWidth(vol_width)
        reg_theme(self.volume_slider, slider_style)
        self.volume_slider.valueChanged.connect(self.set_volume)

        # 音量按钮（可点击静音/恢复，根据音量切换贴图）
        self.vol_btn = QPushButton()
        vol_sz = int(20 * self.scale)
        self.vol_btn.setFixedSize(vol_sz, vol_sz)
        self.vol_btn.setCursor(Qt.PointingHandCursor)
        self.vol_btn.setStyleSheet("QPushButton { background: transparent; border: none; }")
        self.vol_btn.setIconSize(QSize(vol_sz, vol_sz))
        self._vol_before_mute = self.volume   # 静音前音量（与滑块初始值一致）
        self._vol_muted = False
        self._vol_icon_cache = {}    # (svg名, 颜色) -> QIcon 缓存，拖动滑块不重复渲染
        self._update_vol_btn_icon(self.volume_slider.value(), hover=False)
        self.vol_btn.setToolTip("点击静音，再点恢复音量")
        self.vol_btn.clicked.connect(self._toggle_mute)
        self.vol_btn.enterEvent = lambda e: QPushButton.enterEvent(self.vol_btn, e) or self._update_vol_btn_icon(self.volume_slider.value(), hover=True)
        self.vol_btn.leaveEvent = lambda e: QPushButton.leaveEvent(self.vol_btn, e) or self._update_vol_btn_icon(self.volume_slider.value(), hover=False)

        # 收藏按钮
        self.fav_btn = QPushButton()
        fav_size = int(25 * self.scale)
        self.fav_btn.setFixedSize(fav_size, fav_size)
        self.fav_btn.setCursor(Qt.PointingHandCursor)
        self._update_fav_btn_style(False)
        self.fav_btn.clicked.connect(self._toggle_fav)
        self.song_table.cellClicked.connect(self._on_table_cell_clicked)

        # 组装
        bottom_layout.addWidget(self.cover_label)
        bottom_layout.addWidget(self.song_info_widget)
        bottom_layout.addWidget(self.fav_btn)
        # 更多按钮（改为 more-horizontal.svg 三点图标，尺寸/交互不变）
        self.add_btn = QPushButton()
        add_sz = int(48 * self.scale)
        self.add_btn.setFixedSize(add_sz, add_sz)
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.setStyleSheet("QPushButton { background: transparent; border: none; }")
        icon = self._render_more_icon(add_sz, 0.5)
        if icon is not None:
            self.add_btn.setIcon(icon)
            icon_disp = int(add_sz * 0.5)
            self.add_btn.setIconSize(QSize(icon_disp, icon_disp))
        self.add_btn._menu_anchor_self = True
        self.add_btn.clicked.connect(self._on_add_btn_clicked)
        bottom_layout.addWidget(self.add_btn)
        bottom_layout.addWidget(self.btn_prev)
        bottom_layout.addWidget(self.btn_play)
        bottom_layout.addWidget(self.btn_next)
        bottom_layout.addWidget(self.progress_bar)
        bottom_layout.addWidget(self.time_label)   # 放在进度条右侧
        bottom_layout.addWidget(self.mode_btn)     # 列表按钮左侧
        bottom_layout.addWidget(self.playlist_btn) # 播放列表面板按钮
        # 模式按钮与列表按钮之间增加 3px 额外间距
        bottom_layout.insertSpacing(bottom_layout.indexOf(self.mode_btn) + 1, int(3 * self.scale))
        # 下一首按钮与进度条之间增加间距，使进度条与左右两侧视觉等距
        # （右侧 10px 默认间距 + 计时标签内部留白，故左侧取 15px）
        bottom_layout.insertSpacing(bottom_layout.indexOf(self.progress_bar), int(15 * self.scale))
        # 播放列表面板按钮与音量按钮之间增加间距，避免打开播放列表时误触静音
        bottom_layout.insertSpacing(bottom_layout.indexOf(self.playlist_btn) + 1, int(10 * self.scale))
        bottom_layout.addWidget(self.vol_btn)
        bottom_layout.addWidget(self.volume_slider)

        main_layout.addWidget(self.title_bar, 0)
        main_layout.addWidget(self.body_widget, 1)
        main_layout.addWidget(self.bottom_bar, 0)

        # ---------- 信号连接 ----------
        self.left_menu.itemClicked.connect(self.on_menu_clicked)
        self.song_table.cellDoubleClicked.connect(self._on_table_cell_double_clicked)

        # ---------- 定时器 ----------
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_progress)
        self.timer.start(100)

        # ---------- Tooltip 控制 ----------
        self._tooltip_timer = QTimer(self)
        self._tooltip_timer.setSingleShot(True)
        self._tooltip_timer.timeout.connect(self._on_tooltip_timer)
        self._tooltip_owner = None      # 当前触发 tooltip 的 widget
        self._tooltip_text = ""         # 待显示的文本
        self._tooltip_anchor = QRect()  # 触发区域全局坐标，用于定位

        # 全局 ToolTip 样式：小字体、无动画、无边框（消除圆角残留）
        self.setStyleSheet(self.styleSheet() + f"""
            QToolTip {{
                font-size: {int(14 * self.scale)}px;
                color: #666666;
                background-color: #FFFFFF;
                border: none;
                padding: {int(4 * self.scale)}px {int(8 * self.scale)}px;
                border-radius: 0px;
            }}
        """)

        # ---------- 初始化 ----------
        # 默认打开第一项（排行榜）；初始化触发的 on_menu_clicked 属于"程序默认
        # 打开"，不视为用户浏览行为，禁止覆盖 last_browse_menu 历史记录
        self._suppress_browse_save = True
        self.left_menu.setCurrentRow(0)
        self.on_menu_clicked(self.left_menu.item(0))
        self._suppress_browse_save = False
        self.was_playing = False  # 用于拖动状态记忆

        # ---------- 看板娘 ----------
        self.mascot = None
        self._create_mascot()

        # ---------- 会话恢复（等窗口显示稳定后执行） ----------
        QTimer.singleShot(150, self._restore_session)
        # 延迟清理历史遗留缓存（孤儿 mp3 / 无主封面），避免启动卡顿
        QTimer.singleShot(500, self._startup_cache_cleanup)

    def _discover_mascot_packs(self):
        """扫描脚本同级 mascot/ 目录下的看板娘资源包（含 settings.json 的子目录），
        并把 mascot 根目录的旧版素材作为"内置默认"兜底包一并列出。"""
        packs = []
        base = mascot_dir()
        if os.path.isdir(base):
            for entry in sorted(os.listdir(base)):
                cfg_path = os.path.join(base, entry, "settings.json")
                if os.path.isfile(cfg_path):
                    try:
                        with open(cfg_path, 'r', encoding='utf-8') as f:
                            cfg = json.load(f)
                        # 小组件配置拆分到 controls/settings.json（与 settings.json
                        # 同级 controls/ 目录）；存在则合并进 config['controls']。
                        ctl_path = os.path.join(base, entry, "controls",
                                                "settings.json")
                        if os.path.isfile(ctl_path):
                            try:
                                with open(ctl_path, 'r', encoding='utf-8') as f:
                                    ctl_cfg = json.load(f)
                                # _dir 记录 controls/settings.json 所在目录，作为
                                # 图标/底图等资源的相对基准（配置里写相对路径即可）
                                ctl_cfg["_dir"] = os.path.join(base, entry, "controls")
                                cfg["controls"] = ctl_cfg
                            except Exception as e:
                                print(f"⚠️ 看板娘资源包 {entry} 小组件配置加载失败：{e}")
                        packs.append({
                            "id": entry,
                            "name": cfg.get("name") or entry,
                            "dir": os.path.join(base, entry),
                            "config": cfg,
                        })
                    except Exception as e:
                        print(f"⚠️ 看板娘资源包 {entry} 加载失败：{e}")
        # 旧版直接放在 mascot 根目录的素材兜底（无配置，用默认对话）
        if os.path.isfile(os.path.join(base, "idle_alpha.png")) or \
                os.path.isfile(os.path.join(base, "idle.png")):
            packs.append({
                "id": "__builtin__", "name": "内置默认", "dir": base, "config": None,
            })
        return packs

    def _pack_available(self, p):
        """资源包是否可用：立绘文件真实存在（settings.json 缺失不算数）"""
        if p is None:
            return False
        if p['config'] is None:
            # 旧版兜底包：mascot 根目录直接放素材
            return (os.path.isfile(os.path.join(p['dir'], "idle.png"))
                    or os.path.isfile(os.path.join(p['dir'], "idle_alpha.png")))
        spr = (p['config'] or {}).get('sprite') or {}
        name = spr.get('image') or "idle.png"
        return os.path.isfile(os.path.join(p['dir'], name))

    def _get_mascot_pack(self, pack_id):
        """按 id 取资源包；找不到或立绘文件缺失时，自动切换到其他可用看板娘
        并更新保存 mascot_pack_id（边界兜底）"""
        packs = self._discover_mascot_packs()
        if not packs:
            return None
        # 优先：指定 id 且立绘文件存在
        for p in packs:
            if p['id'] == pack_id and self._pack_available(p):
                return p
        # 兜底：任意一个立绘可用的包
        for p in packs:
            if self._pack_available(p):
                if p['id'] != pack_id:
                    print(f"⚠️ 看板娘资源包「{pack_id}」不可用（找不到立绘），自动切换到「{p['id']}」")
                    self.mascot_pack_id = p['id']
                    self._save_settings()
                return p
        # 全部不可用时返回第一个（_load_image 会文字占位）
        return packs[0]

    def _create_mascot(self, pos=None):
        """创建看板娘（透明置顶小窗）。素材缺失时静默降级，不影响主程序。
        pos：切换资源包时沿用旧窗口位置；None 时使用已保存位置/默认右下角。
        浮动与置顶状态从持久化配置读取。"""
        try:
            pack = self._get_mascot_pack(self.mascot_pack_id)
            if pos is None and self.mascot_pos is not None:
                pos = QPoint(int(self.mascot_pos['x']), int(self.mascot_pos['y']))
            self.mascot = MascotWindow(
                self, self.scale, pack=pack, pos=pos,
                float_enabled=self.mascot_float, topmost=self.mascot_topmost)
            if self.mascot_enabled:
                self.mascot.show()
            print("👧 看板娘已启动" if self.mascot_enabled else "👧 看板娘已创建（未显示）")
        except Exception as e:
            print(f"⚠️ 看板娘启动失败：{e}")
            self.mascot = None

    def switch_mascot(self, pack_id):
        """切换看板娘资源包：保存偏好并沿用旧窗口位置重建"""
        packs = self._discover_mascot_packs()
        if not any(p['id'] == pack_id for p in packs):
            return
        self.mascot_pack_id = pack_id
        self._save_settings()
        old = getattr(self, 'mascot', None)
        old_pos = None
        if old is not None:
            try:
                old_pos = old.pos()  # 记录旧窗口位置，切换后不跳动
                self.mascot_pos = {"x": old_pos.x(), "y": old_pos.y()}
            except RuntimeError:
                old_pos = None
            try:
                old.close()
            except RuntimeError:
                pass
            self.mascot = None
        self._create_mascot(pos=old_pos)

    def _toggle_mascot(self):
        """标题栏按钮：显示/隐藏看板娘，并同步设置页开关状态"""
        if getattr(self, 'mascot', None) is None:
            self._create_mascot()
            return
        if self.mascot.isVisible():
            self.mascot.hide()
            self._sync_mascot_enabled(False)
        else:
            self.mascot.show()
            self._sync_mascot_enabled(True)

    def _sync_mascot_enabled(self, checked):
        """统一更新看板娘开关状态（设置页滑块同步 + 持久化）"""
        self.mascot_enabled = checked
        if getattr(self, '_mascot_toggle', None) is not None:
            self._mascot_toggle.setChecked(checked)
        self._save_settings()

    def _mascot_say(self, text):
        """看板娘说话（未启用/被隐藏时静默）"""
        m = getattr(self, 'mascot', None)
        if m is not None and m.isVisible():
            m.say(text)

    def _mascot_say_event(self, event_key, **ctx):
        """看板娘按触发条件说话（文本来自资源包 settings.json 的 dialogues）"""
        m = getattr(self, 'mascot', None)
        if m is not None and m.isVisible():
            m.say_event(event_key, **ctx)

    def _render_menu_icon(self, svg_name):
        """将侧栏菜单 SVG（resources/icons/1-6.svg）渲染为 QIcon：
        - 默认状态：灰色 #B3B3B3（与菜单文本同色）
        - 选中状态：红色 #EC4141（与选中文本同色）
        图标渲染尺寸固定为 22*scale 逻辑像素，并按 DPR 放大物理分辨率，
        高分屏下清晰无锯齿、不改变侧栏布局。"""
        path = os.path.join(self.icons_folder, svg_name)
        if not os.path.exists(path):
            return None
        try:
            import re
            with open(path, 'r', encoding='utf-8') as f:
                svg_text = f.read()
            from PyQt5.QtSvg import QSvgRenderer
            sz = int(22 * self.scale)
            dpr = self.devicePixelRatio() or 1.0
            icon = QIcon()
            for color_hex, mode in (("#B3B3B3", QIcon.Normal),
                                    (_theme_color(), QIcon.Selected)):
                colored = re.sub(r'fill:\s*#[0-9a-fA-F]{3,8}',
                                 f'fill:{color_hex}', svg_text)
                pm = QPixmap(max(1, int(round(sz * dpr))),
                             max(1, int(round(sz * dpr))))
                pm.setDevicePixelRatio(dpr)
                pm.fill(Qt.transparent)
                p = QPainter(pm)
                # try/finally 确保 QPainter 必定 end()，避免绘制中销毁设备
                try:
                    p.setRenderHint(QPainter.Antialiasing)
                    p.setRenderHint(QPainter.SmoothPixmapTransform)
                    renderer = QSvgRenderer(colored.encode('utf-8'))
                    if renderer.isValid():
                        renderer.render(p, QRectF(0, 0, sz, sz))
                finally:
                    p.end()
                icon.addPixmap(pm, mode)
            return icon
        except Exception:
            return None

    def _render_more_icon(self, sz, ratio=0.6):
        """渲染水平三点 SVG（resources/icons/more-horizontal.svg）为 QIcon：
        默认灰 #666666，hover/按下红 #EC4141（与原有 "⋯" 文本样式一致）。
        ratio 为图标相对按钮尺寸的比例；DPR 放大、抗锯齿、按尺寸缓存。"""
        if not hasattr(self, '_more_icon_cache'):
            self._more_icon_cache = {}
        key = (sz, ratio)
        if key in self._more_icon_cache:
            return self._more_icon_cache[key]
        path = os.path.join(self.icons_folder, "more-horizontal.svg")
        if not os.path.exists(path):
            return None
        try:
            import re
            with open(path, 'r', encoding='utf-8') as f:
                svg_text = f.read()
            from PyQt5.QtSvg import QSvgRenderer
            # 图标渲染尺寸取按钮尺寸的 ratio，避免三点过大
            isize = max(8, int(sz * ratio))
            dpr = self.devicePixelRatio() or 1.0
            icon = QIcon()
            for color_hex, mode in (("#666666", QIcon.Normal),
                                    (_theme_color(), QIcon.Active),
                                    (_theme_color(), QIcon.Selected)):
                colored = re.sub(r'fill:\s*#[0-9a-fA-F]{3,8}',
                                 f'fill:{color_hex}', svg_text)
                pm = QPixmap(max(1, int(round(isize * dpr))),
                             max(1, int(round(isize * dpr))))
                pm.setDevicePixelRatio(dpr)
                pm.fill(Qt.transparent)
                p = QPainter(pm)
                # try/finally 确保 QPainter 必定 end()
                try:
                    p.setRenderHint(QPainter.Antialiasing)
                    p.setRenderHint(QPainter.SmoothPixmapTransform)
                    renderer = QSvgRenderer(colored.encode('utf-8'))
                    if renderer.isValid():
                        renderer.render(p, QRectF(0, 0, isize, isize))
                finally:
                    p.end()
                icon.addPixmap(pm, mode)
            self._more_icon_cache[key] = icon
            return icon
        except Exception:
            return None

    def _render_svg_pixmap(self, svg_name, color_hex, sz, zoom=1.0):
        """渲染 resources/icons/ 下 SVG 为指定颜色、指定尺寸的 QPixmap（DPR 放大、平滑、缓存）。
        zoom > 1 时放大 SVG 内容（居中、超出部分裁掉），用于让图标视觉上更大。"""
        if not hasattr(self, '_btn_pm_cache'):
            self._btn_pm_cache = {}
        key = (svg_name, color_hex, sz, zoom)
        if key in self._btn_pm_cache:
            return self._btn_pm_cache[key]
        path = os.path.join(self.icons_folder, svg_name)
        if not os.path.exists(path):
            return None
        try:
            import re
            with open(path, 'r', encoding='utf-8') as f:
                svg_text = f.read()
            from PyQt5.QtSvg import QSvgRenderer
            dpr = self.devicePixelRatio() or 1.0
            # 同时替换 fill 与 stroke 颜色，兼容 "fill:#xxx" / "fill: #xxx"
            # 以及 <style>.cls-1 { fill:#xxx }</style> 这种 class 引用写法
            colored = re.sub(r'fill:\s*#[0-9a-fA-F]{3,8}',
                             f'fill:{color_hex}', svg_text)
            colored = re.sub(r'stroke:\s*#[0-9a-fA-F]{3,8}',
                             f'stroke:{color_hex}', colored)
            pm = QPixmap(max(1, int(round(sz * dpr))),
                         max(1, int(round(sz * dpr))))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            # try/finally 确保 QPainter 必定 end()
            try:
                p.setRenderHint(QPainter.Antialiasing)
                p.setRenderHint(QPainter.SmoothPixmapTransform)
                renderer = QSvgRenderer(colored.encode('utf-8'))
                if renderer.isValid():
                    if zoom > 1.0:
                        zsz = sz * zoom
                        renderer.render(
                            p, QRectF(-(zsz - sz) / 2, -(zsz - sz) / 2, zsz, zsz))
                    else:
                        renderer.render(p, QRectF(0, 0, sz, sz))
            finally:
                p.end()
            self._btn_pm_cache[key] = pm
            return pm
        except Exception:
            return None

    def _render_svg_icon(self, svg_name, color_hex, sz):
        """渲染 resources/icons/ 下 SVG 为指定颜色、指定尺寸的 QIcon（基于 _render_svg_pixmap）。
        注意：仅单色 QIcon，hover 变色通过 enterEvent/leaveEvent 切换图标实现，
        避免多状态 QIcon 在按钮获得键盘焦点时误用 Active 红色导致常亮。"""
        pm = self._render_svg_pixmap(svg_name, color_hex, sz)
        return QIcon(pm) if pm is not None else None

    def _get_button_style(self):
        radius = int(25 * self.scale)
        return f"""
            QPushButton {{
                background-color: transparent;
                border: none;
                outline: none;
                border-radius: {radius}px;
            }}
            QPushButton:hover {{
                background-color: #F0F0F2;
            }}
            QPushButton:pressed {{
                background-color: #E0E0E0;
            }}
        """

    def _clear_table_completely(self):
        """完全清空表格区域（数据行 + 隐藏表头），呈现全空白。

        用于猜你喜欢首次加载前的占位：清空全部数据行并隐藏表头，视觉上
        整个表格区域不留任何元素。注意只隐藏表头、不清空表头文本，避免
        破坏初始表头标签（["", "歌曲名", "歌手", "专辑", "时长", "", ""]）。"""
        self.song_table.setRowCount(0)
        self.song_table.clearContents()
        self.song_table.horizontalHeader().setVisible(False)

    def display_playlist(self, menu_text):
        self.song_table.setRowCount(0)
        # 恢复表头可见（加载空白阶段可能被隐藏）
        self.song_table.horizontalHeader().setVisible(True)
        # 还原表头，避免被排行榜/歌单浏览模式的 setText 污染
        self.song_table.horizontalHeaderItem(1).setText("歌曲名")
        songs = self.playlist_data.get(menu_text, [])
        for idx, song_info in enumerate(songs):
            self._add_song_row(idx, song_info)
        self.setWindowTitle(f"VeryGoodPlayer · {menu_text} ({len(songs)} 首)")
        # 显示后刷新当前播放行的高亮
        self._update_table_playing_indicator()
        # 切回侧栏时恢复该页面滚动位置（仅本次进入时）
        self._maybe_restore_scroll(menu_text)
        # 快照当前列表，供下次启动恢复
        self._snapshot_session()

    def _update_table_playing_indicator(self):
        """更新主表格中当前播放行的视觉高亮（歌曲名变红色），
           同时将播放按钮恢复为默认样式。
           通过 song_id / filepath / 名称+歌手 在主表格中查找当前播放歌曲，
           无论其来源是哪个播放列表，只要存在于当前表格中就高亮。"""
        # 步骤 1：重置所有行的样式到默认
        for r in range(self.song_table.rowCount()):
            item = self.song_table.item(r, 1)
            if item:
                item.setForeground(QColor("#1A1A1A"))
            # 重置播放按钮状态（▶ 默认灰色图标，TablePlayButton 自绘）
            cell_widget = self.song_table.cellWidget(r, 0)
            pb = getattr(cell_widget, '_play_btn', None) or cell_widget
            if pb and hasattr(pb, '_playing'):
                pb._playing = False
                pb.update()
        # 步骤 2：获取当前播放歌曲信息
        if self.current_playing_row < 0 or self.current_playing_row >= len(self._panel_queue):
            return
        playing_song = self._panel_queue[self.current_playing_row]
        # 步骤 3：在当前表格显示的列表中查找匹配曲目
        cur_menu = getattr(self, '_current_playlist_menu', None) or self.current_menu
        if not cur_menu or cur_menu not in self.playlist_data:
            return
        songs = self.playlist_data[cur_menu]
        playing_song_id = playing_song.get('song_id')
        playing_filepath = playing_song.get('filepath')
        playing_name = playing_song.get('name', '')
        playing_singer = playing_song.get('singer', '')
        # 优先级：song_id > filepath > name+singer
        match_row = -1
        for idx, s in enumerate(songs):
            sid = s.get('song_id')
            if sid and playing_song_id and sid == playing_song_id:
                match_row = idx
                break
            fp = s.get('filepath')
            if fp and playing_filepath and fp == playing_filepath:
                match_row = idx
                break
        if match_row < 0:
            for idx, s in enumerate(songs):
                if s.get('name') == playing_name and s.get('singer') == playing_singer:
                    match_row = idx
                    break
        # 步骤 4：高亮匹配行
        if 0 <= match_row < self.song_table.rowCount():
            item = self.song_table.item(match_row, 1)
            if item:
                item.setForeground(QColor(_theme_color()))
            # 播放按钮变红（TablePlayButton 自绘切换红色图标）
            cell_widget = self.song_table.cellWidget(match_row, 0)
            pb = getattr(cell_widget, '_play_btn', None) or cell_widget
            if pb and hasattr(pb, '_playing'):
                pb._playing = True
                pb.update()

    def _rebuild_table_play_icons(self):
        """主题色变更后，更新歌曲表格所有播放按钮的 hover 红色图标"""
        try:
            for row in range(self.song_table.rowCount()):
                cell = self.song_table.cellWidget(row, 0)
                pb = getattr(cell, '_play_btn', None) or cell
                if pb is not None and hasattr(pb, 'set_pixmaps'):
                    btn_size = pb.width() or int(28 * self.scale)
                    icon_sz = max(8, int(btn_size * 0.5))
                    pb.set_pixmaps(
                        self._render_svg_pixmap("play-table.svg", "#888888", icon_sz),
                        self._render_svg_pixmap("play-table.svg", self.theme_color, icon_sz))
        except Exception:
            pass

    def _add_song_row(self, row, song_info):
        self.song_table.insertRow(row)
        play_btn = TablePlayButton()
        btn_size = int(28 * self.scale)
        play_btn.setFixedSize(btn_size, btn_size)
        # 播放图标 SVG（默认灰 #888888 / hover 红 #EC4141，与原文本行为一致）
        # 用 play-table.svg（居中版）：原 play.svg 的三角形在 viewBox 内偏左，
        # 表格按钮带 hover 圆时视觉上明显不居中；底栏 MediaButton 仍用原 play.svg。
        icon_sz = max(8, int(btn_size * 0.5))
        play_btn.set_pixmaps(self._render_svg_pixmap("play-table.svg", "#888888", icon_sz),
                             self._render_svg_pixmap("play-table.svg", self.theme_color, icon_sz))
        play_btn.clicked.connect(lambda checked, r=row: self.play_song(r))
        # 容器包裹实现 y 方向精确居中（cellWidget 在部分平台的默认对齐不居中）
        play_container = QWidget()
        play_container.setStyleSheet("background: transparent;")
        play_layout = QHBoxLayout(play_container)
        play_layout.setContentsMargins(0, 0, 0, 0)
        play_layout.setSpacing(0)
        # 注意：不能用 play_layout.setAlignment(...)——它只设置布局在其父布局中的
        # 对齐，顶层布局下无效，按钮会被放在格子左侧。必须在 addWidget 上指定对齐。
        play_layout.addWidget(play_btn, 0, Qt.AlignCenter)
        play_container._play_btn = play_btn
        self.song_table.setCellWidget(row, 0, play_container)

        name_item = QTableWidgetItem(song_info['name'])
        self.song_table.setItem(row, 1, name_item)
        singer_item = QTableWidgetItem(song_info['singer'])
        self.song_table.setItem(row, 2, singer_item)
        album_item = QTableWidgetItem(song_info['album'])
        self.song_table.setItem(row, 3, album_item)
        self.song_table.setItem(row, 4, QTableWidgetItem(song_info['duration']))
        # 收藏状态列（图标）
        fav_item = QTableWidgetItem("")
        fav_item.setFlags(fav_item.flags() & ~Qt.ItemIsSelectable)  # 保留交互，去掉选中高亮
        fav_item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
        fav_img = "like.svg" if self._is_fav(song_info) else "dislike.svg"
        path = os.path.join(self.icons_folder, fav_img)
        if os.path.exists(path):
            icon_size = int(16 * self.scale)
            fav_item.setIcon(QIcon(self._render_fav_icon_pixmap(path, QSize(icon_size, icon_size))))
        self.song_table.setItem(row, 5, fav_item)
        # 更多按钮（改为 more-horizontal.svg 三点图标，尺寸/交互不变）
        add_btn = QPushButton()
        add_btn.setFixedSize(btn_size, btn_size)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.setStyleSheet("QPushButton { background-color: transparent; border: none; }")
        icon = self._render_more_icon(btn_size)
        if icon is not None:
            add_btn.setIcon(icon)
            icon_disp = int(btn_size * 0.6)
            add_btn.setIconSize(QSize(icon_disp, icon_disp))
        add_btn.clicked.connect(lambda checked, r=row, b=add_btn: self._on_add_clicked(r, b))
        self.song_table.setCellWidget(row, 6, add_btn)
        for col in [1, 2, 3, 4]:
            item = self.song_table.item(row, col)
            if item:
                item.setTextAlignment((Qt.AlignLeft | Qt.AlignVCenter) if col in (1, 2, 3) else (Qt.AlignCenter | Qt.AlignVCenter))

    def load_local_folder(self):
        menu_text = "📁 本地下载"
        songs = []
        audio_exts = {'.mp3', '.flac', '.wav', '.m4a', '.ogg', '.aac', '.wma'}

        if not os.path.exists(self.local_folder):
            os.makedirs(self.local_folder)

        for file in os.listdir(self.local_folder):
            filepath = os.path.join(self.local_folder, file)
            if not os.path.isfile(filepath):
                continue
            ext = os.path.splitext(file)[1].lower()
            if ext in audio_exts:
                name = os.path.splitext(file)[0]
                singer = "本地文件"
                album = ""
                duration = "--:--"

                if HAS_MUTAGEN:
                    try:
                        meta = MutagenFile(filepath)
                        if meta is not None:
                            if 'title' in meta:
                                name = str(meta['title'][0])
                            elif 'TIT2' in meta:
                                name = str(meta['TIT2'][0])
                            if 'artist' in meta:
                                singer = str(meta['artist'][0])
                            elif 'TPE1' in meta:
                                singer = str(meta['TPE1'][0])
                            if 'album' in meta:
                                album = str(meta['album'][0])
                            elif 'TALB' in meta:
                                album = str(meta['TALB'][0])
                            if hasattr(meta.info, 'length') and meta.info.length:
                                secs = int(meta.info.length)
                                mins = secs // 60
                                secs = secs % 60
                                duration = f"{mins:02d}:{secs:02d}"
                    except:
                        pass

                songs.append({
                    'name': name,
                    'singer': singer,
                    'album': album,
                    'duration': duration,
                    'filepath': filepath
                })

        # 应用本地下载排序方式（曲名/歌手/时间）
        mode = self._local_sort
        if mode == "name_desc":
            songs.sort(key=lambda s: s.get("name", ""), reverse=True)
        elif mode == "singer_asc":
            songs.sort(key=lambda s: s.get("singer", ""))
        elif mode == "singer_desc":
            songs.sort(key=lambda s: s.get("singer", ""), reverse=True)
        elif mode == "time_desc":
            songs.sort(key=lambda s: os.path.getmtime(s.get("filepath", ""))
                       if s.get("filepath") else 0, reverse=True)
        elif mode == "time_asc":
            songs.sort(key=lambda s: os.path.getmtime(s.get("filepath", ""))
                       if s.get("filepath") else 0)
        else:  # name_asc
            songs.sort(key=lambda s: s.get("name", ""))

        self.playlist_data[menu_text] = songs
        print(f"✅ 本地下载已更新：{len(songs)} 首歌曲")
        if self.current_menu == menu_text:
            self.display_playlist(menu_text)

    def _set_cover_pixmap(self, pix):
        """把封面 QPixmap 缩放后渲染到左下角封面标签"""
        if pix.isNull():
            self._set_default_cover()
            return
        scaled = pix.scaled(self.cover_label.width(),
                                            self.cover_label.height(),
                                            Qt.KeepAspectRatio,
                                            Qt.SmoothTransformation)
        self.cover_label.setPixmap(scaled)

    def _set_default_cover(self):
        """显示默认封面 no_cover.png；缺失则用灰底占位"""
        default_cover = image_path("no_cover.png")
        if os.path.exists(default_cover):
            pix = QPixmap(default_cover)
            if not pix.isNull():
                self._set_cover_pixmap(pix)
                return
        pixmap = QPixmap(self.cover_label.width(), self.cover_label.height())
        pixmap.fill(QColor("#CCCCCC"))
        self.cover_label.setPixmap(pixmap)

    def update_cover(self, filepath, cover_url=None):
        """更新左下角封面。明确区分在线 / 离线路径，互不干扰。

        - 在线模式（cover_url 有值）：仅从网络下载封面，不读取本地文件或
          内嵌封面，避免在线歌曲被误判为无封面。
        - 离线模式（cover_url 为空，有 filepath）：仅从本地同名图片 / 音频
          文件内嵌封面读取，完全不联网。
        - 两者都失败则显示默认封面。
        """
        # ===== 在线模式 =====
        if cover_url and self.online_api:
            song_id = None
            # 优先从 _panel_queue 获取 song_id（current_playing_row 始终是 _panel_queue 索引）
            if 0 <= self.current_playing_row < len(self._panel_queue):
                song_id = self._panel_queue[self.current_playing_row].get('song_id')
            if song_id:
                # 同步探测缓存：缓存命中即本地读文件（极快、不联网、不卡 UI），
                # 直接显示；未命中才后台下载，下载完成由 _apply_cover_path 替换。
                cov_path = self.online_api.download_cover(cover_url, song_id)
                if cov_path and os.path.exists(cov_path):
                    pix = QPixmap(cov_path)
                    if not pix.isNull():
                        self._set_cover_pixmap(pix)
                        return
                self._set_default_cover()
                self._run_in_thread(self.online_api.download_cover,
                                    lambda res: self._apply_cover_path(res),
                                    cover_url, song_id)
            else:
                self._set_default_cover()
            return

        # ===== 离线模式 =====
        cover_path = None
        cover_data = None
        if filepath:
            # 1) 同目录同名图片（兼容旧下载 / 外部导入）
            base = os.path.splitext(filepath)[0]
            for ext in ['.jpg', '.jpeg', '.png']:
                candidate = base + ext
                if os.path.exists(candidate):
                    cover_path = candidate
                    break
            # 2) 音频文件内嵌封面（封面已写入文件本体）
            if cover_path is None:
                cover_data = read_embedded_cover(filepath)

        if cover_path:
            pix = QPixmap(cover_path)
            if not pix.isNull():
                self._set_cover_pixmap(pix)
                return
        if cover_data is not None:
            pix = QPixmap()
            if pix.loadFromData(cover_data) and not pix.isNull():
                self._set_cover_pixmap(pix)
                return
        self._set_default_cover()

    def play_song(self, row, auto_advance=False):
        """播放指定行歌曲。auto_advance=True 表示由自动切歌/上下一首触发，失败时自动跳过"""
        self._auto_advancing = auto_advance
        if not HAS_PYGAME:
            QMessageBox.warning(self, "缺少组件", "请先安装pygame库：\npip install pygame")
            return

        if self.current_menu is None:
            return
        menu = getattr(self, '_current_playlist_menu', None) or self.current_menu
        songs = self.playlist_data.get(menu, [])
        if row >= len(songs):
            return
        song_info = songs[row]
        # 保存原面板队列：播放失败时恢复（点击主表格失败不替换原队列）
        self._prev_panel_queue = list(self._panel_queue)
        self._prev_panel_queue_source = self._panel_queue_source
        # 快照当前播放列表到面板队列（仅播放入口触发替换）
        self._panel_queue = list(songs)
        self._panel_queue_source = menu
        filepath = song_info.get('filepath')

        # ---------- 若本地已有文件则直接播放，不再读取缓存 ----------
        if filepath is None and song_info.get('song_id'):
            safe = re.sub(r'[\\/:*?"<>|]', '_', song_info.get('name', '')).strip()
            if safe:
                for ext in ['.mp3', '.flac']:
                    local_fp = os.path.join(self.local_folder, f"{safe}{ext}")
                    if os.path.exists(local_fp):
                        filepath = local_fp
                        song_info['filepath'] = local_fp
                        break

        # ---------- 在线歌曲：后台获取地址 + 下载，封面并行加载 ----------
        if filepath is None and song_info.get('song_id') and self.online_api:
            song_id = song_info['song_id']
            print(f"🌐 后台下载在线歌曲... (ID: {song_id})")
            # 保存当前播放状态，下载失败后恢复
            self._prev_playing_row = self.current_playing_row
            self._prev_was_playing = self.btn_play.is_playing
            self.current_playing_row = row
            self._song_loading = True
            self._song_ready = False
            # 协调器：歌曲与封面后台并行加载，都就绪后才 _play_prepared 一起呈现
            self._pending_prepare = {
                'row': row, 'song_info': song_info,
                'filepath': None, 'song_ok': False, 'cover_ok': False,
                'cover_path': None, 'failed': False,
            }
            self.btn_play.set_loading(True)
            # 1) 歌曲文件：后台下载
            self._run_in_thread(self._prepare_online_song,
                                lambda res: self._on_online_song_ready(res, row, song_info),
                                song_id)
            # 2) 封面：后台下载（与歌曲并行，纳入同一加载动画）
            cover_url = song_info.get('cover_url')
            if cover_url:
                self._run_in_thread(self.online_api.download_cover,
                                    lambda res: self._on_cover_ready(res, row),
                                    cover_url, song_id)
            else:
                # 无封面 URL：封面部分立即视为就绪（用默认/内嵌）
                self._pending_prepare['cover_ok'] = True
                self._maybe_prepare()
            # 3) 歌词：后台预加载并缓存（面板打开时直接命中，无需联网）
            self._run_in_thread(self.online_api.get_lyric,
                                lambda res: self._cache_lyric(song_info, res),
                                song_id)
            return

        # ---------- 本地或已有缓存 ----------
        if not filepath or not os.path.exists(filepath):
            if song_info.get('song_id'):
                # 在线歌曲无本地文件（离线/断网/未缓存）：toast 提示，不弹窗
                self._show_toast("播放失败，请检查网络连接", 3000)
            else:
                QMessageBox.warning(self, "文件不存在", f"无法找到文件：{filepath}")
            # 播放失败：恢复原面板队列，不替换播放列表
            if hasattr(self, '_prev_panel_queue'):
                self._panel_queue = self._prev_panel_queue
                self._panel_queue_source = self._prev_panel_queue_source
                try:
                    self._refresh_playlist_list()
                except Exception:
                    pass
            return

        self._play_prepared(row, filepath, song_info)
        # 本地歌曲：后台预加载在线歌词（优先于内嵌/外部lrc），解决重启后缓存歌曲无歌词的问题
        if song_info.get('song_id') and self.online_api:
            self._run_in_thread(self.online_api.get_lyric,
                                lambda res: self._cache_lyric(song_info, res),
                                song_info['song_id'])

    def _prepare_online_song(self, song_id):
        """后台线程：获取播放地址 + 下载缓存"""
        url, size, trial, err = self.online_api.get_song_url(song_id, level="standard")
        if err or not url:
            return (None, err or "无播放地址（可能无版权）"), trial
        cached = self.online_api.download_to_cache(song_id, url, expected_size=size)
        if cached:
            return (cached, None), trial
        return (None, "下载失败"), trial

    @staticmethod
    def _is_no_copyright_err(err):
        """判断错误是否为“明确无版权”（API 正常返回但无播放地址）。

        仅无版权时自动跳过；限流、网络异常、下载失败等系统级错误
        一律不跳过，避免误跳过能试听/能完整播放的歌曲。
        """
        if not err:
            return False
        return "无版权" in err or "暂无播放地址" in err

    def _on_online_song_ready(self, result, row, song_info):
        """在线歌曲就绪回调（主线程）。

        仅标记歌曲部分就绪，待封面也就绪（见 _maybe_prepare）后再一起呈现，
        避免歌曲先播放、封面还在后台慢慢加载导致两者不同步显示。
        """
        if self._pending_prepare is None or self._pending_prepare.get('row') != row:
            # 已取消/被新播放请求取代，放弃本次结果
            self.btn_play.set_loading(False)
            return
        if result is None:
            self._finish_loading(success=False)
            # 线程异常（网络断开等）：不跳过，恢复上一首播放状态
            self._show_toast("播放失败，请检查网络连接", 3000)
            self._restore_previous_playback()
            return
        (filepath, err), trial = result
        if err or not filepath:
            self._finish_loading(success=False)
            # 自动播放（切歌/顺序播放）时队列中无法播放的歌曲直接跳过，不卡住
            if self._auto_advancing:
                self._skip_unplayable(row)
                return
            self._show_toast(err, 3000)
            # 手动播放失败：恢复原队列并弹提示
            self._restore_previous_playback()
            return
        song_info['filepath'] = filepath
        if trial is not None:
            song_info['is_trial'] = True
            song_info['trial_end_sec'] = trial.get("end", 0)
        self._pending_prepare['filepath'] = filepath
        self._pending_prepare['song_ok'] = True
        self._maybe_prepare()

    @staticmethod
    def _cache_lyric(song_info, text):
        """后台歌词预加载结果缓存：失败/为空不写入（面板打开时才再尝试在线获取）。
        注意 song_info 是 _panel_queue 中歌曲对象的引用，直接修改即可生效。
        无论成功与否都打上 _lyric_loaded 标记，供面板区分“仍在加载”与“确实无歌词”。"""
        song_info['_lyric_loaded'] = True
        if text:
            song_info['_lyric_text'] = text

    def _on_cover_ready(self, cover_path, row):
        """封面后台下载完成回调（主线程）。

        封面失败/缺失也视为“封面就绪”（用默认封面），不能因此卡住播放。
        """
        if self._pending_prepare is None or self._pending_prepare.get('row') != row:
            return
        self._pending_prepare['cover_path'] = cover_path
        self._pending_prepare['cover_ok'] = True
        self._maybe_prepare()

    def _maybe_prepare(self):
        """歌曲与封面都就绪后，关闭加载动画并一起呈现播放界面"""
        pp = self._pending_prepare
        if pp is None or not (pp.get('song_ok') and pp.get('cover_ok')):
            return
        self._pending_prepare = None
        self._finish_loading(success=True)
        self._play_prepared(pp['row'], pp['filepath'], pp['song_info'])

    def _finish_loading(self, success):
        """统一收尾加载状态（成功或失败都调用），并清理协调器状态。

        仅用按钮转圈表示加载中，不使用遮罩动画。
        """
        self.btn_play.set_loading(False)
        self._song_loading = False
        self._song_ready = success
        self._pending_prepare = None

    def _skip_unplayable(self, failed_row):
        """跳过无法播放的歌曲（无版权等）。

        在后台线程中预检测后续队列，一次性定位到下一首可播放歌曲，
        避免连续多首无版权时逐首试错造成播放卡顿。
        """
        if not self._panel_queue:
            return
        if self._skip_retry_count >= 10:
            self._skip_retry_count = 0
            self._show_toast("连续多首歌曲无法播放，已停止", 3000)
            self._restore_previous_playback()
            return
        self._skip_retry_count += 1
        queue = list(self._panel_queue)  # 快照，避免检测期间队列被修改
        self._run_in_thread(
            lambda: self._scan_next_playable(failed_row, queue),
            lambda res: self._on_next_playable_found(res, queue),
        )

    def _scan_next_playable(self, failed_row, queue):
        """后台线程：检测 failed_row 之后的歌曲，返回第一个可播放的索引。

        返回 ("ok", idx) 或 ("all_failed", n)。本地文件无需网络立即可用；
        在线歌曲顺序探测播放地址。为降低网易云限流风险：
          - 只跳过“明确无版权”的歌曲；
          - 系统级错误（限流/网络）保守视为可播，交由正常播放流程处理；
          - 检测范围限制为后续 8 首。
        """
        n = len(queue)
        if n == 0:
            return None
        for k in range(1, n + 1):
            idx = (failed_row + k) % n
            si = queue[idx]
            fp = si.get('filepath')
            if fp and os.path.exists(fp):
                return ("ok", idx)
            if not si.get('song_id') or not self.online_api:
                continue  # 无 song_id 且无本地文件，视为不可播
            if self._probe_song(si):
                return ("ok", idx)
            if k >= 8:
                break
        return ("all_failed", n)

    def _probe_song(self, song_info):
        """后台线程：检测单首在线歌曲是否可播（不下载，含 60 秒结果缓存）。

        只对“明确无版权”（API 正常返回但无播放地址）返回 False；
        试听片段（有地址）视为可播；限流、网络异常等系统级错误保守返回 True，
        避免误跳过能播放的歌曲。
        """
        try:
            sid = song_info.get('song_id')
            if not hasattr(self, '_probe_cache'):
                self._probe_cache = {}
            now = datetime.now().timestamp()
            cached = self._probe_cache.get(sid)
            if cached and now - cached[1] < 60:
                return cached[0]
            url, size, trial, err = self.online_api.get_song_url(
                sid, level="standard")
            if url:
                result = True
            elif self._is_no_copyright_err(err):
                result = False
            else:
                result = True  # 限流/网络错误等，保守不跳过
            self._probe_cache[sid] = (result, now)
            return result
        except Exception:
            return True

    def _on_next_playable_found(self, res, queue):
        """跳过检测结果回调（主线程）"""
        if res is None or not self._panel_queue:
            return
        status, info = res
        if status == "all_failed":
            self._skip_retry_count = 0
            self._show_toast("列表中暂无可播放的歌曲", 3000)
            self._restore_previous_playback()
            return
        idx = info
        if idx < 0 or idx >= len(self._panel_queue):
            return
        self._show_toast("已自动跳过无法播放的歌曲", 2500)
        self._play_queue_index(idx, auto_advance=True)

    def _restore_previous_playback(self):
        """在线歌曲获取失败时，恢复上一首的播放状态（若无则重置为停止态）；
        同时恢复原面板队列（点击主表格播放失败不替换播放列表）"""
        # 恢复队列快照
        if hasattr(self, '_prev_panel_queue'):
            self._panel_queue = self._prev_panel_queue
            self._panel_queue_source = self._prev_panel_queue_source
            try:
                self._refresh_playlist_list()
            except Exception:
                pass
        try:
            if hasattr(self, '_prev_playing_row') and self._prev_playing_row >= 0 \
                    and pygame.mixer.music.get_busy():
                self.current_playing_row = self._prev_playing_row
                self._song_ready = True
                self.btn_play.set_icon_type(MediaButton.ICON_PAUSE)
            else:
                self.current_playing_row = -1
                self._song_ready = False
                self.btn_play.set_icon_type(MediaButton.ICON_PLAY)
        except:
            self.current_playing_row = -1
            self._song_ready = False
            self.btn_play.set_icon_type(MediaButton.ICON_PLAY)

    def _play_prepared(self, row, filepath, song_info):
        """文件已就绪，执行播放"""
        # 先更新当前行，确保 update_cover 获取正确的 song_id
        self.current_playing_row = row
        # 使用 _panel_queue_source 优先（_panel_queue 的实际来源），
        # 确保从播放列表面板切歌时 _playing_menu 与队列来源一致
        self._playing_menu = self._panel_queue_source or (getattr(self, '_current_playlist_menu', None) or self.current_menu)
        self._playing_row = row
        # 刷新播放列表面板：仅更新当前播放歌曲高亮，保持用户当前浏览位置不变。
        # 滚动定位（滑到当前播放歌曲）延迟到下次打开播放列表时执行，
        # 避免点击切歌时列表突然跳动影响浏览体验。
        self._refresh_playlist_list()
        # 同步更新主表格中的当前播放行高亮
        self._update_table_playing_indicator()
        # 快照当前播放列表与歌曲，供下次启动恢复
        self._snapshot_session()
        # 每次播放都检查试听标记（已缓存的也会弹）
        if song_info.get('is_trial'):
            self._show_toast("当前歌曲仅为试听片段", 2500)
        self.update_cover(filepath, cover_url=song_info.get('cover_url'))
        try:
            pygame.mixer.music.load(filepath)
            pygame.mixer.music.play()
            # 获取总时长（优先用 API 返回的 duration_sec）
            if song_info.get('duration_sec'):
                self.current_song_duration = song_info['duration_sec']
            elif HAS_MUTAGEN:
                try:
                    meta = MutagenFile(filepath)
                    if meta and hasattr(meta.info, 'length'):
                        self.current_song_duration = int(meta.info.length)
                    else:
                        self.current_song_duration = 0
                except:
                    self.current_song_duration = 0
            else:
                self.current_song_duration = 0

            # 重置起始偏移
            self.current_start_pos = 0
            song_info['_start_pos'] = 0
            # 重置回弹标记，终止可能正在进行的回弹动画
            self.progress_bar._bouncing = False

            # 记录播放历史并清理旧缓存
            if song_info.get('song_id') and self.online_api:
                try:
                    self.online_api.record_play(song_info['song_id'])
                except:
                    pass

            if self.current_song_duration == 0:
                self.progress_bar.setEnabled(False)
                self.progress_bar.setToolTip("无法获取歌曲时长，进度跳转不可用")
                self.time_label.setText("--:-- / --:--")
            else:
                self.progress_bar.setEnabled(True)
                self.progress_bar.setToolTip("")
                total_m = self.current_song_duration // 60
                total_s = self.current_song_duration % 60
                self.time_label.setText(f"00:00 / {total_m:02d}:{total_s:02d}")
            self.btn_play.set_loading(False)
            self.btn_play.set_icon_type(MediaButton.ICON_PAUSE)
            self.progress_bar.setValue(0)
            self._song_loading = False
            self._song_ready = True
            self._skip_retry_count = 0  # 播放成功，重置跳过计数器
            self._trial_end_sec = song_info.get('trial_end_sec', 0)
            # 更新底部歌曲信息
            self.label_song_name.setText(song_info['name'])
            self.label_song_artist.setText(song_info['singer'])
            # 同步收藏按钮状态
            self._update_fav_btn_style(self._is_fav(song_info))
            print(f"🎵 开始播放：{song_info['name']} - {song_info['singer']}")
            self._mascot_say_event("on_play", song=song_info['name'])
        except Exception as e:
            self._song_ready = False
            self._song_loading = False
            self.btn_play.set_loading(False)
            print(f"❌ 播放失败：{e}")
            if self._auto_advancing:
                # 自动切歌/顺序播放遇到无法播放的歌曲（如文件损坏）：自动跳过，不卡住
                self._skip_unplayable(row)
                return
            self._show_toast("播放失败，请检查网络连接", 3000)
    
    def toggle_play(self):
        if not HAS_PYGAME:
            return
        if self.current_playing_row == -1:
            if self._panel_queue:
                self._play_queue_index(0)
                return
            if self.song_table.rowCount() > 0:
                self.play_song(0)
            return

        # 当前行存在但歌曲尚未真正加载（如会话恢复后、或在线加载失败）：开始播放该行
        if not self._song_ready:
            if self._song_loading:
                return  # 正在加载中，忽略重复点击
            if 0 <= self.current_playing_row < len(self._panel_queue):
                self._play_queue_index(self.current_playing_row)
            return

        if pygame.mixer.music.get_busy():
            pygame.mixer.music.pause()
            self.btn_play.set_loading(False)
            self.btn_play.set_icon_type(MediaButton.ICON_PLAY)
            print("⏸️ 暂停")
            self._mascot_say_event("on_pause")
        else:
            try:
                pygame.mixer.music.unpause()
            except Exception:
                # 极端情况下无可暂停的音乐：直接重新播放当前行
                if 0 <= self.current_playing_row < len(self._panel_queue):
                    self._play_queue_index(self.current_playing_row)
                return
            self.btn_play.set_loading(False)
            self.btn_play.set_icon_type(MediaButton.ICON_PAUSE)
            print("▶️ 继续播放")
            self._mascot_say_event("on_resume")

    def _play_queue_index(self, idx, auto_advance=False):
        """按 _panel_queue 中的索引播放歌曲（直接使用队列中的副本播放）。

        auto_advance=True 表示由自动切歌/上下首/跳过触发，
        播放失败时（如无版权）会自动跳过后续歌曲。
        """
        if idx < 0 or idx >= len(self._panel_queue):
            return
        si = self._panel_queue[idx]
        fp = si.get('filepath')
        # 已有本地文件 → 直接播放
        if fp and os.path.exists(fp):
            self.current_playing_row = idx
            self._play_prepared(idx, fp, si)
            # 本地歌曲：后台预加载在线歌词，解决重启后缓存歌曲无歌词的问题
            if si.get('song_id') and self.online_api:
                self._run_in_thread(self.online_api.get_lyric,
                                    lambda res: self._cache_lyric(si, res),
                                    si['song_id'])
            return
        # 在线歌曲 → 走下载流程（歌曲与封面并行加载，纳入同一加载动画）
        if si.get('song_id') and self.online_api:
            sid = si['song_id']
            self._auto_advancing = auto_advance
            self._prev_playing_row = self.current_playing_row
            self._prev_was_playing = self.btn_play.is_playing
            self.current_playing_row = idx
            self._song_loading = True
            self._song_ready = False
            self._pending_prepare = {
                'row': idx, 'song_info': si,
                'filepath': None, 'song_ok': False, 'cover_ok': False,
                'cover_path': None, 'failed': False,
            }
            self.btn_play.set_loading(True)
            self._run_in_thread(self._prepare_online_song,
                                lambda res: self._on_online_song_ready(res, idx, si),
                                sid)
            cover_url = si.get('cover_url')
            if cover_url:
                self._run_in_thread(self.online_api.download_cover,
                                    lambda res: self._on_cover_ready(res, idx),
                                    cover_url, sid)
            else:
                self._pending_prepare['cover_ok'] = True
                self._maybe_prepare()
            # 3) 歌词：后台预加载并缓存
            self._run_in_thread(self.online_api.get_lyric,
                                lambda res: self._cache_lyric(si, res),
                                sid)
            return

    def prev_song(self, auto_advance=True):
        if self._song_loading:
            return
        if not self._panel_queue:
            return
        if self.play_mode == 1:  # 单曲循环 → 重播当前曲目
            self._play_queue_index(self.current_playing_row, auto_advance=auto_advance)
            return
        # 列表循环 / 随机播放 → 按列表顺序上一首
        if self.current_playing_row > 0:
            self._play_queue_index(self.current_playing_row - 1, auto_advance=auto_advance)
        else:
            self._play_queue_index(len(self._panel_queue) - 1, auto_advance=auto_advance)

    def _queue_to_play_next(self, song_info):
        """将歌曲插入到当前播放列表和面板队列的下一首位置"""
        insert_pos = self.current_playing_row + 1 if self.current_playing_row >= 0 else len(self._panel_queue)
        # 避免重复（在线歌曲按 song_id 去重，离线歌曲按 filepath 去重）
        sid = song_info.get('song_id')
        fp = song_info.get('filepath')
        for s in self._panel_queue:
            if sid and s.get('song_id') == sid:
                self._show_toast("歌曲已在列表中", 2000)
                return
            if not sid and fp and s.get('filepath') == fp:
                self._show_toast("歌曲已在列表中", 2000)
                return
        # 同时插入面板队列和源列表（保证播放一致性）
        self._panel_queue.insert(insert_pos, song_info)
        src = self._panel_queue_source
        if src and src in self.playlist_data:
            self.playlist_data[src].insert(insert_pos, song_info)
        # 队列为空时首次插入，同步 _panel_queue_source 到当前菜单
        if not self._panel_queue_source:
            cur_menu = getattr(self, '_current_playlist_menu', None) or self.current_menu
            if cur_menu:
                self._panel_queue_source = cur_menu
        # 始终刷新列表面板（即使当前不可见，确保下次打开时数据正确）
        self._refresh_playlist_list()
        self._show_toast(f"下一首播放：{song_info.get('name', '')}", 2000)

    def next_song(self, auto_advance=True):
        if self._song_loading:
            return
        if not self._panel_queue:
            return
        if self.play_mode == 1:  # 单曲循环 → 重播当前曲目
            self._play_queue_index(self.current_playing_row, auto_advance=auto_advance)
        elif self.play_mode == 2:  # 随机播放
            if not self._shuffle_queue:
                self._rebuild_shuffle_queue()
            if self._shuffle_queue:
                self._play_queue_index(self._shuffle_queue.pop(0), auto_advance=auto_advance)
            else:
                self._play_queue_index(self.current_playing_row, auto_advance=auto_advance)
        else:  # 列表循环
            if self.current_playing_row < len(self._panel_queue) - 1:
                self._play_queue_index(self.current_playing_row + 1, auto_advance=auto_advance)
            else:
                self._play_queue_index(0, auto_advance=auto_advance)

    def _toggle_play_mode(self):
        """切换播放模式：列表循环 → 单曲循环 → 随机播放"""
        self.play_mode = (self.play_mode + 1) % 3
        if self.play_mode == 0:
            self.mode_btn.setIcon(self._icon_loop)
            self.mode_btn.setToolTip("播放模式：列表循环")
        elif self.play_mode == 1:
            self.mode_btn.setIcon(self._icon_repeat)
            self.mode_btn.setToolTip("播放模式：单曲循环")
        else:
            self.mode_btn.setIcon(self._icon_random)
            self.mode_btn.setToolTip("播放模式：随机播放")
            self._rebuild_shuffle_queue()
        self._show_toast(self.mode_btn.toolTip(), 1500)

    def _update_mode_icon_hover(self, hover):
        """hover 时切换模式按钮图标颜色（默认灰 #666666 / hover 红 #EC4141）"""
        icons = {
            0: (self._icon_loop, self._icon_loop_hover),
            1: (self._icon_repeat, self._icon_repeat_hover),
            2: (self._icon_random, self._icon_random_hover),
        }
        normal, hovered = icons.get(self.play_mode, (self._icon_loop, self._icon_loop_hover))
        self.mode_btn.setIcon(hovered if hover else normal)

    def _rebuild_shuffle_queue(self):
        """生成随机播放队列（不含当前曲目，确保全部播完才重复）"""
        n = len(self._panel_queue)
        if n <= 1:
            self._shuffle_queue = []
            return
        all_indices = list(range(n))
        if self.current_playing_row in all_indices:
            all_indices.remove(self.current_playing_row)
        random.shuffle(all_indices)
        self._shuffle_queue = all_indices

    def _on_song_end(self):
        """歌曲播放结束处理（根据模式决定下一首或重播）"""
        if self.play_mode == 1:  # 单曲循环
            self.current_start_pos = 0
            try:
                pygame.mixer.music.play()
                self.progress_bar.setValue(0)
            except:
                pass
        else:
            self.next_song()

    def update_progress(self):
        if not HAS_PYGAME:
            return
        # 检测播放结束（含试听越界）
        if self.btn_play.is_playing:
            pos_ms = pygame.mixer.music.get_pos()
            if pos_ms < 0:
                # get_pos 返回 -1 表示歌曲已播完或尚未开始
                if not pygame.mixer.music.get_busy():
                    self._on_song_end()
                return
            pos_sec = self.current_start_pos + pos_ms / 1000.0
            if not pygame.mixer.music.get_busy() or \
               (self.current_song_duration > 0 and pos_sec >= self.current_song_duration) or \
               (self._trial_end_sec > 0 and pos_sec >= self._trial_end_sec):
                self._on_song_end()
                return
        else:
            return

        total_sec = self.current_song_duration

        if total_sec > 0:
            # 更新进度条
            progress = int((pos_sec / total_sec) * 1000)
            progress = min(max(progress, 0), 1000)
            if not self.progress_bar.isSliderDown() and not self.progress_bar._bouncing:
                self.progress_bar.setValue(progress)
            # 更新时间显示
            cur_m = int(pos_sec) // 60
            cur_s = int(pos_sec) % 60
            total_m = total_sec // 60
            total_s = total_sec % 60
            self.time_label.setText(f"{cur_m:02d}:{cur_s:02d} / {total_m:02d}:{total_s:02d}")
        else:
            # 无总时长，只显示当前时间（不更新进度条）
            # 但我们可以显示已播放时间
            cur_m = int(pos_sec) // 60
            cur_s = int(pos_sec) % 60
            self.time_label.setText(f"{cur_m:02d}:{cur_s:02d} / --:--")

    def on_slider_pressed(self):
        self.was_playing = pygame.mixer.music.get_busy() if HAS_PYGAME else False
        # 保存当前实际播放位置（秒），用于越界回弹
        if HAS_PYGAME and self.was_playing:
            pos_ms = pygame.mixer.music.get_pos()
            if pos_ms >= 0:
                self._slider_prev_sec = self.current_start_pos + pos_ms / 1000.0
            else:
                self._slider_prev_sec = self.current_start_pos
        else:
            self._slider_prev_sec = self.current_start_pos

    def on_slider_released(self):
        if not HAS_PYGAME:
            return
        if self.current_playing_row == -1:
            return
        if self.current_song_duration == 0:
            self._bounce_back_slider()
            self._show_toast("无法获取歌曲时长，进度跳转不可用", 1500)
            return

        value = self.progress_bar.value()
        target_sec = (value / 1000.0) * self.current_song_duration

        # 计算合法边界（取歌曲总时长与试听片段结束时间的较小值）
        valid_max_sec = self.current_song_duration
        if self._trial_end_sec > 0:
            valid_max_sec = min(valid_max_sec, self._trial_end_sec)

        # 越界拦截：仅在真正拖出范围时才回弹（给浮点/拖到底留容差）。
        # 注意：拖到最开头(0)与拖到最末尾(valid_max_sec)都是合法定位，
        # 不应触发回弹，否则用户无法把进度拉到底/拉到头。
        eps = 0.05  # 0.05 秒容差
        if target_sec >= valid_max_sec + eps:
            self._bounce_back_slider()
            if self._trial_end_sec > 0 and target_sec > self._trial_end_sec + eps:
                self._show_toast("试听片段不支持跳转到此位置", 1500)
            else:
                self._show_toast("已超出歌曲播放时长范围", 1500)
            return

        # --- 合法跳转 ---
        # 夹紧到 [0, duration-0.1]，避免跳到精确末尾被播放器判定结束而自动切歌
        target_sec = max(0.0, min(target_sec, self.current_song_duration - 0.1))
        self.current_start_pos = target_sec
        # 同步到 song_info 供详情页歌词使用。
        # 注意：必须与 play_song 使用相同的列表解析（歌单详情页/排行榜
        # 页时 _current_playlist_menu 才是真正的歌曲列表键，self.current_menu
        # 指向的可能是浏览模式列表，playlist_data 中并不存在该歌曲列表）
        menu = getattr(self, '_current_playlist_menu', None) or self.current_menu
        songs = self.playlist_data.get(menu, [])
        if 0 <= self.current_playing_row < len(songs):
            songs[self.current_playing_row]['_start_pos'] = target_sec
        # 兜底：直接同步面板队列当前播放歌曲（DetailPanel 歌词引用它）
        if 0 <= self.current_playing_row < len(self._panel_queue):
            self._panel_queue[self.current_playing_row]['_start_pos'] = target_sec
        try:
            pygame.mixer.music.play(start=target_sec)
            if not pygame.mixer.music.get_busy():
                self.next_song()
                return
            if not self.was_playing:
                pygame.mixer.music.pause()
                self.btn_play.set_loading(False)
                self.btn_play.set_icon_type(MediaButton.ICON_PLAY)
            else:
                self.btn_play.set_loading(False)
                self.btn_play.set_icon_type(MediaButton.ICON_PAUSE)
            self.progress_bar.setValue(value)
        except Exception as e:
            print(f"跳转失败：{e}")

    def _bounce_back_slider(self):
        """将进度条平滑回弹到当前实际播放位置，不触发任何音频操作"""
        if not HAS_PYGAME:
            return
        # 计算当前实际播放位置（秒）
        pos_ms = pygame.mixer.music.get_pos()
        if pos_ms >= 0:
            current_sec = self.current_start_pos + pos_ms / 1000.0
        else:
            current_sec = self.current_start_pos
        # 边界值约束
        if self.current_song_duration > 0:
            current_sec = max(0.0, min(current_sec, self.current_song_duration - 0.01))
        else:
            current_sec = 0.0
        # 转换为滑块值 (0-1000)
        if self.current_song_duration > 0:
            target_val = int((current_sec / self.current_song_duration) * 1000)
            target_val = max(0, min(1000, target_val))
        else:
            target_val = 0
        # 标记锁定，防止 update_progress 干扰动画
        self.progress_bar._bouncing = True
        # 启动回弹动画（OutCubic 缓动曲线，120ms 快速回弹）
        anim = self.progress_bar.animateToValue(target_val, 120)
        anim.finished.connect(lambda: self._on_bounce_finished(target_val))

    def _on_bounce_finished(self, target_val):
        """回弹动画完成后的收尾处理"""
        self.progress_bar._bouncing = False
        # 确保最终值与实际播放位置一致
        if not self.progress_bar.isSliderDown():
            self.progress_bar.setValue(target_val)

    def set_volume(self, value):
        if HAS_PYGAME:
            pygame.mixer.music.set_volume(value / 100.0)
        self._update_vol_btn_icon(value)
        # 静音切换时滑块会瞬间经过 0，但随后立即恢复，最终状态正确；
        # 此处始终以当前值覆盖保存，确保下次启动复用实际音量
        if value != 0 or not self._vol_muted:
            self.volume = value
            self._save_settings()

    def _toggle_mute(self):
        """点击音量按钮：当前有声则静音，静音中则恢复静音前的音量"""
        cur = self.volume_slider.value()
        if cur > 0:
            self._vol_before_mute = cur
            self._vol_muted = True
            self.volume_slider.setValue(0)  # 触发 set_volume → 更新贴图/播放器音量
        else:
            self._vol_muted = False
            self.volume_slider.setValue(self._vol_before_mute)

    @staticmethod
    def _volume_icon_name(value):
        """根据音量值选择对应贴图：0→静音，1-33→极低，34-66→低，67-100→高"""
        if value <= 0:
            return "volume-slash.svg"
        if value <= 33:
            return "volume-exlow.svg"
        if value <= 66:
            return "volume-low.svg"
        return "volume-high.svg"

    def _make_volume_icon(self, svg_name, color):
        """渲染音量 SVG 为指定颜色的 QIcon（DPR 放大、平滑、结果缓存）"""
        key = (svg_name, color)
        if key in self._vol_icon_cache:
            return self._vol_icon_cache[key]
        path = os.path.join(self.icons_folder, svg_name)
        if not os.path.exists(path):
            return None
        try:
            import re
            with open(path, 'r', encoding='utf-8') as f:
                svg_text = f.read()
            from PyQt5.QtSvg import QSvgRenderer
            dpr = self.devicePixelRatio() or 1.0
            sz = self.vol_btn.width()
            colored = re.sub(r'fill:#[0-9a-fA-F]{3,8}', f'fill:{color}', svg_text)
            pm = QPixmap(max(1, int(round(sz * dpr))), max(1, int(round(sz * dpr))))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            try:
                p.setRenderHint(QPainter.Antialiasing)
                p.setRenderHint(QPainter.SmoothPixmapTransform)
                renderer = QSvgRenderer(colored.encode('utf-8'))
                if renderer.isValid():
                    renderer.render(p, QRectF(0, 0, sz, sz))
            finally:
                p.end()
            icon = QIcon(pm)
            self._vol_icon_cache[key] = icon
            return icon
        except Exception:
            return None

    def _update_vol_btn_icon(self, value, hover=False):
        """根据音量与 hover 状态刷新音量按钮贴图（默认色与 dislike 图标一致）"""
        icon = self._make_volume_icon(self._volume_icon_name(value),
                                      "#EC4141" if hover else "#969696")
        if icon is not None:
            self.vol_btn.setIcon(icon)

    def _restore_toolbar(self):
        """恢复默认工具栏（搜索模式按钮 + 搜索框）"""
        while self.toolbar_layout.count():
            item = self.toolbar_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
        self.toolbar_layout.addWidget(self.search_mode_btn)
        self.toolbar_layout.addWidget(self.search_input, 1)
        self.toolbar_layout.addWidget(self.search_btn)
        self.search_mode_btn.show()
        self.search_input.show()
        self.search_btn.show()

    # ---------- 仿 iOS 风格 Toggle 开关 ----------
    class ToggleSwitch(QWidget):
        """仿 iOS 的滑动开关，主题色为红色（#EC4141）。"""
        toggled = pyqtSignal(bool)

        def __init__(self, checked=False, scale=1.0, parent=None):
            super().__init__(parent)
            self._scale = scale
            self._checked = checked
            self._knob = 1.0 if checked else 0.0  # 0=关 1=开，用于动画
            self._anim = QPropertyAnimation(self, b"knob", self)
            self._anim.setDuration(180)
            self._anim.setEasingCurve(QEasingCurve.OutCubic)
            self.setCursor(Qt.PointingHandCursor)
            self.setFixedSize(int(46 * scale), int(26 * scale))
            self.setStyleSheet("background: transparent;")

        def getKnob(self):
            return self._knob

        def setKnob(self, v):
            self._knob = v
            self.update()

        knob = pyqtProperty(float, getKnob, setKnob)

        def isChecked(self):
            return self._checked

        def setChecked(self, v, animate=True):
            if self._checked == v:
                return
            self._checked = v
            self._anim.stop()
            self._anim.setStartValue(self._knob)
            self._anim.setEndValue(1.0 if v else 0.0)
            if animate:
                self._anim.start()
            else:
                self._knob = 1.0 if v else 0.0
                self.update()

        def mousePressEvent(self, e):
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)
            super().mousePressEvent(e)

        def paintEvent(self, e):
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            w, h = self.width(), self.height()
            r = h / 2.0
            # 轨道背景：开启=红色，关闭=浅灰
            track = QColor(_theme_color()) if self._checked else QColor("#E0E0E0")
            p.setPen(Qt.NoPen)
            p.setBrush(track)
            p.drawRoundedRect(QRectF(0, 0, w, h), r, r)
            # 圆形滑块
            pad = int(3 * self._scale)
            d = h - pad * 2
            x = pad + self._knob * (w - d - pad * 2)
            y = pad
            p.setBrush(QColor("#FFFFFF"))
            p.setPen(QColor(0, 0, 0, 15))
            p.drawEllipse(QRectF(x, y, d, d))

    # ---------- 设置面板 ----------
    def _build_settings_panel(self):
        """构建设置主页面（不使用表格）。目前含：看板娘开关、下载地址。"""
        panel = QWidget()
        panel.setStyleSheet("background-color: #F5F5F7;")
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(int(40*self.scale), int(32*self.scale),
                                int(40*self.scale), int(32*self.scale))
        vbox.setSpacing(int(22*self.scale))

        title = QLabel("设置")
        title.setStyleSheet(f"font-size: {int(22*self.scale)}px; font-weight: 600; "
                            f"color: #1A1A1A;")
        vbox.addWidget(title)
        vbox.addSpacing(int(8*self.scale))

        # —— 外观（看板娘 / 开屏画面）——
        sec_appearance = QLabel("外观")
        sec_appearance.setStyleSheet(
            f"font-size: {int(15*self.scale)}px; font-weight: 600; "
            f"color: #666666; margin-top: {int(8*self.scale)}px;")
        vbox.addWidget(sec_appearance)

        # —— 看板娘开关（仿 iOS 风格）——
        row_mascot = QHBoxLayout()
        row_mascot.setSpacing(int(12*self.scale))
        lab_mascot = QLabel("看板娘")
        lab_mascot.setStyleSheet(f"font-size: {int(14*self.scale)}px; color: #1A1A1A;")
        self._mascot_toggle = self.ToggleSwitch(checked=self.mascot_enabled, scale=self.scale)
        self._mascot_toggle.toggled.connect(self._toggle_mascot_setting)
        row_mascot.addWidget(lab_mascot)
        row_mascot.addWidget(self._mascot_toggle)
        row_mascot.addStretch(1)
        vbox.addLayout(row_mascot)

        # —— 开屏画面开关（仿 iOS 风格）——
        row_splash = QHBoxLayout()
        row_splash.setSpacing(int(12*self.scale))
        lab_splash = QLabel("开屏画面")
        lab_splash.setStyleSheet(f"font-size: {int(14*self.scale)}px; color: #1A1A1A;")
        self._splash_toggle = self.ToggleSwitch(checked=self.splash_enabled, scale=self.scale)
        self._splash_toggle.toggled.connect(self._toggle_splash_setting)
        row_splash.addWidget(lab_splash)
        row_splash.addWidget(self._splash_toggle)
        row_splash.addStretch(1)
        vbox.addLayout(row_splash)

        # —— 主题色（预设色块 + 自定义 hex）——
        sec_theme = QLabel("主题色")
        sec_theme.setStyleSheet(
            f"font-size: {int(15*self.scale)}px; font-weight: 600; "
            f"color: #666666; margin-top: {int(8*self.scale)}px;")
        vbox.addWidget(sec_theme)

        # 预设色块（自绘圆形 ColorSwatch，同一 widget 尺寸保证同行竖直严格对齐）
        preset_colors = ["#EC4141", "#5c9fe4", "#FF7A45", "#F5A623",
                         "#52C41A", "#13C2C2", "#722ED1", "#EB2F96"]
        self._theme_swatches = []
        self._theme_swatch_row = QHBoxLayout()
        self._theme_swatch_row.setSpacing(int(10*self.scale))
        for col in preset_colors:
            sw = ColorSwatch(col, scale=self.scale)
            sw.clicked.connect(lambda checked, c=col: self._apply_custom_theme(c))
            self._theme_swatches.append((col, sw))
            self._theme_swatch_row.addWidget(sw)
        self._theme_swatch_row.addStretch(1)
        vbox.addLayout(self._theme_swatch_row)

        # 自定义 hex 输入 + 应用
        row_hex = QHBoxLayout()
        row_hex.setSpacing(int(10*self.scale))
        lab_hex = QLabel("自定义")
        lab_hex.setStyleSheet(f"font-size: {int(14*self.scale)}px; color: #1A1A1A;")
        self._theme_hex_edit = QLineEdit()
        self._theme_hex_edit.setPlaceholderText("#EC4141")
        self._theme_hex_edit.setText(self.theme_color)
        self._theme_hex_edit.setFixedHeight(int(32*self.scale))
        self._theme_hex_edit.setStyleSheet(
            f"QLineEdit {{ border: 1px solid #DCDCDC; border-radius: "
            f"{int(6*self.scale)}px; padding: 0 {int(8*self.scale)}px; "
            f"font-size: {int(14*self.scale)}px; color: #333333; "
            f"background: #FFFFFF; text-transform: uppercase; }}")
        self._theme_hex_preview = QLabel()
        self._theme_hex_preview.setFixedSize(int(24*self.scale), int(24*self.scale))
        self._theme_hex_preview.setStyleSheet(
            f"border-radius: {int(12*self.scale)}px; background-color: {self.theme_color};")
        btn_apply_hex = QPushButton("应用")
        btn_apply_hex.setFixedSize(int(64*self.scale), int(32*self.scale))
        btn_apply_hex.setCursor(Qt.PointingHandCursor)
        btn_apply_hex.setStyleSheet(
            f"QPushButton {{ background: #FFFFFF; border: 1px solid #DCDCDC; "
            f"border-radius: {int(6*self.scale)}px; "
            f"font-size: {int(14*self.scale)}px; color: #1A1A1A; }}"
            f"QPushButton:hover {{ background-color: #F0F0F0; }}")
        btn_apply_hex.clicked.connect(self._apply_hex_from_input)
        self._theme_hex_edit.textChanged.connect(self._preview_hex_input)
        row_hex.addWidget(lab_hex)
        row_hex.addWidget(self._theme_hex_edit, 1)
        row_hex.addWidget(self._theme_hex_preview)
        row_hex.addWidget(btn_apply_hex)
        vbox.addLayout(row_hex)

        # —— 下载管理（下载地址 / 缓存设置）——
        sec_dl = QLabel("下载管理")
        sec_dl.setStyleSheet(f"font-size: {int(15*self.scale)}px; font-weight: 600; "
                             f"color: #666666; margin-top: {int(8*self.scale)}px;")
        vbox.addWidget(sec_dl)

        # —— 下载地址 ——
        row_dl = QHBoxLayout()
        row_dl.setSpacing(int(12*self.scale))
        lab_dl = QLabel("下载地址")
        lab_dl.setStyleSheet(f"font-size: {int(14*self.scale)}px; color: #1A1A1A;")
        self._dl_edit = QLineEdit()
        self._dl_edit.setReadOnly(True)
        self._dl_edit.setText(os.path.normpath(self.download_dir))
        self._dl_edit.setFixedHeight(int(32*self.scale))
        self._dl_edit.setStyleSheet(
            f"QLineEdit {{ border: 1px solid #DCDCDC; border-radius: "
            f"{int(6*self.scale)}px; padding: 0 {int(8*self.scale)}px; "
            f"font-size: {int(14*self.scale)}px; color: #333333; "
            f"background: #FFFFFF; }}")
        btn_browse = QPushButton("选择文件夹")
        btn_browse.setFixedSize(int(88*self.scale), int(32*self.scale))
        btn_browse.setCursor(Qt.PointingHandCursor)
        btn_browse.setStyleSheet(
            f"QPushButton {{ background: #FFFFFF; border: 1px solid #DCDCDC; "
            f"border-radius: {int(6*self.scale)}px; "
            f"font-size: {int(14*self.scale)}px; color: #1A1A1A; }}"
            f"QPushButton:hover {{ background-color: #F0F0F0; }}")
        btn_browse.clicked.connect(self._browse_download_dir)
        row_dl.addWidget(lab_dl)
        row_dl.addWidget(self._dl_edit, 1)
        row_dl.addWidget(btn_browse)
        vbox.addLayout(row_dl)

        spin_style = (
            f"QSpinBox {{ border: 1px solid #DCDCDC; border-radius: "
            f"{int(6*self.scale)}px; padding: 0 {int(6*self.scale)}px; "
            f"font-size: {int(14*self.scale)}px; color: #333333; "
            f"background: #FFFFFF; }}"
            f"QSpinBox:focus {{ border: 1px solid #EC4141; }}"
            f"QSpinBox::up-button, QSpinBox::down-button {{ width: {int(18*self.scale)}px; "
            f"border: none; background: transparent; }}")

        # 最多保留歌曲缓存数
        row_songs = QHBoxLayout()
        row_songs.setSpacing(int(12*self.scale))
        lab_songs = QLabel("缓存歌曲上限（首）")
        lab_songs.setStyleSheet(f"font-size: {int(14*self.scale)}px; color: #1A1A1A;")
        self._cache_songs_spin = QSpinBox()
        self._cache_songs_spin.setRange(5, 200)
        self._cache_songs_spin.setValue(self.cache_max_songs)
        self._cache_songs_spin.setFixedSize(int(120*self.scale), int(32*self.scale))
        reg_theme(self._cache_songs_spin, spin_style)
        self._cache_songs_spin.valueChanged.connect(self._apply_cache_songs)
        row_songs.addWidget(lab_songs)
        row_songs.addWidget(self._cache_songs_spin)
        row_songs.addStretch(1)
        vbox.addLayout(row_songs)

        # 缓存总大小上限
        row_size = QHBoxLayout()
        row_size.setSpacing(int(12*self.scale))
        lab_size = QLabel("缓存大小上限（MB）")
        lab_size.setStyleSheet(f"font-size: {int(14*self.scale)}px; color: #1A1A1A;")
        self._cache_mb_spin = QSpinBox()
        self._cache_mb_spin.setRange(50, 5000)
        self._cache_mb_spin.setSingleStep(50)
        self._cache_mb_spin.setValue(self.cache_max_mb)
        self._cache_mb_spin.setFixedSize(int(120*self.scale), int(32*self.scale))
        reg_theme(self._cache_mb_spin, spin_style)
        self._cache_mb_spin.valueChanged.connect(self._apply_cache_mb)
        row_size.addWidget(lab_size)
        row_size.addWidget(self._cache_mb_spin)
        row_size.addStretch(1)
        vbox.addLayout(row_size)

        # 说明文字
        tip_cache = QLabel("超出上限时按最近播放顺序自动清理旧缓存（含封面）。")
        tip_cache.setStyleSheet(f"font-size: {int(12*self.scale)}px; color: #999999;")
        vbox.addWidget(tip_cache)

        vbox.addStretch(1)
        self.settings_panel = panel
        # 打开设置时重置待应用色，高亮当前已生效主题色
        self._pending_theme = None
        self._update_theme_swatches()

    def _toggle_mascot_setting(self, checked):
        """设置页的看板娘开关：切换状态并即时 show/hide + 持久化"""
        if getattr(self, 'mascot', None) is not None:
            if checked:
                self.mascot.show()
            else:
                self.mascot.hide()
        self._sync_mascot_enabled(checked)

    def _toggle_splash_setting(self, checked):
        """设置页的开屏画面开关：即时生效（下次启动） + 持久化"""
        self.splash_enabled = bool(checked)
        self._save_settings()

    # ---------- 主题色设置 ----------
    def _apply_custom_theme(self, color):
        """点击预设色块：仅选中（填入输入框 + 预览 + 高亮），需按“应用”才生效"""
        if not self._is_valid_hex(color):
            return
        self._pending_theme = color.lower()
        self._theme_hex_edit.setText(color.upper())
        self._preview_hex_input(color)
        self._update_theme_swatches()

    def _apply_hex_from_input(self):
        """“应用”按钮：将待应用色（输入框中的值）设为生效主题色"""
        raw = self._theme_hex_edit.text().strip()
        if not raw.startswith("#"):
            raw = "#" + raw
        if not self._is_valid_hex(raw):
            self._show_toast("请输入有效的颜色值，例如 #EC4141", 2500)
            return
        self._pending_theme = None
        self._apply_theme(raw)
        self._update_theme_swatches()

    def _preview_hex_input(self, text):
        """实时预览输入色（输入非法时回退到当前主题色）"""
        raw = text.strip()
        if not raw.startswith("#"):
            raw = "#" + raw
        if self._is_valid_hex(raw):
            self._theme_hex_preview.setStyleSheet(
                f"border-radius: {int(12*self.scale)}px; background-color: {raw};")

    def _update_theme_swatches(self):
        """高亮当前选中的预设色块（按待应用色 _pending_theme，否则按已生效主题色）；
        色块 widget 尺寸始终一致，圆心严格对齐（选中态由 ColorSwatch 自绘缩小圆半径实现）。"""
        cur = (getattr(self, "_pending_theme", None) or self.theme_color).lower()
        for col, sw in self._theme_swatches:
            sw.set_selected(col.lower() == cur)

    def _browse_download_dir(self):
        """弹出文件夹选择对话框，修改下载地址并即时生效、持久化"""
        d = QFileDialog.getExistingDirectory(
            self, "选择下载文件夹", os.path.normpath(self.download_dir))
        if not d:
            return
        self._apply_download_dir(d)

    def _apply_download_dir(self, new_dir):
        """更新下载目录：同步 local_folder/cache_folder 并保存设置"""
        new_dir = os.path.normpath(new_dir)
        if not os.path.isdir(new_dir):
            try:
                os.makedirs(new_dir)
            except Exception as e:
                print(f"⚠️ 创建下载目录失败：{e}")
                return
        self.download_dir = new_dir
        self.local_folder = new_dir
        cache = os.path.join(new_dir, ".cache")
        self.cache_folder = cache
        if not os.path.exists(self.cache_folder):
            os.makedirs(self.cache_folder)
        self._dl_edit.setText(new_dir)
        self._save_settings()
        # 刷新当前页面（如正在浏览本地下载）以反映新目录
        if self.current_menu == "📁 本地下载":
            self.load_local_folder()

    def _apply_cache_songs(self, value):
        """缓存歌曲数量上限：即时生效 + 持久化"""
        self.cache_max_songs = value
        api = getattr(self, 'online_api', None)
        if api is not None:
            try:
                api._MAX_CACHE_SONGS = value
            except Exception:
                pass
        self._save_settings()

    def _apply_cache_mb(self, value):
        """缓存大小上限（MB）：即时生效 + 持久化"""
        self.cache_max_mb = value
        api = getattr(self, 'online_api', None)
        if api is not None:
            try:
                api._MAX_CACHE_MB = value
            except Exception:
                pass
        self._save_settings()

    def on_menu_clicked(self, item):
        # 菜单项显示文本不含 emoji，完整键（含 emoji）存在 Qt.UserRole，
        # 保证 playlist_data 键、控制台输出等内部逻辑不受影响
        menu_text = item.data(Qt.UserRole) or item.text()
        # 切换侧栏前保存当前页面滚动位置，并标记返回时恢复
        self._save_scroll_state()
        self._pending_scroll_restore = True
        # 隐藏猜你喜欢失败提示图，避免遮挡其他侧栏
        self._hide_recommend_error()
        # 切换侧栏时隐藏遮罩（猜你喜欢后台加载任务不被中断，结果返回后
        # 仅缓存，不自动切回，避免干扰当前浏览位置）
        self._hide_loading()

        # 在切换前保存当前页面的子状态
        if self.current_menu == "📋 我的歌单":
            self._saved_playlist_state = getattr(self, '_playlist_current', None)
        elif self.current_menu == "📊 排行榜":
            self._saved_toplist_state = {
                'viewing_songs': getattr(self, '_toplist_viewing_songs', False),
                'name': getattr(self, '_current_toplist_name', None),
            }
        else:
            self._saved_playlist_state = getattr(self, '_saved_playlist_state', None)

        self.current_menu = menu_text
        # 记录浏览的侧栏选项并持久化，供下次启动恢复
        # （初始化默认打开阶段由 _suppress_browse_save 抑制，避免覆盖历史）
        if not getattr(self, '_suppress_browse_save', False):
            if getattr(self, 'last_browse_menu', None) != menu_text:
                self.last_browse_menu = menu_text
                self._save_settings()
        self._playlist_browsing = False
        self._toplist_browsing = False
        self._current_playlist_menu = None
        for c in range(7):
            self.song_table.setColumnHidden(c, False)
        # 切换侧栏：隐藏各卡片容器、恢复歌曲表格（各侧栏状态独立）
        if hasattr(self, '_toplist_cards_widget'):
            self._toplist_cards_widget.hide()
        if hasattr(self, '_playlist_page'):
            self._playlist_page.hide()
        self.song_table.show()
        # 切出歌单时恢复默认工具栏
        self._restore_toolbar()

        # 设置页：隐藏表格与工具栏，显示设置面板，其余逻辑跳过
        is_settings = (menu_text == "⚙ 设置")
        self.settings_panel.setVisible(is_settings)
        self.song_table.setVisible(not is_settings)
        if is_settings:
            self.toolbar.hide()
            self.setWindowTitle(f"VeryGoodPlayer · 设置")
            return

        # 显示/隐藏工具栏
        if menu_text == "🎧 发现音乐":
            self.toolbar.show()
            self.search_input.show()
            self.load_discover_music()
        elif menu_text == "📊 排行榜":
            self.toolbar.hide()
            if self._toplist_viewing_songs and "📊 排行榜" in self.playlist_data and self.playlist_data["📊 排行榜"]:
                self.display_playlist("📊 排行榜")
                self._show_toplist_toolbar(self._current_toplist_name)
            elif (hasattr(self, '_saved_toplist_state') and self._saved_toplist_state
                  and self._saved_toplist_state['viewing_songs']
                  and "📊 排行榜" in self.playlist_data and self.playlist_data["📊 排行榜"]):
                self._toplist_viewing_songs = True
                self._current_toplist_name = self._saved_toplist_state['name']
                self.display_playlist("📊 排行榜")
                self._show_toplist_toolbar(self._current_toplist_name)
            else:
                self._browse_toplist()
                # 排行榜歌曲仍在后台加载：恢复遮罩动画显示
                if self._toplist_loading:
                    self._show_loading("加载中…", "toplist")
        elif menu_text == "📁 本地下载":
            self.load_local_folder()
            self._show_local_toolbar()
        elif menu_text == "🎯 猜你喜欢":
            self.toolbar.hide()
            self._load_recommended()
        elif menu_text == "📋 我的歌单":
            self.toolbar.hide()
            pc = getattr(self, '_playlist_current', None)
            if pc:
                self._open_playlist(pc)
            elif hasattr(self, '_saved_playlist_state') and self._saved_playlist_state:
                self._playlist_current = self._saved_playlist_state
                self._open_playlist(self._saved_playlist_state)
            else:
                self._browse_playlists()
        elif menu_text == "❤️ 我喜欢的音乐":
            self.toolbar.hide()
            self._load_favorites_playlist()
        else:
            self.toolbar.hide()
            if menu_text not in self.playlist_data:
                self.playlist_data[menu_text] = []
            self.display_playlist(menu_text)

    # ---------- 侧栏页面滚动位置保存/恢复 ----------
    def _page_scroll_key(self):
        """当前浏览页面的滚动标识：
        歌单/排行榜卡片浏览页 → 各自的卡片键；歌曲列表页 → 菜单键。"""
        if getattr(self, '_playlist_browsing', False):
            return "📋 我的歌单#browse"
        if getattr(self, '_toplist_browsing', False):
            return "📊 排行榜#browse"
        return getattr(self, '_current_playlist_menu', None) or self.current_menu

    def _save_scroll_state(self):
        """切换侧栏前保存当前页面的滚动位置（歌曲表格/歌单卡片分别保存）"""
        key = self._page_scroll_key()
        if not key:
            return
        if getattr(self, '_playlist_browsing', False) and hasattr(self, '_playlist_cards_scroll'):
            self._page_scroll[key] = ('cards', self._playlist_cards_scroll.verticalScrollBar().value())
        elif getattr(self, '_toplist_browsing', False):
            pass  # 排行榜卡片页无独立滚动条，无可保存内容
        else:
            self._page_scroll[key] = ('table', self.song_table.verticalScrollBar().value())

    def _restore_scroll_state(self, key):
        """延迟到布局完成后恢复指定页面的滚动位置（切回侧栏时保留浏览位置）"""
        saved = self._page_scroll.get(key)
        if saved is None:
            return
        kind, val = saved
        def apply():
            try:
                if kind == 'cards' and hasattr(self, '_playlist_cards_scroll'):
                    sb = self._playlist_cards_scroll.verticalScrollBar()
                    sb.setValue(min(val, sb.maximum()))
                else:
                    sb = self.song_table.verticalScrollBar()
                    sb.setValue(min(val, sb.maximum()))
            except RuntimeError:
                pass
        QTimer.singleShot(0, apply)

    def _maybe_restore_scroll(self, key):
        """仅在本次由侧栏切换进入时才恢复滚动，清除标记，避免干扰后台加载完成后的刷新"""
        if not self._pending_scroll_restore:
            return
        self._pending_scroll_restore = False
        self._restore_scroll_state(key)

    # ========== 在线功能 ==========

    def load_discover_music(self):
        """进入发现音乐页（保留用户已输入的搜索词，切回时不丢失）"""
        menu_text = "🎧 发现音乐"
        self.search_input.setFocus()
        self.setWindowTitle(f"VeryGoodPlayer · {menu_text}")
        # 如果之前有搜索结果或推荐，显示
        if menu_text in self.playlist_data:
            self.display_playlist(menu_text)
        else:
            self.song_table.setRowCount(0)
            self.song_table.setRowCount(1)
            self.song_table.setSpan(0, 0, 1, 7)
            hint = QTableWidgetItem("输入关键词搜索，按 Enter 查找歌曲")
            hint.setTextAlignment(Qt.AlignCenter)
            self.song_table.setItem(0, 0, hint)

    # ---------- 后台线程助手 ----------
    def _run_in_thread(self, fn, on_done, *args, **kwargs):
        """在后台线程执行 fn，完成后主线程回调 on_done(result)"""
        thread = GenericThread(fn, *args, **kwargs)
        thread.done.connect(lambda res: on_done(res))
        # 等线程完全结束后再清理引用和对象
        thread.finished.connect(lambda: self._cleanup_thread(thread))
        thread.finished.connect(thread.deleteLater)
        # 保持引用，防止 thread 被 GC
        if not hasattr(self, '_threads'):
            self._threads = []
        self._threads.append(thread)
        thread.start()
        return thread

    def _cleanup_thread(self, thread):
        if hasattr(self, '_threads') and thread in self._threads:
            self._threads.remove(thread)

    def _active_loaders(self, exclude=None):
        """返回当前仍在后台进行的加载器标识集合（排除 exclude）。"""
        owners = set()
        if self._search_loading and exclude != "search":
            owners.add("search")
        if self._toplist_loading and exclude != "toplist":
            owners.add("toplist")
        if self._recommend_loading and exclude != "recommend":
            owners.add("recommend")
        if self._download_loading and exclude != "download":
            owners.add("download")
        return owners

    def _show_loading(self, text="加载中...", owner=None):
        if owner is not None:
            self._loading_owner = owner
        self.loading_overlay.set_mask_enabled(True)
        self.loading_overlay.set_text(text)
        self.loading_overlay.setGeometry(self.right_panel.rect())
        self.loading_overlay.raise_()
        self.loading_overlay.show()
        self.loading_overlay.repaint()
        QApplication.processEvents()

    def _hide_loading(self, owner=None):
        # 无归属的显式隐藏（如切换侧栏）总是隐藏；归属性隐藏才受"其他加载器"约束
        if owner is None:
            self.loading_overlay.hide()
            self._loading_owner = None
            return
        # 若指定归属而被隐藏的遮罩不属于该加载器，忽略
        if self._loading_owner not in (owner, None):
            return
        # 若仍有其他后台加载器在进行，不隐藏遮罩，避免误杀其它加载的遮罩/反馈
        if self._active_loaders(exclude=owner):
            return
        self.loading_overlay.hide()
        self._loading_owner = None

    # ---------- 自动消失提示条 ----------
    def _show_toast(self, msg, duration=2500):
        """在整个窗口中央显示提示（盖住所有子面板）"""
        toast = QLabel(msg, self.body_widget)
        toast.setStyleSheet(f"""
            QLabel {{
                background-color: #666666;
                color: #FFFFFF;
                font-size: {int(15 * self.scale)}px;
                padding: {int(10 * self.scale)}px {int(20 * self.scale)}px;
                border-radius: {int(6 * self.scale)}px;
                border: none;
            }}
        """)
        toast.adjustSize()
        bw = self.body_widget.width()
        bh = self.body_widget.height()
        toast.move((bw - toast.width()) // 2, int(bh * 0.35))
        toast.raise_()
        toast.show()
        # 淡出动画
        opacity = QGraphicsOpacityEffect(toast)
        opacity.setOpacity(1.0)
        toast.setGraphicsEffect(opacity)
        fade = QPropertyAnimation(opacity, b"opacity", toast)
        fade.setDuration(500)
        fade.setEndValue(0.0)  # 不设 startValue，自动用当前值 1.0
        fade.finished.connect(toast.deleteLater)
        QTimer.singleShot(duration, fade.start)

    # ---------- UI 偏好配置 ----------
    def _migrate_legacy_config(self):
        """旧版兼容：将根目录下的 settings.json / favorites.json / playlists.json
        迁移到 config/ 子目录。仅当目标文件不存在且源文件存在时移动；迁移失败
        不影响后续（程序会以默认配置启动，旧文件保留以供排查）。"""
        import shutil
        names = ["settings.json", "favorites.json", "playlists.json"]
        src_dir = app_data_dir()
        dst_dir = config_dir()
        for name in names:
            src = os.path.join(src_dir, name)
            dst = os.path.join(dst_dir, name)
            if os.path.exists(src) and not os.path.exists(dst):
                try:
                    shutil.move(src, dst)
                    print(f"📁 已迁移配置文件 {name} -> config/")
                except Exception as e:
                    print(f"⚠️ 迁移 {name} 失败：{e}")

    def _load_settings(self):
        """读取 UI 偏好配置（settings.json），如收藏列表排序方式"""
        try:
            if os.path.exists(self.settings_file):
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                mode = data.get("favorites_sort")
                if mode in ("time_desc", "time_asc", "name_asc", "name_desc",
                            "singer_asc", "singer_desc"):
                    self._favorites_sort = mode
                lmode = data.get("local_sort")
                if lmode in ("name_asc", "name_desc", "singer_asc", "singer_desc",
                             "time_asc", "time_desc"):
                    self._local_sort = lmode
                ps = data.get("playlist_sort")
                if isinstance(ps, dict):
                    valid = ("none", "time_desc", "time_asc", "name_asc",
                             "name_desc", "singer_asc", "singer_desc")
                    self._playlist_sort = {k: v for k, v in ps.items() if v in valid}
                cs = data.get("card_sort")
                if cs in ("mtime_desc", "mtime_asc", "name_asc", "name_desc"):
                    self._card_sort = cs
                mid = data.get("mascot_pack")
                if isinstance(mid, str) and mid:
                    self.mascot_pack_id = mid
                dd = data.get("download_dir")
                if isinstance(dd, str) and dd and os.path.isdir(dd):
                    self.download_dir = dd
                self.mascot_enabled = bool(data.get("mascot_enabled", True))
                self.splash_enabled = bool(data.get("splash_enabled", True))
                vol = data.get("volume")
                if isinstance(vol, int) and 0 <= vol <= 100:
                    self.volume = vol
                lm = data.get("last_menu")
                if isinstance(lm, str) and lm:
                    self.last_menu = lm
                lp = data.get("last_playlist")
                if isinstance(lp, list):
                    self.last_playlist = [s for s in lp if isinstance(s, dict)]
                pl = data.get("last_playing")
                if isinstance(pl, dict):
                    self.last_playing = pl
                bm = data.get("last_browse_menu")
                if isinstance(bm, str) and bm:
                    self.last_browse_menu = bm
                cs = data.get("cache_max_songs")
                if isinstance(cs, int) and 5 <= cs <= 200:
                    self.cache_max_songs = cs
                cm = data.get("cache_max_mb")
                if isinstance(cm, int) and 50 <= cm <= 5000:
                    self.cache_max_mb = cm
                mp = data.get("mascot_pos")
                if (isinstance(mp, dict) and isinstance(mp.get("x"), (int, float))
                        and isinstance(mp.get("y"), (int, float))):
                    self.mascot_pos = {"x": int(mp["x"]), "y": int(mp["y"])}
                mf = data.get("mascot_float")
                if isinstance(mf, bool):
                    self.mascot_float = mf
                mt = data.get("mascot_topmost")
                if isinstance(mt, bool):
                    self.mascot_topmost = mt
                mc = data.get("mascot_controls")
                if isinstance(mc, bool):
                    self.mascot_controls = mc
                tc = data.get("theme_color")
                if isinstance(tc, str) and self._is_valid_hex(tc):
                    self.theme_color = tc.lower()
                    _set_theme(self.theme_color)
        except Exception as e:
            print(f"⚠️ 设置加载失败：{e}")

    def _save_settings(self):
        """将 UI 偏好配置实时写入 settings.json"""
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump({
                    "favorites_sort": self._favorites_sort,
                    "local_sort": self._local_sort,
                    "playlist_sort": self._playlist_sort,
                    "card_sort": self._card_sort,
                    "mascot_pack": self.mascot_pack_id,
                    "download_dir": self.download_dir,
                    "mascot_enabled": self.mascot_enabled,
                    "splash_enabled": self.splash_enabled,
                    "volume": self.volume,
                    "last_menu": self.last_menu,
                    "last_playlist": self.last_playlist,
                    "last_playing": self.last_playing,
                    "last_browse_menu": self.last_browse_menu,
                    "cache_max_songs": self.cache_max_songs,
                    "cache_max_mb": self.cache_max_mb,
                    "mascot_pos": self.mascot_pos,
                    "mascot_float": self.mascot_float,
                    "mascot_topmost": self.mascot_topmost,
                    "mascot_controls": self.mascot_controls,
                    "theme_color": self.theme_color,
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ 设置保存失败：{e}")

    # ---------- 主题色（全局） ----------
    def _is_valid_hex(self, s):
        """校验 #RRGGBB 格式（大小写均可）"""
        return (isinstance(s, str) and len(s) == 7 and s[0] == "#"
                and all(c in "0123456789abcdefABCDEF" for c in s[1:]))

    def _theme_rgb(self):
        """返回当前主题色的 (r, g, b) 三元组，供 QColor / QPen 使用"""
        h = self.theme_color.lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

    def _css(self, css):
        """把样式模板里的主题色占位（#EC4141 / 236, 65, 65）替换为当前主题色"""
        r, g, b = self._theme_rgb()
        css = css.replace("#EC4141", self.theme_color)
        css = css.replace("236, 65, 65", f"{r}, {g}, {b}")
        return css

    def _apply_theme(self, color=None):
        """切换主题色并实时刷新所有已登记控件、SVG 图标与画布"""
        if color and self._is_valid_hex(color):
            self.theme_color = color.lower()
            self._save_settings()
        # 同步全局主题色，供子控件绘制（QPen/QColor）读取
        _set_theme(self.theme_color)
        # 重刷所有已登记控件（跳过已被销毁的），含非 MusicPlayer 的子控件
        alive = []
        for widget, tmpl in _THEME_WIDGETS:
            try:
                widget.setStyleSheet(_css_global(tmpl))
                alive.append((widget, tmpl))
            except RuntimeError:
                pass
        _THEME_WIDGETS[:] = alive
        # 看板娘控制组件的收藏激活色同步
        try:
            if getattr(self, "mascot_controls_widget", None) is not None:
                self.mascot_controls_widget._active_color = self.theme_color
                self.mascot_controls_widget.update()
        except Exception:
            pass
        # 重绘使用主题色的画布/图标
        self._rerender_theme_icons()
        for w in (getattr(self, "wave_progress", None),
                  getattr(self, "vol_slider", None),
                  getattr(self, "wave_canvas", None),
                  getattr(self, "left_nav", None),
                  getattr(self, "_mascot_toggle", None),
                  getattr(self, "_splash_toggle", None)):
            try:
                if w is not None:
                    w.update()
            except Exception:
                pass

    def _rerender_theme_icons(self):
        """用当前主题色重渲染所有依赖主题色的 SVG 图标（模式/汉堡/返回/播放/侧栏菜单）"""
        try:
            mode_sz = int(26 * self.scale)
            self._icon_loop_hover = self._render_svg_icon("repeat.svg", self.theme_color, mode_sz)
            self._icon_repeat_hover = self._render_svg_icon("repeat-one.svg", self.theme_color, mode_sz)
            self._icon_random_hover = self._render_svg_icon("shuffle.svg", self.theme_color, mode_sz)
            self._update_mode_icon_hover(getattr(self.mode_btn, "_hovered", False))
        except Exception:
            pass
        try:
            pl_sz = int(20 * self.scale)
            self._hamburger_icon_hover = self._render_svg_icon("music-list.svg", self.theme_color, pl_sz)
        except Exception:
            pass
        try:
            if getattr(self, "song_table", None) is not None:
                self._rebuild_table_play_icons()
        except Exception:
            pass
        try:
            if getattr(self, "left_menu", None) is not None and getattr(self, "_menu_specs", None):
                for i in range(self.left_menu.count()):
                    it = self.left_menu.item(i)
                    svg = self._menu_specs[i][2]
                    icon = self._render_menu_icon(svg)
                    if icon is not None:
                        it.setIcon(icon)
        except Exception:
            pass
        try:
            if getattr(self, "_playlist_back_btn", None) is not None:
                self._playlist_back_btn.setIcon(
                    self._render_svg_icon("caret-left.svg", self.theme_color, int(18*self.scale)))
            if getattr(self, "_detail_back_btn", None) is not None:
                self._detail_back_btn.setIcon(
                    self._render_svg_icon("caret-left.svg", self.theme_color, int(18*self.scale)))
            if getattr(self, "_toplist_back_btn", None) is not None:
                self._toplist_back_btn.setIcon(
                    self._render_svg_icon("caret-left.svg", self.theme_color, int(18*self.scale)))
        except Exception:
            pass

    # ---------- 会话恢复（当前播放列表 + 当前歌曲，不保存进度） ----------
    _SESSION_SONG_KEYS = ("song_id", "name", "singer", "album", "duration",
                          "duration_sec", "cover_url", "filepath", "added_at")

    def _sanitize_song(self, song):
        """只保留可安全 JSON 序列化的字段，控制 settings.json 体积"""
        return {k: song[k] for k in self._SESSION_SONG_KEYS
                if k in song and song[k] is not None}

    def _snapshot_session(self):
        """保存当前播放列表（播放列表面板 _panel_queue，独立于歌单）与当前播放歌曲，
        供下次启动恢复。"""
        # 播放列表面板是独立队列，不绑定任何歌单，因此直接快照 _panel_queue
        if not self._panel_queue:
            return
        self.last_playlist = [self._sanitize_song(s) for s in self._panel_queue]
        # 记录当前播放歌来源菜单，仅用于恢复时高亮主表格，不影响播放队列
        menu = (getattr(self, '_playing_menu', None)
                or getattr(self, 'current_menu', None))
        if menu:
            self.last_menu = menu
        self.last_playing = None
        if 0 <= getattr(self, 'current_playing_row', -1) < len(self._panel_queue):
            self.last_playing = self._sanitize_song(
                self._panel_queue[self.current_playing_row])
        self._save_settings()

    @staticmethod
    def _find_song_row(songs, target):
        """按 song_id → filepath → 名称+歌手 的顺序在列表中定位歌曲"""
        tid, tfp = target.get('song_id'), target.get('filepath')
        tname, tsinger = target.get('name', ''), target.get('singer', '')
        for idx, s in enumerate(songs):
            sid, fp = s.get('song_id'), s.get('filepath')
            if (sid and tid and sid == tid) or (fp and tfp and fp == tfp):
                return idx
        for idx, s in enumerate(songs):
            if s.get('name') == tname and s.get('singer') == tsinger:
                return idx
        return -1

    def _restore_session(self):
        """启动后恢复上次浏览的侧栏、上次的播放列表与当前歌曲。

        - 浏览侧栏按 last_browse_menu 直接切换（无历史时保持默认页排行榜）；
        - 播放数据只在后台填充：不自动播放，用户切到对应列表时歌曲会正常高亮。"""
        # 恢复上次浏览的侧栏（独立于播放队列恢复，故放在 early return 之前）
        browse = getattr(self, 'last_browse_menu', None)
        if browse:
            for i in range(self.left_menu.count()):
                it = self.left_menu.item(i)
                if (it.data(Qt.UserRole) or it.text()) == browse and self.current_menu != browse:
                    self.left_menu.setCurrentRow(i)
                    self.on_menu_clicked(it)
                    break

        menu = getattr(self, 'last_menu', None)
        if not menu or menu == "⚙ 设置":
            return
        saved_playing = getattr(self, 'last_playing', None)
        is_custom = menu.startswith("📋 ") and menu != "📋 我的歌单"

        # 只把数据填回 playlist_data，不调用任何显示函数
        if menu == "📁 本地下载":
            # 此刻 current_menu 为默认页，load_local_folder 只填充数据不会显示
            self.load_local_folder()
        elif menu == "❤️ 我喜欢的音乐":
            songs = []
            for fav in self._favorites:
                songs.append({
                    "song_id": fav.get("song_id"),
                    "name": fav.get("name", ""),
                    "singer": fav.get("singer", ""),
                    "album": fav.get("album", ""),
                    "duration": fav.get("duration", "--:--"),
                    "duration_sec": fav.get("duration_sec", 0),
                    "cover_url": fav.get("cover_url", ""),
                    "filepath": None,
                })
            self._sort_favorites_songs(songs)
            self.playlist_data[menu] = songs
        elif is_custom:
            name = menu[len("📋 "):]
            pl = self._playlists.get(name)
            if not pl:
                return
            songs = []
            for s in pl:
                songs.append({
                    "song_id": s.get("song_id"),
                    "name": s.get("name", ""),
                    "singer": s.get("singer", ""),
                    "album": s.get("album", ""),
                    "duration": s.get("duration", "--:--"),
                    "duration_sec": s.get("duration_sec", 0),
                    "cover_url": s.get("cover_url", ""),
                    "added_at": s.get("added_at", ""),
                    "filepath": None,
                })
            self._sort_playlist_songs(name, songs)
            self.playlist_data[menu] = songs
        elif menu == "🎯 猜你喜欢":
            # 猜你喜欢是动态推荐页：启动后首次进入必须全空白重新加载，
            # 不恢复上次会话的旧快照，否则 _load_recommended 会把旧表格
            # 当作"已有内容"渲染在初次遮罩下层（残留上次的推荐表格）。
            # 下方播放队列（_panel_queue）的恢复逻辑不受此影响。
            self.playlist_data[menu] = []
        else:
            # 在线动态列表（发现音乐/排行榜等）：直接恢复快照
            self.playlist_data[menu] = list(self.last_playlist or [])

        # 恢复播放队列与当前歌曲（底部栏显示歌名/歌手，不自动播放）
        # 播放队列直接来自上次快照 last_playlist（独立于歌单，不被歌单增删影响）
        playing = saved_playing
        queue = list(self.last_playlist or [])
        if queue:
            row = self._find_song_row(queue, playing) if playing else -1
            self._panel_queue = queue
            self._panel_queue_source = menu
            self.current_playing_row = row if row >= 0 else 0
            self._playing_menu = menu
            self._playing_row = self.current_playing_row
            if 0 <= self.current_playing_row < len(queue):
                song = queue[self.current_playing_row]
                self.label_song_name.setText(song.get('name') or "未播放")
                self.label_song_artist.setText(song.get('singer') or "")
                self._restore_cover(song)
                # 同步收藏按钮状态，避免已收藏歌曲启动时显示为未收藏
                self._update_fav_btn_style(self._is_fav(song))
                # 恢复会话时即后台预取歌词，使打开详情面板时无需先点击播放才加载
                if song.get('song_id') and self.online_api:
                    self._run_in_thread(self.online_api.get_lyric,
                                        lambda res: self._cache_lyric(song, res),
                                        song['song_id'])
            self.last_playing = playing

    def _restore_cover(self, song):
        """恢复底部专辑封面。在线/离线路径明确分开：

        - 在线歌曲（有 cover_url + song_id）：先显示默认封面占位，再后台
          线程下载封面，下载完成后替换（不读本地/内嵌，避免误判无封面）。
        - 本地歌曲（有 filepath 无 cover_url）：仅从文件内嵌/同名图片读取，
          完全不联网。
        """
        filepath = song.get('filepath')
        cover_url = song.get('cover_url')
        song_id = song.get('song_id')

        # ===== 在线歌曲 =====
        if cover_url and song_id and self.online_api:
            self._set_default_cover()  # 占位，待后台下载完成后替换
            self._run_in_thread(self.online_api.download_cover,
                                lambda res: self._apply_cover_path(res),
                                cover_url, song_id)
            return

        # ===== 本地歌曲 =====
        if filepath:
            self.update_cover(filepath, cover_url=None)
            return

        self._set_default_cover()

    def _apply_cover_path(self, cover_path):
        """后台封面下载完成后，在主线程更新底部封面"""
        if not cover_path or not os.path.exists(cover_path):
            return
        pix = QPixmap(cover_path)
        if pix.isNull():
            return
        self._set_cover_pixmap(pix)

    def _startup_cache_cleanup(self):
        """启动时清理历史遗留缓存（孤儿 mp3 / 无主封面）"""
        api = getattr(self, 'online_api', None)
        if api is None:
            return
        try:
            api.cleanup_orphans()
        except Exception as e:
            print(f"⚠️ 缓存清理失败：{e}")

    # ---------- 收藏 ----------
    def _load_favorites(self):
        try:
            if os.path.exists(self.fav_file):
                with open(self.fav_file, 'r', encoding='utf-8') as f:
                    self._favorites = json.load(f)
            else:
                self._favorites = []
        except:
            self._favorites = []

    def _save_favorites(self):
        try:
            with open(self.fav_file, 'w', encoding='utf-8') as f:
                json.dump(self._favorites, f, ensure_ascii=False, indent=2)
            print(f"📝 收藏已保存 ({len(self._favorites)} 项)")
        except Exception as e:
            print(f"⚠️ 收藏保存失败：{e}")

    # ---------- 自定义歌单 ----------
    def _load_playlists(self):
        try:
            if os.path.exists(self.playlist_file):
                with open(self.playlist_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                # 顶层 _meta 单独存每个歌单的修改时间（用于卡片按时间倒序）
                self._playlist_meta = data.pop("_meta", {})
                self._playlists = data
            else:
                self._playlists = {}
                self._playlist_meta = {}
        except:
            self._playlists = {}
            self._playlist_meta = {}

    def _save_playlists(self):
        try:
            data = dict(self._playlists)
            data["_meta"] = getattr(self, '_playlist_meta', {})
            with open(self.playlist_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠️ 歌单保存失败：{e}")

    def _load_recommended(self, force=False):
        """猜你喜欢：基于收藏歌曲，通过相似歌曲接口 + 搜索混合推荐。
        force=True 时强制联网刷新；否则仅首次自动加载。

        遮罩只覆盖"猜你喜欢"自己的内容区域，各侧栏状态完全独立：
        - 首次加载（无旧数据）：先把表格区域完全清空（含表头），遮罩下全空白；
        - 之后加载（换一批刷新）：遮罩下保留上一份猜你喜欢表格的完整内容，
          不提前增删，也绝不覆盖其他侧栏的内容。
        推荐请求始终在后台线程执行，切换侧栏不会中断；结果返回时若已离开
        猜你喜欢页，则只缓存数据、不自动切回，避免干扰浏览位置。
        """
        if force:
            self._recommended_loaded = False  # 标记需重新请求
            # 不清空旧数据：遮罩下继续显示上一份表格内容

        has_old = ("🎯 猜你喜欢" in self.playlist_data
                   and bool(self.playlist_data["🎯 猜你喜欢"]))

        # 先把表格切换到"猜你喜欢"自己的内容（遮罩只盖它，不盖其他侧栏）：
        if has_old:
            # 有旧数据：渲染猜你喜欢旧内容作为遮罩下层
            self._show_recommend_toolbar()
            self.display_playlist("🎯 猜你喜欢")
        else:
            # 无旧数据：完全清空表格区域（含表头），呈现全空白
            self.toolbar.hide()
            self._clear_table_completely()

        # 已加载过且数据存在 → 直接展示缓存（上面已渲染）
        if self._recommended_loaded and has_old:
            return

        # 正在后台加载 → 显示遮罩（下层已是猜你喜欢自己的内容/空白），不重复请求
        if self._recommend_loading:
            self._show_loading("心动推荐中…", "recommend")
            return

        # 在线服务不可用（离线）：不发起请求，直接展示错误提示图
        if not self.online_api:
            self.playlist_data["🎯 猜你喜欢"] = []
            self._recommended_loaded = True
            self._show_recommend_error()
            return

        self._load_favorites()
        if not self._favorites:
            self._show_toast("暂无收藏，请先收藏歌曲后再使用该功能", 3000)
            self.playlist_data["🎯 猜你喜欢"] = []
            self._recommended_loaded = True
            self._show_recommend_toolbar()
            self.display_playlist("🎯 猜你喜欢")
            return

        # 前台遮罩：下层已是猜你喜欢自己的内容（旧表格或全空白）
        self._show_loading("心动推荐中…", "recommend")
        self._recommend_loading = True
        def recommend_all():
            # 在线服务不可用（离线）：静默返回空，不打印一堆失败日志
            if not self.online_api:
                return []
            # 随机抽取最多 10 首收藏歌曲作为推荐种子，
            # 避免固定取前 10 首导致相似歌曲候选池一成不变
            seed_songs = random.sample(self._favorites, min(10, len(self._favorites)))
            all_songs = []
            seen_ids = set()
            score_map = {}  # song_id -> 关联得分
            # 收藏本身的歌曲也加入去重集
            for fav in self._favorites:
                sid = fav.get('song_id')
                if sid:
                    seen_ids.add(sid)
            # 1. 相似歌曲推荐（主渠道），每个种子多取几首，扩大候选池
            for fav in seed_songs:
                sid = fav.get('song_id')
                if not sid:
                    continue
                try:
                    simi = self.online_api.get_similar_songs(sid, limit=10)
                    for s in simi:
                        sid2 = s.get('song_id')
                        if sid2 and sid2 not in seen_ids:
                            seen_ids.add(sid2)
                            all_songs.append(s)
                        if sid2:
                            score_map[sid2] = score_map.get(sid2, 0) + 1
                except Exception as e:
                    print(f"相似推荐失败 (song_id={sid}): {e}")
            # 2. 如果相似歌曲不够，用搜索兜底（权重减半）
            if len(all_songs) < 15:
                for fav in seed_songs[:5]:
                    kw = fav.get('name', '').strip()
                    if not kw:
                        continue
                    try:
                        songs, _ = self.online_api.search(kw, limit=5)
                        for s in songs:
                            sid = s.get('song_id')
                            if sid and sid not in seen_ids:
                                seen_ids.add(sid)
                                all_songs.append(s)
                            if sid:
                                score_map[sid] = score_map.get(sid, 0) + 0.5
                    except:
                        pass
            # 3. 新歌优先：上一批推荐过的歌放后面，未推荐过的新歌放前面（真正换内容）。
            #    不再按"得分降序"硬排——否则某首与多个种子相似的歌得分最高且唯一时，
            #    会被永远锁死在第一首。改为两组各自随机打乱，第一首也会随每次刷新变化。
            prev_shown = getattr(self, '_recommended_shown_ids', set()) or set()
            new_songs = [s for s in all_songs if s.get('song_id') not in prev_shown]
            old_songs = [s for s in all_songs if s.get('song_id') in prev_shown]
            random.shuffle(new_songs)
            random.shuffle(old_songs)
            # 优先用新歌填满 30 首，不够再补旧歌，避免越换越少
            pool = new_songs[:30]
            if len(pool) < 30:
                pool += old_songs[:30 - len(pool)]
            return pool
        self._run_in_thread(recommend_all,
                            lambda result: self._on_recommend_done(result))

    def _on_recommend_done(self, result):
        self._recommend_loading = False
        self._hide_loading("recommend")
        if result is None:
            result = []
        self.playlist_data["🎯 猜你喜欢"] = result
        # 记录本批推荐的歌曲 ID，供下一次"换一批"排除，避免反复推同一批
        self._recommended_shown_ids = set(s.get('song_id') for s in result if s.get('song_id'))
        self._recommended_loaded = True
        # 若已切换到其他侧栏：只缓存结果，不自动切回，避免干扰当前浏览位置
        cur_menu = getattr(self, '_current_playlist_menu', None) or self.current_menu
        if cur_menu == "🎯 猜你喜欢":
            if result:
                self._hide_recommend_error()
                self._show_recommend_toolbar()
                self.display_playlist("🎯 猜你喜欢")
            else:
                # 推荐失败（离线/网络异常）：空白区域居中展示 error 图
                self._show_recommend_error()
        print(f"🎯 推荐完成，{len(result)} 首歌曲")

    def _show_recommend_error(self):
        """猜你喜欢加载失败：清空表格，在空白区域居中展示 error.png（高度 200）+ 提示文字"""
        self.toolbar.hide()
        self._clear_table_completely()
        if not hasattr(self, '_recommend_error_overlay') or self._recommend_error_overlay is None:
            overlay = QWidget(self.right_panel)
            overlay.setStyleSheet("background: transparent;")
            vbox = QVBoxLayout(overlay)
            vbox.setSpacing(int(12 * self.scale))
            # 图片
            self._recommend_error_img = QLabel(overlay)
            self._recommend_error_img.setAlignment(Qt.AlignCenter)
            # 下方提示文字
            self._recommend_error_text = QLabel("啊哦，网络出错了~", overlay)
            self._recommend_error_text.setAlignment(Qt.AlignCenter)
            self._recommend_error_text.setStyleSheet(
                f"background: transparent; color: #555555; "
                f"font-size: {int(17 * self.scale)}px;")
            vbox.addStretch(1)
            vbox.addWidget(self._recommend_error_img)
            vbox.addWidget(self._recommend_error_text)
            vbox.addStretch(1)
            self._recommend_error_overlay = overlay
            self.right_panel.installEventFilter(self)
        path = image_path("error.png")
        dpr = self.devicePixelRatioF() or 1.0
        pix = QPixmap(path)
        if not pix.isNull():
            pix = pix.scaledToHeight(int(200 * dpr), Qt.SmoothTransformation)
            pix.setDevicePixelRatio(dpr)
            self._recommend_error_img.setPixmap(pix)
        self._recommend_error_overlay.setGeometry(self.right_panel.rect())
        self._recommend_error_overlay.raise_()
        self._recommend_error_overlay.show()

    def _hide_recommend_error(self):
        """隐藏猜你喜欢加载失败的 error 提示"""
        if getattr(self, '_recommend_error_overlay', None) is not None:
            try:
                self._recommend_error_overlay.hide()
            except RuntimeError:
                pass

    def _show_recommend_toolbar(self):
        """显示猜你喜欢页面的工具栏（包含「换一批」刷新按钮）"""
        self.toolbar.show()
        while self.toolbar_layout.count():
            item = self.toolbar_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
        # 容器：灰色提示文字 + 可点击的「换一批」
        container = QWidget(self.toolbar)
        container.setStyleSheet("background: transparent;")
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, int(30 * self.scale), 0)
        row.setSpacing(0)
        hint = QLabel("没发现喜欢的？")
        hint.setStyleSheet(f"""
            QLabel {{
                background: transparent; border: none;
                font-size: {int(14*self.scale)}px; color: #777777;
            }}
        """)
        row.addWidget(hint)
        btn = QPushButton("换一批")
        reg_theme(btn, f"""
            QPushButton {{
                background: transparent; border: none;
                font-size: {int(14*self.scale)}px; color: #EC4141;
                text-decoration: underline;
            }}
            QPushButton:hover {{
                color: #CC3333;
            }}
        """)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self._load_recommended(force=True))
        row.addWidget(btn)
        self.toolbar_layout.addStretch(1)
        self.toolbar_layout.addWidget(container, 0)

    def _add_to_playlist(self, song_info, playlist_name):
        """把歌曲添加到指定歌单"""
        if playlist_name not in self._playlists:
            self._playlists[playlist_name] = []
        sid = song_info.get('song_id')
        # 避免重复
        for s in self._playlists[playlist_name]:
            if s.get('song_id') == sid:
                self._show_toast("歌曲已在歌单中", 2000)
                return
        self._playlists[playlist_name].append({
            "song_id": sid,
            "name": song_info.get('name', ''),
            "singer": song_info.get('singer', ''),
            "album": song_info.get('album', ''),
            "duration": song_info.get('duration', ''),
            "duration_sec": song_info.get('duration_sec', 0),
            "cover_url": song_info.get('cover_url', ''),
            "added_at": datetime.now().isoformat(),  # 添加时间（用于歌单内排序）
        })
        self._playlist_meta[playlist_name] = time.time()  # 更新修改时间
        self._save_playlists()
        self._show_toast(f"已添加到「{playlist_name}」", 2000)

    def _build_playlist_cards_widget(self):
        """歌单母列表：封面棋盘网格容器（圆角封面卡片），外层包 QScrollArea
        以支持大量歌单时的滚动（否则卡片过多会超出可视区导致白屏）。"""
        self._playlist_cards_widget = QWidget()
        self._playlist_cards_widget.setObjectName("playlistCards")
        self._playlist_cards_widget.setStyleSheet(
            "QWidget#playlistCards { background: transparent; }")
        self._playlist_cards_grid = QGridLayout(self._playlist_cards_widget)
        # 边距/间距与排行榜卡片接近，左侧留白充足避免贴边，顶部留白稍大避免首行贴边
        self._playlist_cards_grid.setContentsMargins(40, 28, 20, 20)
        self._playlist_cards_grid.setSpacing(18)  # 卡片间距
        self._playlist_layout = None  # (列数, 卡片尺寸)，None=尚未布局，首次按宽度计算
        # 包裹滚动区
        self._playlist_cards_scroll = QScrollArea()
        self._playlist_cards_scroll.setWidgetResizable(True)
        self._playlist_cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._playlist_cards_scroll.setWidget(self._playlist_cards_widget)
        self._playlist_cards_scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: #F5F5F7; width: 8px;"
            "border-radius: 4px; }"
            "QScrollBar::handle:vertical { background: #C8C8CC;"
            "border-radius: 4px; }"
            "QScrollBar::handle:vertical:hover { background: #A8A8AE; }")
        # 卡片页头部：右上角排序按钮（样式同歌曲排序按钮）
        self._playlist_header = QWidget()
        hl = QHBoxLayout(self._playlist_header)
        hl.setContentsMargins(40, int(14*self.scale), 20, int(6*self.scale))
        hl.setSpacing(int(8*self.scale))
        hl.addStretch(1)
        self._card_sort_btn = QPushButton("排序", self._playlist_header)
        reg_theme(self._card_sort_btn, f"""
            QPushButton {{ background: transparent; border: none;
                font-size: {int(14*self.scale)}px; color: #EC4141;
                text-decoration: underline;
                padding: {int(4*self.scale)}px {int(6*self.scale)}px;
            }}
            QPushButton:hover {{ color: #CC3333; }}
        """)
        self._card_sort_btn.setCursor(Qt.PointingHandCursor)
        self._card_sort_btn.clicked.connect(self._pop_card_sort_menu)
        hl.addWidget(self._card_sort_btn, 0)
        # 页面容器：头部 + 滚动区
        self._playlist_page = QWidget()
        pl = QVBoxLayout(self._playlist_page)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(0)
        pl.addWidget(self._playlist_header, 0)
        pl.addWidget(self._playlist_cards_scroll, 1)

    def _playlist_layout_metrics(self):
        """按滚动区宽度计算歌单卡片列数与卡片尺寸（保证至少 4 列）。

        - 宽窗口：卡片保持原尺寸（150*scale），按宽度增加列数；
        - 窄窗口：4 列是保底布局，卡片等比压缩（下限 110*scale）确保放得下。
        注意：
        - 必须用滚动区宽度而非网格容器宽度：页面隐藏时容器会保留
          旧尺寸（被自身最小宽度撑住），据其算列数会导致小窗口下右侧被截；
        - 必须预留竖滚动条宽度而非直接用视口宽度：若按视口算，
          "滚动条出现→视口变窄→列数减一→内容变矮→滚动条消失"会形成
          振荡死循环，表现为页面疯狂闪烁；
        - 滚动条宽度不能用 verticalScrollBar().width()：滚动条从未显示时
          仍是默认构造尺寸（宽约 100px），会把可用宽度虚扣一大截，
          导致刚启动进入歌单页时卡片整体小一圈。此处直接用样式表固定值。"""
        grid = self._playlist_cards_grid
        margins = grid.contentsMargins()
        avail = (self._playlist_cards_scroll.width()
                 - 8  # 竖滚动条样式宽度（见 _build_playlist_cards_widget 的 QSS）
                 - margins.left() - margins.right())
        spacing = grid.spacing()
        base = int(150 * self.scale)
        min_s = int(110 * self.scale)
        cols = max(4, avail // (base + spacing))
        s = max(min_s, min(base, (avail - (cols - 1) * spacing) // cols))
        return cols, s

    def _relayout_playlist_cards(self):
        """按宽度自适应列数排布歌单卡片，严格从左到右填充，不居中、不跳位。

        第 0 位（左上角）= 新建歌单卡片（固定）。
        第一行排满后进入第二行，以此类推。
        多余空间全部由右侧隐身占位列吸收，保持左对齐。
        """
        names = self._sorted_playlist_names()
        self._playlist_card_map = {}
        cols, card_sz = self._playlist_layout_metrics()
        self._playlist_layout = (cols, card_sz)
        self._playlist_card_sz = card_sz  # 供 _playlist_card_size 取当前尺寸
        total_cells = 1 + len(names)     # 新建卡片 + 所有歌单卡片
        rows = (total_cells + cols - 1) // cols
        # 清空网格。deleteLater 是延迟删除，必须先 hide() 立即从视觉上移除，
        # 否则窗口缩放触发重排时新旧两组卡片会同屏
        while self._playlist_cards_grid.count():
            item = self._playlist_cards_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        # 新建歌单卡片 —— 永远在第 0 位（row=0, col=0）
        self._playlist_cards_grid.addWidget(self._make_new_playlist_card(), 0, 0)
        # 歌单卡片从左到右依次填入：第 1 位开始（row=0, col=1）
        for i, name in enumerate(names):
            card = self._make_playlist_card(name)
            self._playlist_card_map[name] = card
            pos = i + 1                     # 0 被新建卡占用
            self._playlist_cards_grid.addWidget(card, pos // cols, pos % cols)
        # 右侧占位列：透明 widget，跨越所有行，吸收多余水平空间，让卡片组左对齐
        # 注意：不能用 setFixedWidth，否则 stretch 失效；改用 Expanding sizePolicy。
        spacer = QWidget()
        spacer.setMinimumWidth(1)
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        spacer.setStyleSheet("background:transparent;")
        self._playlist_cards_grid.addWidget(spacer, 0, cols, rows, 1)
        # 清除 stretch → 仅右侧占位列 + 底行 stretch
        for c in range(cols + 2):
            self._playlist_cards_grid.setColumnStretch(c, 0)
        for r in range(rows + 1):
            self._playlist_cards_grid.setRowStretch(r, 0)
        self._playlist_cards_grid.setColumnStretch(cols, 1)
        self._playlist_cards_grid.setRowStretch(rows, 1)

    def _sorted_playlist_names(self):
        """按当前卡片排序方式返回歌单名列表。无时间记录的歌单时间戳记 0。"""
        mode = self._card_sort
        names = list(self._playlists.keys())
        if mode == "mtime_asc":
            names.sort(key=lambda nm: self._playlist_meta.get(nm, 0))
        elif mode == "name_asc":
            names.sort(key=lambda nm: nm.lower())
        elif mode == "name_desc":
            names.sort(key=lambda nm: nm.lower(), reverse=True)
        else:  # "mtime_desc"（默认）
            names.sort(key=lambda nm: self._playlist_meta.get(nm, 0),
                       reverse=True)
        return names

    def _apply_card_sort(self, mode):
        """应用卡片排序方式：保存偏好并重新布局（保持卡片页、不进详情页）"""
        if self._card_sort == mode:
            return
        self._card_sort = mode
        self._save_settings()
        self._relayout_playlist_cards()

    def _pop_card_sort_menu(self):
        """弹出卡片排序菜单（右上角按钮），可切换卡片排序方式，类似歌曲排序菜单。"""
        menu = QMenu(self)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint)
        menu.setStyleSheet(f"""
            QMenu {{ background:#FFFFFF; border:1px solid #E0E0E0;
                border-radius:{int(8*self.scale)}px; padding:4px; }}
            QMenu::item {{ padding:{int(6*self.scale)}px {int(14*self.scale)}px;
                font-size:{int(14*self.scale)}px; color:#1A1A1A;
                border-radius:{int(5*self.scale)}px; }}
            QMenu::item:selected {{ background:#F0F0F2; }}
            QMenu::separator {{ height:1px; background:#EEEEEE; margin:3px 6px; }}
        """)
        opts = [
            ("mtime_desc", "修改时间（新→旧）"),
            ("mtime_asc", "修改时间（旧→新）"),
            ("name_asc", "名称（A→Z）"),
            ("name_desc", "名称（Z→A）"),
        ]
        for mode, label in opts:
            act = QAction(label, menu)
            act.setCheckable(True)
            act.setChecked(mode == self._card_sort)
            act.setData(mode)
            act.triggered.connect(
                lambda checked, m=mode: self._apply_card_sort(m))
            menu.addAction(act)
        menu.adjustSize()
        win_geo = self.geometry()
        mw, mh = menu.width(), menu.height()
        btn = self._card_sort_btn
        btn_top = btn.mapToGlobal(QPoint(0, 0)).y()
        btn_center = btn_top + btn.height() / 2
        win_center = win_geo.y() + win_geo.height() / 2
        pt_x = win_geo.x() + win_geo.width() - mw - int(15 * self.scale)
        pt_y = (btn_top - mh) if btn_center > win_center else (btn_top + btn.height())
        pt_y = max(win_geo.y() + int(4*self.scale),
                   min(pt_y, win_geo.y() + win_geo.height() - mh - int(4*self.scale)))
        menu.exec_(QPoint(pt_x, pt_y))

    def _scroll_card_to_visible(self, name):
        """重命名后，若目标卡片不在当前可视区域，平滑滚动到可见位置。

        同时支持向上滚动（卡片在可视区上方）与向下滚动（卡片在可视区下方）。
        由于重命名后会立即重建卡片网格，布局几何可能尚未刷新，故延迟一帧
        （QTimer.singleShot(0)）再读取坐标，确保 card.y() / viewport 高度准确。
        参考播放列表 _scroll_playlist_to_playing 的做法：隐藏时跳过，待显示后重算。"""
        card = self._playlist_card_map.get(name)
        if card is None:
            return
        scroll = self._playlist_cards_scroll
        # 页面隐藏时坐标系异常，跳过；委托到显示流程后再次触发
        if not self._playlist_page.isVisible():
            return
        QTimer.singleShot(0, lambda: self._scroll_card_to_visible_now(name))

    def _scroll_card_to_visible_now(self, name):
        card = self._playlist_card_map.get(name)
        if card is None:
            return
        scroll = self._playlist_cards_scroll
        sb = scroll.verticalScrollBar()
        vp_h = scroll.viewport().height()
        if vp_h <= 0:
            return
        # 卡片在内容坐标系中的位置（card 的父即滚动内容根 widget，坐标系一致）
        card_top = card.y()
        card_bottom = card.y() + card.height()
        vis_top = sb.value()
        vis_bottom = vis_top + vp_h
        if vis_top <= card_top and card_bottom <= vis_bottom:
            return  # 已完全可见，无需滚动
        # 目标：卡片在下方则向下滚动贴近视口底部；在上方则向上滚动贴近视口顶部
        if card_bottom > vis_bottom:
            target = card_bottom - vp_h            # 下滑，整张卡片进入视口底缘
        else:
            target = card_top                       # 上滑，整张卡片进入视口顶缘
        target = max(sb.minimum(), min(target, sb.maximum()))
        if abs(target - sb.value()) < 1:
            return
        # 平滑动画
        anim = QPropertyAnimation(sb, b"value", self)
        anim.setDuration(int(280 * self.scale))
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.setStartValue(sb.value())
        anim.setEndValue(target)
        anim.start()
        self._card_scroll_anim = anim

    def _playlist_card_size(self):
        """歌单卡片封面尺寸（圆角正方形，宽 = 高）。

        布局时会按窗口宽度压缩（见 _playlist_layout_metrics），
        未布局前回退原始尺寸。"""
        s = getattr(self, '_playlist_card_sz', None)
        if s is None:
            s = int(150 * self.scale)
        return s, s

    def _make_new_playlist_card(self):
        """第一格固定卡片：新建歌单（虚线边框 + 大号 ＋ 图标）。

        与歌单卡片同构：封面区(150) + 底部占位行，保证两张卡片整体高度
        一致、上下对齐。"""
        s, _ = self._playlist_card_size()
        name_row_h = int(22 * self.scale)  # 与歌单卡片底部名称行等高
        card = QWidget(self._playlist_cards_widget)
        card.setFixedWidth(s)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(int(6 * self.scale))

        # 封面按钮：虚线圆角框，内部为大加号 + 文字
        btn = QPushButton(card)
        btn.setFixedSize(s, s)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFocusPolicy(Qt.NoFocus)
        reg_theme(btn, f"""
            QPushButton {{
                background: transparent;
                border: 2px dashed #CCCCCC;
                border-radius: {int(10 * self.scale)}px;
            }}
            QPushButton:hover {{
                border-color: #EC4141;
            }}
            /* 加号与文字随按钮 hover 变红 */
            QPushButton QLabel {{
                color: #BBBBBB; background: transparent; border: none;
            }}
            QPushButton:hover QLabel {{
                color: #EC4141;
            }}
        """)
        # 内层内容（大加号 + 新建歌单），不拦截鼠标事件
        inner = QWidget(btn)
        inner.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        il = QVBoxLayout(inner)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(0)
        il.addStretch(1)
        plus = QLabel("＋", inner)
        plus.setAlignment(Qt.AlignCenter)
        plus.setStyleSheet(
            f"font-size: {int(46 * self.scale)}px;"
            f"background: transparent; border: none;")
        il.addWidget(plus, 0, Qt.AlignCenter)
        txt = QLabel("新建歌单", inner)
        txt.setAlignment(Qt.AlignCenter)
        txt.setStyleSheet(
            f"font-size: {int(15 * self.scale)}px;"
            f"background: transparent; border: none;")
        il.addWidget(txt, 0, Qt.AlignCenter)
        il.addStretch(1)
        inner.setGeometry(0, 0, s, s)
        btn.clicked.connect(lambda: self._create_new_playlist())
        lay.addWidget(btn, 0, Qt.AlignHCenter)

        # 底部占位行（与歌单卡片名称行同高），保证两卡整体对齐
        spacer = QWidget(card)
        spacer.setFixedHeight(name_row_h)
        lay.addWidget(spacer)
        return card

    def _make_playlist_card(self, name):
        """创建歌单卡片：封面（最新歌曲封面/占位图）+ 下方歌单名 + 更多按钮。"""
        s, _ = self._playlist_card_size()
        radius = int(10 * self.scale)
        card = QWidget(self._playlist_cards_widget)
        card.setFixedWidth(s)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(int(6 * self.scale))

        # 封面（QLabel + 圆角 pixmap，可点击打开歌单）
        cover_lbl = QLabel(card)
        cover_lbl.setFixedSize(s, s)
        cover_lbl.setCursor(Qt.PointingHandCursor)
        cover_lbl.setStyleSheet(
            f"QLabel {{ background: #F0F0F2; border: none;"
            f"border-radius: {radius}px; }}")
        cover_lbl.mousePressEvent = lambda e: self._open_playlist(name)
        lay.addWidget(cover_lbl, 0, Qt.AlignHCenter)

        # 底部：歌单名 + 更多按钮
        bottom = QWidget(card)
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(2, 0, 0, 0)
        bl.setSpacing(0)
        name_btn = QPushButton(name, bottom)
        name_btn.setCursor(Qt.PointingHandCursor)
        name_btn.setFocusPolicy(Qt.NoFocus)
        reg_theme(name_btn, 
            f"QPushButton {{ background: transparent; border: none;"
            f"color: #1A1A1A; font-size: {int(14 * self.scale)}px;"
            f"text-align: left; }}"
            f"QPushButton:hover {{ color: #EC4141; }}")
        # 超长歌单名省略号
        max_w = s - int(28 * self.scale)
        elided = name_btn.fontMetrics().elidedText(
            name, Qt.ElideRight, max_w)
        name_btn.setText(elided)
        name_btn.clicked.connect(lambda: self._open_playlist(name))
        bl.addWidget(name_btn, 0, Qt.AlignLeft | Qt.AlignVCenter)
        bl.addStretch(1)
        more_btn = QPushButton(bottom)
        more_btn.setFixedSize(int(22 * self.scale), int(22 * self.scale))
        more_btn.setCursor(Qt.PointingHandCursor)
        more_btn.setFocusPolicy(Qt.NoFocus)
        more_btn.setIcon(self._render_svg_icon(
            "more-horizontal.svg", "#666666", int(18 * self.scale)))
        more_btn.setIconSize(QSize(int(18 * self.scale), int(18 * self.scale)))
        more_btn.setStyleSheet(
            "QPushButton { background: transparent; border: none; }"
            "QPushButton:hover { background: #F0F0F2; border-radius: 4px; }")
        more_btn.clicked.connect(
            lambda checked, n=name, b=more_btn: self._show_playlist_menu(n, b))
        bl.addWidget(more_btn, 0, Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(bottom)

        # 封面异步加载：取歌单内最新歌曲封面；无则占位图
        songs = self._playlists.get(name, [])
        cover_url, sid = "", None
        for song in reversed(songs):  # 最新添加的在末尾
            if song.get('cover_url') and song.get('song_id'):
                cover_url, sid = song['cover_url'], song['song_id']
                break
        self._load_playlist_card_cover(cover_lbl, cover_url, sid)
        return card

    def _load_playlist_card_cover(self, cover_lbl, cover_url, sid):
        """歌单卡片封面：先显示占位图，有在线封面则后台下载后替换。"""
        self._set_card_cover(
            cover_lbl, image_path("no_cover.png"))
        if cover_url and sid and self.online_api:
            self._run_in_thread(
                self.online_api.download_cover,
                lambda res: self._set_card_cover(cover_lbl, res),
                cover_url, sid)

    def _set_card_cover(self, cover_lbl, cover_path):
        """把封面路径渲染为高清圆角图片设置到封面 QLabel。

        直接 setPixmap（带 DPR 的物理分辨率位图）避免 QIcon 二次缩放产生
        像素点；控件被重建销毁时安全忽略。"""
        try:
            if not cover_path or not os.path.exists(cover_path):
                return
            pix = QPixmap(cover_path)
            if pix.isNull():
                return
            s = cover_lbl.width() or int(150 * self.scale)
            radius = int(10 * self.scale)
            dpr = self.devicePixelRatio() or 1.0
            out = QPixmap(max(1, int(s * dpr)), max(1, int(s * dpr)))
            out.setDevicePixelRatio(dpr)
            out.fill(Qt.transparent)
            p = QPainter(out)
            try:
                p.setRenderHint(QPainter.Antialiasing)
                p.setRenderHint(QPainter.SmoothPixmapTransform)
                path = QPainterPath()
                path.addRoundedRect(0, 0, s, s, radius, radius)
                p.setClipPath(path)
                p.drawPixmap(QRect(0, 0, s, s), pix)
            finally:
                p.end()
            cover_lbl.setPixmap(out)
        except RuntimeError:
            pass  # 卡片已被重建销毁，忽略

    def _browse_playlists(self):
        """歌单母列表：棋盘卡片（第一格新建歌单 + 歌单封面卡片 + 更多菜单）"""
        self._load_playlists()
        self._playlist_browsing = True
        self._playlist_current = None
        self.toolbar.hide()
        # 显示歌单卡片容器、隐藏歌曲表格与其他卡片容器
        self.song_table.hide()
        if hasattr(self, '_toplist_cards_widget'):
            self._toplist_cards_widget.hide()
        self._playlist_page.show()
        self._relayout_playlist_cards()
        self.setWindowTitle("VeryGoodPlayer · 我的歌单")
        # 切回侧栏时恢复歌单卡片页滚动位置
        self._maybe_restore_scroll("📋 我的歌单#browse")

    def _btn_style(self, color="#999999"):
        return f"""
            QPushButton {{
                background: transparent; border: none;
                color: {color}; font-size: {int(12*self.scale)}px;
                border-radius: {int(11*self.scale)}px;
            }}
            QPushButton:hover {{ color: #EC4141; background: #F0F0F2; }}
        """

    def _show_playlist_menu(self, name, button):
        """歌单卡片「更多」按钮菜单：重命名 / 删除。

        沿用歌曲子菜单的样式（_add_menu_item），但使用独立的菜单跟踪状态
        （_playlist_menu_open / _playlist_menu_btn），与歌曲菜单互不影响。"""
        # 重复点击同一按钮且菜单仍打开 → 保持现状，不重新弹出
        prev_menu = getattr(self, '_playlist_menu_open', None)
        if prev_menu is not None:
            try:
                if (getattr(self, '_playlist_menu_btn', None) is button
                        and prev_menu.isVisible()):
                    return
            except RuntimeError:
                pass
            try:
                prev_menu.hide()
            except RuntimeError:
                pass
            try:
                prev_menu.deleteLater()
            except RuntimeError:
                pass
            self._playlist_menu_open = None
            self._playlist_menu_btn = None
        # 以主窗口为父创建菜单，避免以 toolbar 按钮为父时，菜单 popup 落入
        # 带 QGraphicsDropShadowEffect 的祖先容器渲染链，导致每个菜单项下边缘
        # 出现灰色阴影（详情页封面大图带阴影 effect，歌曲菜单所在视图无此祖先故无阴影）
        menu = QMenu(self)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint)
        menu.setStyleSheet(f"""
            QMenu {{
                background: #FFFFFF; border: 1px solid #E0E0E0;
                border-radius: {int(6*self.scale)}px;
                padding: {int(5*self.scale)}px {int(6*self.scale)}px;
                font-size: {int(14*self.scale)}px;
            }}
            QMenu::separator {{
                height: {int(1*self.scale)}px;
                background: #EBEBEB;
                margin: {int(3*self.scale)}px {int(12*self.scale)}px;
            }}
        """)
        menu_specs = [
            ("重命名", "pen-line.svg",
             lambda: self._rename_playlist(name), True),
            ("删除", "trash.svg",
             lambda: self._delete_playlist(name), True),
        ]
        for i, (text, svg, cb, enabled) in enumerate(menu_specs):
            if i > 0:
                menu.addSeparator()
            self._add_menu_item(menu, text, svg, cb, enabled=enabled)
        menu.adjustSize()
        self._track_menu_hover(menu, getattr(menu, '_song_menu_items', []))
        # 定位在主窗口内（参照歌曲菜单，避免超出窗口）
        win_geo = self.geometry()
        mw, mh = menu.width(), menu.height()
        btn_top = button.mapToGlobal(QPoint(0, 0)).y()
        btn_center = btn_top + button.height() / 2
        win_center = win_geo.y() + win_geo.height() / 2
        if btn_center > win_center:
            pt_y = btn_top - mh
        else:
            pt_y = btn_top + button.height()
        if pt_y < win_geo.y() + int(4*self.scale):
            pt_y = win_geo.y() + int(4*self.scale)
        if pt_y + mh > win_geo.y() + win_geo.height():
            pt_y = win_geo.y() + win_geo.height() - mh - int(4*self.scale)
        # 水平：跟随更多按钮，菜单右边缘对齐按钮右边缘 + 小偏移
        # （与主表格歌曲菜单的"窗口右对齐"定位逻辑区分开）
        btn_right = button.mapToGlobal(QPoint(button.width(), 0)).x()
        pt_x = btn_right - mw + int(4 * self.scale)
        if pt_x < win_geo.x() + int(4 * self.scale):
            pt_x = win_geo.x() + int(4 * self.scale)
        self._playlist_menu_open = menu
        self._playlist_menu_btn = button
        menu.popup(QPoint(pt_x, pt_y))

    def _rename_playlist(self, old_name):
        name = self._ask_playlist_name(self, "修改名称")
        if not name or name == old_name:
            return
        self._playlists[name] = self._playlists.pop(old_name)
        if old_name in self._playlist_meta:
            self._playlist_meta[name] = self._playlist_meta.pop(old_name)
        else:
            self._playlist_meta[name] = time.time()
        self._save_playlists()
        old_menu = "📋 " + old_name
        new_menu = "📋 " + name
        # 如果当前正在查看该歌单（详情页），仅更新标题与各内部键绑定，
        # 不跳回卡片页；否则（在卡片页）刷新卡片列表本身
        if getattr(self, '_playlist_current', None) == old_name:
            self._playlist_current = name
            self._playlist_title_lbl.setText(name)
            self._current_playlist_menu = new_menu
            if getattr(self, 'current_menu', None) == old_menu:
                self.current_menu = new_menu
            if old_menu in self.playlist_data:
                self.playlist_data[new_menu] = self.playlist_data.pop(old_menu)
            # 顶部更多按钮位置不变；标题刷新即可，无需重建页面
        else:
            # 停留在卡片页：只刷新卡片（不切换视图）
            self._browse_playlists()
            # 若重命名后该卡片因排序变化而移出当前可视区域，平滑滚动到可见位置
            self._scroll_card_to_visible(name)

    def _create_new_playlist(self):
        """新建歌单后停留在卡片页，不立即进入空歌单"""
        name = self._ask_playlist_name(self)
        if not name:
            return
        self._playlists[name] = []
        self._playlist_meta[name] = time.time()  # 记录创建时间
        self._save_playlists()
        # 仅刷新卡片页，停留在歌单母列表（不调用 _open_playlist 进入空歌单）
        self._browse_playlists()

    def _open_playlist(self, name):
        """打开指定歌单，显示歌曲列表（按该歌单的排序偏好排列）"""
        # 隐藏歌单卡片容器、恢复歌曲表格
        if hasattr(self, '_playlist_page'):
            self._playlist_page.hide()
        self.song_table.show()
        self._load_playlists()
        songs = []
        for s in self._playlists.get(name, []):
            songs.append({
                "song_id": s.get("song_id"),
                "name": s.get("name", ""),
                "singer": s.get("singer", ""),
                "album": s.get("album", ""),
                "duration": s.get("duration", "--:--"),
                "duration_sec": s.get("duration_sec", 0),
                "cover_url": s.get("cover_url", ""),
                "added_at": s.get("added_at", ""),
                "filepath": None,
            })
        # 应用该歌单的排序偏好（仅影响主表格显示顺序，不写回 playlists.json）
        self._sort_playlist_songs(name, songs)
        self._playlist_browsing = False
        for c in range(7):
            self.song_table.setColumnHidden(c, False)
        self._playlist_current = name
        menu_name = "📋 " + name
        self._current_playlist_menu = menu_name
        self.playlist_data[menu_name] = songs
        # 工具栏：返回 + 居中标题 + 编辑/删除
        self.toolbar.show()
        self.search_input.hide()
        # 清除旧项目
        if hasattr(self, '_playlist_back_btn'):
            for w in [self._playlist_back_btn, self._playlist_title_lbl,
                      self._playlist_top_more,
                      getattr(self, '_playlist_sort_btn', None)]:
                if w is not None:
                    try: w.deleteLater()
                    except: pass
        # 返回按钮（图标用 caret-left.svg，不再用文本左箭头）
        self._playlist_back_btn = QPushButton("返回", self.toolbar)
        reg_theme(self._playlist_back_btn, f"""
            QPushButton {{ background: transparent; border: none;
                font-size: {int(14*self.scale)}px; color: #EC4141;
                padding: {int(4*self.scale)}px {int(8*self.scale)}px;
            }}
            QPushButton:hover {{ color: #CC3333; }}
        """)
        back_icon = self._render_svg_icon("caret-left.svg", self.theme_color,
                                          int(18*self.scale))
        if back_icon is not None:
            self._playlist_back_btn.setIcon(back_icon)
            self._playlist_back_btn.setIconSize(
                QSize(int(18*self.scale), int(18*self.scale)))
        self._playlist_back_btn.setCursor(Qt.PointingHandCursor)
        self._playlist_back_btn.clicked.connect(self._back_to_playlist_list)
        # 居中标题
        self._playlist_title_lbl = QLabel(name, self.toolbar)
        self._playlist_title_lbl.setAlignment(Qt.AlignCenter)
        self._playlist_title_lbl.setStyleSheet(f"""
            font-size: {int(14*self.scale)}px; font-weight: bold;
            color: #1A1A1A; border: none; background: transparent;
        """)
        # 排序按钮（放在编辑/删除左侧，样式与"换一批"/收藏排序一致）
        self._playlist_sort_btn = QPushButton("排序", self.toolbar)
        reg_theme(self._playlist_sort_btn, f"""
            QPushButton {{ background: transparent; border: none;
                font-size: {int(14*self.scale)}px; color: #EC4141;
                text-decoration: underline;
                padding: {int(4*self.scale)}px {int(6*self.scale)}px;
            }}
            QPushButton:hover {{ color: #CC3333; }}
        """)
        self._playlist_sort_btn.setCursor(Qt.PointingHandCursor)
        # 更多按钮（替换原来的编辑/删除，点击打开歌单菜单：重命名 / 删除）
        self._playlist_top_more = QPushButton(self.toolbar)
        self._playlist_top_more.setFixedSize(28, 28)
        self._playlist_top_more.setCursor(Qt.PointingHandCursor)
        self._playlist_top_more.setIcon(self._render_more_icon(28))
        self._playlist_top_more.setIconSize(QSize(int(16*self.scale), int(16*self.scale)))
        self._playlist_top_more.setStyleSheet(self._btn_style())
        self._playlist_top_more.clicked.connect(
            lambda: self._show_playlist_menu(name, self._playlist_top_more))
        # 排序菜单（样式与歌曲子菜单一致，去掉系统阴影）
        sort_menu = QMenu(self.toolbar)
        sort_menu.setWindowFlags(sort_menu.windowFlags() | Qt.FramelessWindowHint)
        reg_theme(sort_menu, f"""
            QMenu {{
                background: #FFFFFF; border: 1px solid #E0E0E0;
                border-radius: {int(6*self.scale)}px;
                padding: {int(5*self.scale)}px {int(6*self.scale)}px;
                font-size: {int(14*self.scale)}px;
            }}
            QMenu::item {{
                padding: {int(8*self.scale)}px {int(24*self.scale)}px;
                margin: {int(1*self.scale)}px 0;
                color: #1A1A1A;
                border-radius: {int(4*self.scale)}px;
            }}
            QMenu::item:hover, QMenu::item:selected {{
                background-color: #F0F0F2;
                color: #EC4141;
            }}
        """)
        sort_options = [
            ("添加时间（新→旧）", "time_desc"),
            ("添加时间（旧→新）", "time_asc"),
            ("曲名（A→Z）", "name_asc"),
            ("曲名（Z→A）", "name_desc"),
            ("歌手（A→Z）", "singer_asc"),
            ("歌手（Z→A）", "singer_desc"),
        ]
        for label, mode in sort_options:
            act = sort_menu.addAction(label)
            act.setCheckable(True)
            act.setData(mode)
        self._playlist_sort_btn.clicked.connect(
            lambda: self._pop_playlist_sort_menu(name, self._playlist_sort_btn, sort_menu))
        # 清空布局，重建：返回  stretch  标题  stretch  排序 编辑 删除
        while self.toolbar_layout.count():
            item = self.toolbar_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
        self.toolbar_layout.addWidget(self._playlist_back_btn, 0)
        self.toolbar_layout.addStretch(1)
        self.toolbar_layout.addWidget(self._playlist_title_lbl, 0)
        self.toolbar_layout.addStretch(1)
        self.toolbar_layout.addWidget(self._playlist_sort_btn, 0)
        self.toolbar_layout.addWidget(self._playlist_top_more, 0)
        self._playlist_back_btn.show()
        self._playlist_title_lbl.show()
        self._playlist_sort_btn.show()
        self._playlist_top_more.show()
        self.display_playlist(menu_name)

    def _sort_playlist_songs(self, name, songs):
        """按歌单的排序偏好排列歌曲（仅影响主表格显示顺序）"""
        mode = self._playlist_sort.get(name, "none")
        if mode == "none":
            return  # 默认顺序，保持歌单原始排列
        if mode in ("time_desc", "time_asc"):
            # 按添加时间排序；无 added_at 的旧数据（空串）固定排最后
            songs.sort(key=lambda s: s.get("added_at", ""), reverse=(mode == "time_desc"))
            songs.sort(key=lambda s: 0 if s.get("added_at") else 1)  # 稳定分组，无时间移末尾
        elif mode == "name_desc":
            songs.sort(key=lambda s: s.get("name", ""), reverse=True)
        elif mode == "name_asc":
            songs.sort(key=lambda s: s.get("name", ""))
        elif mode == "singer_desc":
            songs.sort(key=lambda s: s.get("singer", ""), reverse=True)
        elif mode == "singer_asc":
            songs.sort(key=lambda s: s.get("singer", ""))

    def _apply_playlist_sort(self, name, mode):
        """应用歌单排序方式：保存偏好并重新打开歌单刷新显示"""
        if self._playlist_sort.get(name) == mode:
            return
        self._playlist_sort[name] = mode
        self._save_settings()  # 实时写入配置文件
        self._open_playlist(name)

    def _pop_playlist_sort_menu(self, name, btn, menu):
        """在排序按钮附近弹出歌单排序菜单（不超出主窗口，不与其他按钮重叠）"""
        # 刷新勾选状态
        cur = self._playlist_sort.get(name, "none")
        for act in menu.actions():
            act.setChecked(act.data() == cur)
        menu.adjustSize()
        win_geo = self.geometry()
        mw, mh = menu.width(), menu.height()
        # 水平：菜单右边缘距窗口右边缘 15px
        pt_x = win_geo.x() + win_geo.width() - mw - int(15 * self.scale)
        if pt_x < win_geo.x():
            pt_x = win_geo.x() + int(10 * self.scale)
        # 垂直：按钮在窗口下半部则向上弹出，否则向下
        btn_top = btn.mapToGlobal(QPoint(0, 0)).y()
        btn_center = btn_top + btn.height() / 2
        win_center = win_geo.y() + win_geo.height() / 2
        if btn_center > win_center:
            pt_y = btn_top - mh
        else:
            pt_y = btn_top + btn.height()
        if pt_y < win_geo.y() + int(4 * self.scale):
            pt_y = win_geo.y() + int(4 * self.scale)
        if pt_y + mh > win_geo.y() + win_geo.height():
            pt_y = win_geo.y() + win_geo.height() - mh - int(4 * self.scale)
        chosen = menu.exec_(QPoint(pt_x, pt_y))
        if chosen is not None:
            mode = chosen.data()
            if mode:
                self._apply_playlist_sort(name, mode)

    # 排行榜卡片渐变色板（不同榜单不同渐变背景）
    _TOPLIST_CARD_GRADIENTS = [
        ("#FF5F6D", "#FFC371"),  # 珊瑚红 → 杏黄
        ("#7F00FF", "#E100FF"),  # 紫 → 洋红
        ("#0BA360", "#3CBA92"),  # 翡翠绿
        ("#FF8008", "#FFC837"),  # 活力橙黄（说唱榜）
        ("#F953C6", "#B91D73"),  # 粉紫
        ("#00B4DB", "#0083B0"),  # 青蓝
        ("#FF512F", "#DD2476"),  # 橙红
        ("#11998E", "#38EF7D"),  # 青绿
        ("#FC5C7D", "#6A82FB"),  # 玫粉 → 蓝
        ("#4568DC", "#B06AB3"),  # 蓝 → 紫
        ("#E65C00", "#F9D423"),  # 火焰橙
        ("#2E3192", "#1BFFFF"),  # 靛蓝 → 天蓝
    ]

    def _build_toplist_cards_widget(self):
        """排行榜母列表：封面棋盘网格容器（圆角渐变卡片）。"""
        self._toplist_cards_widget = QWidget()
        self._toplist_cards_widget.setObjectName("toplistCards")
        self._toplist_cards_widget.setStyleSheet(
            "QWidget#toplistCards { background: transparent; }")
        self._toplist_cards_grid = QGridLayout(self._toplist_cards_widget)
        self._toplist_cards_grid.setContentsMargins(20, 20, 20, 20)
        self._toplist_cards_grid.setSpacing(18)  # 卡片间距
        self._toplist_cards_cols = 0  # 0=尚未布局，首次进入按容器宽度计算列数

    def _clear_toplist_cards(self):
        """移除当前所有排行榜卡片。"""
        while self._toplist_cards_grid.count():
            item = self._toplist_cards_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                # deleteLater 是延迟删除，事件循环处理前旧卡片仍挂在容器上可见，
                # 必须先 hide() 立即从视觉上移除，避免窗口缩放时新旧两组卡片同屏
                w.hide()
                w.deleteLater()
        self._toplist_cards_grid.setRowStretch(0, 0)

    def _relayout_toplist_cards(self):
        """按 4×3 固定棋盘排布卡片；卡片随窗口拉伸填满网格，
        最大化时不留大片四周空白。"""
        if not TOPLIST_IDS:
            return
        cols = 4  # 固定 4 列（榜单共 12 个，恰为 4×3），最大化也保持该布局
        # 列数固定，仅在首次进入（_browse_toplist 置 0 强制重建）时执行；
        # 窗口缩放由网格 stretch 自动分配，无需重建卡片
        if self._toplist_cards_cols == cols:
            return
        self._toplist_cards_cols = cols
        # 清空网格并重新添加卡片
        self._clear_toplist_cards()
        names = list(TOPLIST_IDS.keys())
        rows = (len(names) - 1) // cols + 1
        for i, name in enumerate(names):
            btn = self._make_toplist_card(name)
            self._toplist_cards_grid.addWidget(btn, i // cols, i % cols)
        # 全部行列等分拉伸，卡片铺满容器
        for c in range(cols):
            self._toplist_cards_grid.setColumnStretch(c, 1)
        for r in range(rows):
            self._toplist_cards_grid.setRowStretch(r, 1)
        self._adjust_toplist_spacing()

    def _adjust_toplist_spacing(self):
        """按容器宽度动态调整棋盘边距与卡片间距。

        窗口越大边距/间距越大（有上限），最大化时棋盘既能铺开
        又不至于挤满整个页面；尺寸未变化时不触发布局刷新。"""
        w = self._toplist_cards_widget.width()
        margin = int(min(72 * self.scale, 20 * self.scale + w * 0.02))
        spacing = int(min(36 * self.scale, 18 * self.scale + w * 0.01))
        g = self._toplist_cards_grid
        if g.contentsMargins().left() == margin and g.spacing() == spacing:
            return
        g.setContentsMargins(margin, margin, margin, margin)
        g.setSpacing(spacing)

    @staticmethod
    def _wrap_card_name(name):
        """超长榜单名自动换行（两行均衡、居中显示）。

        优先在 ASCII/中文边界处断开，如"Beatport全球电子舞曲榜" →
        "Beatport\\n全球电子舞曲榜"（在"全球电子舞曲榜"前换行）。"""
        if len(name) <= 8:
            return name
        m = re.match(r'^[A-Za-z0-9 ]+', name)
        if m and m.end() < len(name) and len(m.group()) >= 3:
            return name[:m.end()] + "\n" + name[m.end():]
        half = len(name) // 2
        return name[:half] + "\n" + name[half:]

    def _make_toplist_card(self, name):
        """创建一个排行榜卡片按钮（圆角矩形 + 渐变背景 + 白字居中）。"""
        idx = list(TOPLIST_IDS.keys()).index(name)
        c1, c2 = self._TOPLIST_CARD_GRADIENTS[idx % len(self._TOPLIST_CARD_GRADIENTS)]
        # hover 时轻微提亮
        c1h = QColor(c1).lighter(115).name()
        c2h = QColor(c2).lighter(115).name()
        radius = int(10 * self.scale)
        card = QPushButton(self._wrap_card_name(name), self._toplist_cards_widget)
        card.setCursor(Qt.PointingHandCursor)
        # 不固定尺寸：跟随网格单元拉伸，最大化时棋盘铺满页面不留四周空白
        card.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        card.setMinimumHeight(int(110 * self.scale))
        card.setFocusPolicy(Qt.NoFocus)
        card.setStyleSheet(f"""
            QPushButton {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {c1}, stop:1 {c2});
                color: #FFFFFF; border: none;
                border-radius: {radius}px;
                font-size: {int(15 * self.scale)}px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {c1h}, stop:1 {c2h});
            }}
            QPushButton:pressed {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 {QColor(c1).darker(115).name()},
                    stop:1 {QColor(c2).darker(115).name()});
            }}
        """)
        toplist_id = TOPLIST_IDS.get(name)
        card.clicked.connect(
            lambda _, n=name, t=toplist_id: self._show_toplist_songs(t, n))
        return card

    def _browse_toplist(self):
        """排行榜母列表：封面棋盘网格（圆角渐变卡片）"""
        self._toplist_browsing = True
        self._toplist_viewing_songs = False
        self.toolbar.hide()
        # 显示卡片容器、隐藏歌曲表格
        self.song_table.hide()
        self._toplist_cards_widget.show()
        self._toplist_cards_cols = 0  # 强制重建
        self._relayout_toplist_cards()
        self.setWindowTitle("VeryGoodPlayer · 排行榜")
        # 切回侧栏时恢复排行榜卡片页滚动位置
        self._maybe_restore_scroll("📊 排行榜#browse")

    def _back_to_toplist_list(self):
        """从排行榜歌曲详情返回排行榜列表"""
        if hasattr(self, '_toplist_back_btn'):
            self._toplist_back_btn.hide()
        if hasattr(self, '_toplist_title_lbl'):
            self._toplist_title_lbl.hide()
        self._browse_toplist()

    def _back_to_playlist_list(self):
        self._playlist_back_btn.hide()
        self._browse_playlists()

    def _delete_playlist(self, name):
        """删除歌单"""
        dlg = QDialog(self)
        dlg.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        dlg.setAttribute(Qt.WA_TranslucentBackground, True)
        dlg.setMinimumSize(int(260*self.scale), int(120*self.scale))
        outer = QVBoxLayout(dlg)
        outer.setContentsMargins(0, 0, 0, 0)
        container = QWidget(dlg)
        container.setStyleSheet(f"""
            QWidget#dialogContainer {{
                background-color: #FFFFFF; border-radius: {int(10*self.scale)}px;
                border: 1px solid #DCDCDC;
            }}
        """)
        container.setObjectName("dialogContainer")
        outer.addWidget(container)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        title_row = QHBoxLayout()
        title_lbl = QLabel("删除歌单")
        title_lbl.setStyleSheet(f"font-size:{int(16*self.scale)}px; font-weight:bold; color:#1A1A1A; background:transparent; border:none;")
        title_row.addWidget(title_lbl)
        title_row.addStretch(1)
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(int(22*self.scale), int(22*self.scale))
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setStyleSheet(f"""
            QPushButton {{ background:transparent; border:none;
                font-size:{int(12*self.scale)}px; color:#999999;
                border-radius:{int(11*self.scale)}px;
            }}
            QPushButton:hover {{ background-color:#E0E0E0; color:#1A1A1A; }}
        """)
        btn_close.clicked.connect(dlg.reject)
        title_row.addWidget(btn_close)
        layout.addLayout(title_row)
        msg = QLabel(f"确定删除歌单「{name}」？")
        msg.setStyleSheet(f"font-size:{int(15*self.scale)}px; color:#666666; background:transparent; border:none;")
        msg.setWordWrap(True)
        layout.addWidget(msg)
        layout.addStretch(1)
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedSize(int(60*self.scale), int(32*self.scale))
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{ background:#F0F0F0; border:none; border-radius:{int(16*self.scale)}px;
                font-size:{int(16*self.scale)}px; color:#666666; }}
            QPushButton:hover {{ background:#E0E0E0; }}
        """)
        btn_cancel.clicked.connect(dlg.reject)
        btn_ok = QPushButton("确定")
        btn_ok.setFixedSize(int(60*self.scale), int(32*self.scale))
        btn_ok.setCursor(Qt.PointingHandCursor)
        reg_theme(btn_ok, f"""
            QPushButton {{ background:#EC4141; border:none; border-radius:{int(16*self.scale)}px;
                font-size:{int(16*self.scale)}px; color:#FFFFFF; }}
            QPushButton:hover {{ background:THEME_DARK; }}
        """)
        btn_ok.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)
        # 高度随文本内容自适应（长名称换行时自动扩展，正常内容保持最小高度）
        dlg.adjustSize()
        result = dlg.exec_()
        if result != QDialog.Accepted:
            return
        if name in self._playlists:
            del self._playlists[name]
            self._playlist_meta.pop(name, None)
            self._save_playlists()
        self._browse_playlists()

    def _add_menu_item(self, menu, text, svg_name, callback=None, enabled=True):
        """向菜单添加自定义项（QWidgetAction）：图标 QLabel + 文字 QLabel，
        间距完全可控，图标按 PM_SmallIconSize 高清渲染、1:1 显示。
        解决 QMenu 默认布局下图标贴左边缘、与文字间距过大、被二次缩放
        导致模糊的问题。"""
        # 图标尺寸（QLabel 直接显示 QPixmap，1:1 无缩放，可自由调整）
        sz = int(20 * self.scale)
        act = QWidgetAction(menu)
        w = QWidget()
        w.setObjectName("songMenuItem")
        # 整项鼠标穿透：事件回到 QMenu，使 hovered 信号与点击触发恢复工作
        w.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        lay = QHBoxLayout(w)
        pad_x = int(10 * self.scale)
        pad_y = int(7 * self.scale)
        lay.setContentsMargins(pad_x, pad_y, int(14 * self.scale), pad_y)
        lay.setSpacing(int(15 * self.scale))
        icon_lbl = None
        if svg_name:
            # zoom=1.15：放大 SVG 内容（原本仅占盒子约 13/16），使图标视觉
            # 大小接近早期 QMenu 放大绘制的效果，且保持 1:1 清晰无锯齿
            pm = self._render_svg_pixmap(svg_name, "#8A8A8A", sz, zoom=1.15)
            if pm is not None:
                icon_lbl = QLabel(w)
                icon_lbl.setPixmap(pm)
                icon_lbl.setFixedSize(sz, sz)
                lay.addWidget(icon_lbl)
        txt_lbl = QLabel(text, w)
        txt_lbl.setObjectName("songMenuItemText")
        # 显式设置字体：QLabel 不会继承菜单 QSS 的 font-size，需与正文一致
        font = QFont(QApplication.font())
        font.setPixelSize(int(14 * self.scale))
        txt_lbl.setFont(font)
        lay.addWidget(txt_lbl)
        lay.addStretch(1)
        # 图标/文字均鼠标穿透：事件回到 QMenu，使 hovered 信号与点击触发恢复工作
        for child in (icon_lbl, txt_lbl):
            if child is not None:
                child.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        # 高亮由菜单 hovered 信号手动同步（QWidgetAction 在 QMenu 中不触发
        # enter/leave，且 QSS :hover 会被 QMenu 强制高亮首项导致常亮红）
        def _set_hover(on):
            if not enabled:
                return
            w.setStyleSheet(
                f"background: {'#F0F0F2' if on else 'transparent'};"
                f"border-radius: {int(4*self.scale)}px;")
            reg_theme(txt_lbl, 
                f"color: {'#EC4141' if on else '#1A1A1A'}; background: transparent;")
        w._set_hover = _set_hover
        if not enabled:
            txt_lbl.setStyleSheet("color: #AAAAAA; background: transparent;")
        if callback is not None:
            act.triggered.connect(lambda: self._on_menu_item_triggered(menu, callback))
        act.setDefaultWidget(w)
        menu.addAction(act)
        items = getattr(menu, '_song_menu_items', None)
        if items is None:
            items = []
            menu._song_menu_items = items
        items.append((w, act))
        return act

    def _on_menu_item_triggered(self, menu, callback):
        """菜单项触发：先关闭菜单再执行操作，避免操作刷新界面时菜单仍存在"""
        menu.close()
        callback()

    def _track_menu_hover(self, menu, items):
        """widget action 在 QMenu 中无 enter/leave/hover 事件且 QSS :hover
        会被 QMenu 强制高亮首项，改用菜单 hovered 信号 + Leave 事件手动同步。"""
        def sync(hovered_act):
            for w, a in items:
                if w is not None and hasattr(w, '_set_hover'):
                    w._set_hover(a is hovered_act)
        menu.hovered.connect(sync)

        class _LeaveFilter(QObject):
            def eventFilter(self, obj, ev):
                if ev.type() == QEvent.Leave:
                    for w, _ in items:
                        if w is not None and hasattr(w, '_set_hover'):
                            w._set_hover(False)
                return False
        menu.installEventFilter(_LeaveFilter(menu))
        # 初始无高亮（由鼠标移动触发 hovered 后按需高亮）
        for w, _ in items:
            if w is not None and hasattr(w, '_set_hover'):
                w._set_hover(False)

    def _show_song_menu(self, song_info, button, show_delete=False, show_remove_from_playlist=False):
        """在按钮附近弹出歌曲子菜单（保证不超出窗口）。
        重复点击同一按钮且菜单仍打开时静默保持打开，不闪烁。"""
        # 重复点击同一按钮且菜单仍打开 → 保持现状，不重新弹出。
        # 旧菜单可能已被 Qt 销毁（表格刷新导致其 parent 按钮被删除），
        # 访问已删除对象会抛 RuntimeError，需捕获并清除残留引用。
        prev_menu = getattr(self, '_song_menu_open', None)
        if prev_menu is not None:
            try:
                if (getattr(self, '_song_menu_btn', None) is button
                        and prev_menu.isVisible()):
                    return
            except RuntimeError:
                pass  # 旧菜单已被 Qt 销毁，忽略并继续清理
            # 切换到其他按钮或旧菜单已关闭 → 关闭旧菜单并清除引用
            try:
                prev_menu.hide()
            except RuntimeError:
                pass
            try:
                prev_menu.deleteLater()
            except RuntimeError:
                pass
            self._song_menu_open = None
            self._song_menu_btn = None
        menu = QMenu(button)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint)
        menu.setStyleSheet(f"""
            QMenu {{
                background: #FFFFFF; border: 1px solid #E0E0E0;
                border-radius: {int(6*self.scale)}px;
                padding: {int(5*self.scale)}px {int(6*self.scale)}px;
                font-size: {int(14*self.scale)}px;
            }}
            QMenu::separator {{
                height: {int(1*self.scale)}px;
                background: #EBEBEB;
                margin: {int(3*self.scale)}px {int(12*self.scale)}px;
            }}
        """)
        # 收集菜单项，每项之间统一用分隔线分隔（行距一致）
        menu_specs = []
        if song_info is not None and song_info.get('song_id'):
            menu_specs.append(("添加到歌单", "copy-plus.svg",
                               lambda: self._choose_playlist(song_info), True))
        if song_info is not None:
            si = song_info
            menu_specs.append(("下一首播放", "file-text-plus.svg",
                               lambda: self._queue_to_play_next(si), True))
        # 从歌单删除：仅限浏览自定义歌单时，通过表格行内 ⋯ 按钮触发
        if show_remove_from_playlist and song_info is not None:
            si = song_info
            menu_specs.append(("从歌单删除", "trash.svg",
                               lambda: self._remove_from_playlist_data(si), True))
        # 下载项（本地歌曲不显示：无 song_id 且 filepath 存在）
        is_local = song_info is not None and not song_info.get('song_id') \
                   and song_info.get('filepath') is not None
        has_dl = not is_local
        # 删除项（仅在本地下载页面显示）
        has_del = show_delete and song_info is not None and song_info.get('filepath')
        if has_dl:
            if song_info is not None:
                si = song_info
                menu_specs.append(("下载", "arrow-down-circle.svg",
                                   lambda: self._download_song(si), True))
            else:
                menu_specs.append(("下载", "arrow-down-circle.svg", None, False))
        if has_del:
            si = song_info
            menu_specs.append(("删除", "trash.svg",
                               lambda: self._delete_local_song(si), True))
        for i, (text, svg, cb, enabled) in enumerate(menu_specs):
            if i > 0:
                menu.addSeparator()
            self._add_menu_item(menu, text, svg, cb, enabled=enabled)
        menu.adjustSize()
        self._track_menu_hover(menu, getattr(menu, '_song_menu_items', []))
        # 定位在主窗口内
        win_geo = self.geometry()
        mw, mh = menu.width(), menu.height()
        btn_center_x = button.mapToGlobal(QPoint(button.width() // 2, 0)).x()
        # 水平：按钮居中于菜单（用于底栏），否则右边缘距窗口右边缘 15px
        if getattr(button, '_menu_anchor_self', False):
            pt_x = btn_center_x - mw // 2
            if pt_x < win_geo.x() + int(4*self.scale):
                pt_x = win_geo.x() + int(4*self.scale)
            if pt_x + mw > win_geo.x() + win_geo.width() - int(4*self.scale):
                pt_x = win_geo.x() + win_geo.width() - mw - int(4*self.scale)
        else:
            pt_x = win_geo.x() + win_geo.width() - mw - int(15*self.scale)
            if pt_x < win_geo.x():
                pt_x = win_geo.x() + int(10*self.scale)
        # 垂直：按钮在窗口下半部则向上弹出，否则向下
        btn_top = button.mapToGlobal(QPoint(0, 0)).y()
        btn_center = btn_top + button.height() / 2
        win_center = win_geo.y() + win_geo.height() / 2
        if btn_center > win_center:
            pt_y = btn_top - mh
        else:
            pt_y = btn_top + button.height()
        # 不超过窗口上下边界
        if pt_y < win_geo.y() + int(4*self.scale):
            pt_y = win_geo.y() + int(4*self.scale)
        if pt_y + mh > win_geo.y() + win_geo.height():
            pt_y = win_geo.y() + win_geo.height() - mh - int(4*self.scale)
        # 非模态弹出并记录当前菜单：重复点击同一按钮时静默保持打开
        self._song_menu_open = menu
        self._song_menu_btn = button
        menu.aboutToHide.connect(lambda: self._on_song_menu_closed(menu))
        menu.popup(QPoint(pt_x, pt_y))

    def _on_song_menu_closed(self, menu):
        """菜单关闭后清除状态引用"""
        if getattr(self, '_song_menu_open', None) is menu:
            self._song_menu_open = None
            self._song_menu_btn = None

    # ---------- 下载 ----------
    def _download_song(self, song_info):
        """下载完整歌曲到 songs 文件夹"""
        if not HAS_REQUESTS:
            self._show_toast("缺少 requests 库，无法下载", 3000)
            return
        song_id = song_info.get('song_id')
        if not song_id:
            return
        # 检查是否已下载
        safe = re.sub(r'[\\/:*?"<>|]', '_', song_info.get('name', str(song_id))).strip()
        if not safe:
            safe = str(song_id)
        for ext_candidate in ['.flac', '.mp3']:
            existing = os.path.join(self.local_folder, f"{safe}{ext_candidate}")
            if os.path.exists(existing):
                self._show_toast("歌曲已下载", 2000)
                return
        # 后台获取地址并下载
        self._download_loading = True
        self._show_loading("正在下载...", "download")
        def task():
            for level in ["lossless", "higher", "standard"]:
                url, size, trial, err = self.online_api.get_song_url(song_id, level=level)
                if url and not err:
                    break
            if err or not url:
                return None, "暂无版权，无法下载"
            if trial is not None:
                return None, "暂无版权，无法下载"
            try:
                r = requests.get(url, timeout=60)
                if r.status_code != 200:
                    return None, "下载失败"
                # 根据内容类型决定扩展名
                ct = r.headers.get('content-type', '').lower()
                if 'flac' in ct:
                    ext = '.flac'
                elif 'mpeg' in ct or 'mp3' in ct:
                    ext = '.mp3'
                elif 'wav' in ct:
                    ext = '.wav'
                elif 'ogg' in ct:
                    ext = '.ogg'
                else:
                    ext = '.mp3'
                dl_dir = self.local_folder
                fpath = os.path.join(dl_dir, f"{safe}{ext}")
                base = fpath
                n = 1
                while os.path.exists(fpath):
                    fpath = f"{base.rsplit('.', 1)[0]}_{n}.{ext}"
                    n += 1
                with open(fpath, 'wb') as f:
                    f.write(r.content)
                return fpath, None
            except Exception as e:
                return None, f"下载失败：{e}"
        def done(result):
            self._download_loading = False
            self._hide_loading("download")
            fpath, err = result
            if err:
                self._show_toast(err, 3000)
                return
            self._write_audio_tags(fpath, song_info, self.online_api)
            self._show_toast("下载完成", 2000)
            print(f"✅ 已下载到：{fpath}")
        self._run_in_thread(task, done)

    def _write_audio_tags(self, fpath, song_info, api=None):
        """写入歌名、歌手、专辑，并把封面与歌词内嵌进音频文件（不生成零散文件）。

        下载到本地的歌曲需自带封面和歌词，否则离线播放时会因读取不到而
        显示“无封面/无歌词”。这里把封面/歌词全部嵌进音频文件本体：
          - 封面：通过 api.download_cover 取得图片字节，写入 mp3 的 APIC 帧
            / flac 的 PICTURE 块；
          - 歌词：通过 api.get_lyric 取得文本，写入 mp3 的 USLT 帧 / flac 的
            lyrics 标签。
        封面/歌词获取失败只告警，不影响歌曲下载；读取侧（_load_cover /
        _load_lyrics）会在播放时优先从文件内嵌读取，无需任何外部文件。
        """
        ext = fpath.lower()
        name = song_info.get('name', '')
        singer = song_info.get('singer', '')
        album = song_info.get('album', '')
        song_id = song_info.get('song_id')
        cover_url = song_info.get('cover_url')

        # ---------- 封面（字节）----------
        cover_bytes = None
        if api and (cover_url and song_id):
            try:
                cover_path = api.download_cover(cover_url, song_id)
                if cover_path and os.path.exists(cover_path):
                    with open(cover_path, 'rb') as f:
                        cover_bytes = f.read()
            except Exception as e:
                print(f"⚠️ 获取封面失败: {e}")

        # ---------- 歌词（文本）----------
        lyric_text = None
        if api and song_id:
            try:
                lyric_text = api.get_lyric(song_id)
            except Exception as e:
                print(f"⚠️ 获取歌词失败: {e}")

        # ---------- 基础标签 + 内嵌封面/歌词 ----------
        if not HAS_MUTAGEN:
            return
        try:
            if ext.endswith('.mp3'):
                from mutagen.id3 import (ID3, ID3NoHeaderError, TIT2, TPE1,
                                         TALB, APIC, USLT)
                try:
                    audio = ID3(fpath)       # 尝试读取已有 ID3 标签
                except ID3NoHeaderError:
                    audio = ID3()            # 无标签则创建新标签
                if name:   audio['TIT2'] = TIT2(encoding=3, text=name)
                if singer: audio['TPE1'] = TPE1(encoding=3, text=singer)
                if album:  audio['TALB'] = TALB(encoding=3, text=album)
                if cover_bytes:
                    audio['APIC'] = APIC(encoding=0, mime='image/jpeg',
                                         type=3, desc='Cover',
                                         data=cover_bytes)
                if lyric_text:
                    audio['USLT'] = USLT(encoding=3, lang='eng', desc='',
                                         text=lyric_text)
                audio.save(fpath)
            elif ext.endswith('.flac'):
                from mutagen.flac import FLAC, Picture
                audio = FLAC(fpath)
                if name:   audio['title'] = name
                if singer: audio['artist'] = singer
                if album:  audio['album'] = album
                if cover_bytes:
                    pic = Picture()
                    pic.type = 3
                    pic.mime = 'image/jpeg'
                    pic.desc = 'Cover'
                    pic.data = cover_bytes
                    audio.clear_pictures()
                    audio.add_picture(pic)
                if lyric_text:
                    audio['lyrics'] = lyric_text
                audio.save()
        except Exception as e:
            print(f"⚠️ 写入歌曲标签失败: {e}")

    def _delete_local_song(self, song_info):
        """删除本地文件并从当前歌单移除。若删除的是当前播放曲目，
        自动停止播放并切换到下一首（无下一首则停止）。"""
        filepath = song_info.get('filepath')
        has_file = filepath and os.path.exists(filepath)
        if has_file:
            # 自定义确认对话框，与歌单删除样式一致
            dlg = QDialog(self)
            dlg.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
            dlg.setAttribute(Qt.WA_TranslucentBackground, True)
            dlg.setMinimumSize(int(260*self.scale), int(155*self.scale))
            outer = QVBoxLayout(dlg)
            outer.setContentsMargins(0, 0, 0, 0)
            container = QWidget(dlg)
            container.setStyleSheet(f"""
                QWidget#dlgContainer {{
                    background-color: #FFFFFF; border-radius: {int(10*self.scale)}px;
                    border: 1px solid #DCDCDC;
                }}
            """)
            container.setObjectName("dlgContainer")
            outer.addWidget(container)
            layout = QVBoxLayout(container)
            layout.setContentsMargins(12, 12, 12, 12)
            # 标题行
            title_row = QHBoxLayout()
            title_lbl = QLabel("删除歌曲")
            title_lbl.setStyleSheet(f"font-size:{int(16*self.scale)}px; font-weight:bold; color:#1A1A1A; background:transparent; border:none;")
            title_row.addWidget(title_lbl)
            title_row.addStretch(1)
            btn_close = QPushButton("✕")
            btn_close.setFixedSize(int(22*self.scale), int(22*self.scale))
            btn_close.setCursor(Qt.PointingHandCursor)
            btn_close.setStyleSheet(f"""
                QPushButton {{ background:transparent; border:none;
                    font-size:{int(12*self.scale)}px; color:#999999;
                    border-radius:{int(11*self.scale)}px;
                }}
                QPushButton:hover {{ background-color:#E0E0E0; color:#1A1A1A; }}
            """)
            btn_close.clicked.connect(dlg.reject)
            title_row.addWidget(btn_close)
            layout.addLayout(title_row)
            # 提示文字
            fname = os.path.basename(filepath)
            msg = QLabel(f"确定要删除文件「{fname}」吗？<br>此操作不可恢复！")
            msg.setStyleSheet(f"font-size:{int(15*self.scale)}px; color:#666666; background:transparent; border:none;")
            msg.setWordWrap(True)
            layout.addWidget(msg)
            layout.addStretch(1)
            # 按钮行
            btn_row = QHBoxLayout()
            btn_row.addStretch(1)
            btn_cancel = QPushButton("取消")
            btn_cancel.setFixedSize(int(60*self.scale), int(32*self.scale))
            btn_cancel.setCursor(Qt.PointingHandCursor)
            btn_cancel.setStyleSheet(f"""
                QPushButton {{ background:#F0F0F0; border:none; border-radius:{int(16*self.scale)}px;
                    font-size:{int(16*self.scale)}px; color:#666666; }}
                QPushButton:hover {{ background:#E0E0E0; }}
            """)
            btn_cancel.clicked.connect(dlg.reject)
            btn_ok = QPushButton("确定")
            btn_ok.setFixedSize(int(60*self.scale), int(32*self.scale))
            btn_ok.setCursor(Qt.PointingHandCursor)
            reg_theme(btn_ok, f"""
                QPushButton {{ background:#EC4141; border:none; border-radius:{int(16*self.scale)}px;
                    font-size:{int(16*self.scale)}px; color:#FFFFFF; }}
                QPushButton:hover {{ background:THEME_DARK; }}
            """)
            btn_ok.clicked.connect(dlg.accept)
            btn_row.addWidget(btn_cancel)
            btn_row.addWidget(btn_ok)
            layout.addLayout(btn_row)
            # 高度随文本内容自适应（长文件名换行时自动扩展，正常内容保持最小高度）
            dlg.adjustSize()
            if dlg.exec_() != QDialog.Accepted:
                return

        # 判断此歌曲是否为当前正在播放的曲目
        is_playing_song = False
        playing_idx = -1
        if (0 <= self._playing_row < len(self._panel_queue)
                and 0 <= self.current_playing_row < len(self._panel_queue)):
            playing = self._panel_queue[self.current_playing_row]
            sid = song_info.get('song_id')
            if sid and playing.get('song_id') == sid:
                is_playing_song = True
                playing_idx = self._playing_row
            elif not sid and filepath and playing.get('filepath') == filepath:
                is_playing_song = True
                playing_idx = self._playing_row

        # 处理播放队列与播放状态
        # ⚠️ 顺序很关键：必须先在 pygame 中加载新歌（释放旧文件锁），再删除旧文件
        if is_playing_song:
            self._panel_queue.pop(playing_idx)
            if self._panel_queue:
                next_idx = playing_idx if playing_idx < len(self._panel_queue) else 0
                # _play_queue_index → _play_prepared 内部会 load 新文件，
                # 从而释放 pygame 对旧文件的锁定（Windows 必不可少）
                self._play_queue_index(next_idx, auto_advance=True)
            else:
                # 队列已空，停止播放以释放锁
                if HAS_PYGAME:
                    try:
                        pygame.mixer.music.stop()
                    except:
                        pass
                self._playing_row = -1
                self.current_playing_row = -1
                self._song_ready = False
                self.btn_play.set_icon_type(MediaButton.ICON_PLAY)
                self.progress_bar.setValue(0)
                self.time_label.setText("00:00 / 00:00")
                self.label_song_name.setText("未播放")
                self.label_song_artist.setText("")
                self.update_cover(None)
                self._refresh_playlist_list()
                self._update_table_playing_indicator()

        # 删除文件（此时旧文件锁已确保释放）
        if has_file:
            try:
                os.remove(filepath)
                print(f"🗑️ 已删除文件：{filepath}")
                lrc_path = os.path.splitext(filepath)[0] + ".lrc"
                if os.path.exists(lrc_path):
                    os.remove(lrc_path)
            except Exception as e:
                self._show_toast(f"删除文件失败：{e}", 3000)
                return

        # 非播放曲目，从面板队列同步移除（此处不再提示，统一由下方"已删除"提示）
        if not is_playing_song:
            self._remove_from_playlist(song_info, show_toast=False)

        # 从当前歌单数据中移除
        menu = getattr(self, '_current_playlist_menu', None) or self.current_menu
        songs = self.playlist_data.get(menu, [])
        if song_info in songs:
            songs.remove(song_info)
            self.playlist_data[menu] = songs
        # 刷新表格
        self.display_playlist(menu)
        self._show_toast("已删除", 2000)

    def _toggle_playlist_panel(self):
        """切换播放列表面板的显示/隐藏"""
        if self._panel_anim.state() == QAbstractAnimation.Running:
            self._panel_anim.stop()
        if self._playlist_panel.isVisible():
            self._hide_playlist_panel()
        else:
            self._show_playlist_panel()

    def _show_playlist_panel(self):
        """刷新并显示播放列表面板（从右边缘滑入）"""
        # 先填充列表内容（此时面板尚未显示，滚动会因 viewport 尺寸异常而失效，
        # 因此这里不滚动，滚动画给显示后的 QTimer.singleShot）
        self._refresh_playlist_list()
        cw = self.centralWidget()
        pw = self._playlist_panel.width()
        ph = self._playlist_panel.height()
        mw = cw.width()
        # 计算目标位置（相对于 centralWidget 坐标）
        btn_center = self.playlist_btn.mapTo(cw, self.playlist_btn.rect().center())
        target_x = btn_center.x() - pw // 2 + int(10 * self.scale)
        target_y = btn_center.y() - ph - int(43 * self.scale)
        if target_x < 0:
            target_x = int(10 * self.scale)
        elif target_x + pw > mw:
            target_x = mw - pw - int(10 * self.scale)
        if target_y < 0:
            target_y = btn_center.y() + self.playlist_btn.height() + int(5 * self.scale)
        # 从右边缘外滑入
        start_x = mw
        self._playlist_panel.move(start_x, target_y)
        self._playlist_panel.show()
        self._playlist_panel.raise_()
        self._panel_anim.stop()
        try:
            self._panel_anim.finished.disconnect(self._on_panel_slide_out_done)
        except TypeError:
            pass
        self._panel_anim.setStartValue(QPoint(start_x, target_y))
        self._panel_anim.setEndValue(QPoint(target_x, target_y))
        self._panel_anim.start()
        # 面板显示后布局才就绪（viewport 尺寸正确），此时滚动到当前播放曲目。
        # 用 singleShot(0) 延后到本轮事件循环处理完布局后再执行。
        # 注意：必须用 lambda 包装，直接传绑定方法会因垃圾回收导致回调不执行。
        QTimer.singleShot(0, lambda: self._scroll_playlist_to_playing())

    def _hide_playlist_panel(self):
        """从右边缘滑出并隐藏"""
        pw = self._playlist_panel.width()
        cur_pos = self._playlist_panel.pos()
        end_x = self.centralWidget().width()
        self._panel_anim.stop()
        self._panel_anim.setStartValue(cur_pos)
        self._panel_anim.setEndValue(QPoint(end_x, cur_pos.y()))
        try:
            self._panel_anim.finished.disconnect(self._on_panel_slide_out_done)
        except TypeError:
            pass
        self._panel_anim.finished.connect(self._on_panel_slide_out_done)
        self._panel_anim.start()

    def _on_panel_slide_out_done(self):
        self._playlist_panel.hide()
        self._playlist_panel.move(-9999, -9999)
        try:
            self._panel_anim.finished.disconnect(self._on_panel_slide_out_done)
        except TypeError:
            pass

    def _refresh_playlist_list(self, scroll_to_playing=False, preserve_scroll=None):
        """刷新列表面板中的歌曲列表（独立 _panel_queue，不受 playlist_data 影响）

        scroll_to_playing=True 时，刷新后自动滚动到当前播放曲目（打开面板定位时使用）。
        否则重建列表后恢复原滚动位置，保持用户当前浏览视图不变，
        仅更新当前播放歌曲的高亮（避免切歌/移除歌曲时列表跳动）。
        列表始终按队列真实顺序显示，保证拖拽排序所见即所得。

        preserve_scroll 非空时（拖拽 drop 前快照），强制以该值恢复滚动位置，
        确保拖拽中自动滚动的效果在 drop 重建后不回弹。
        """
        # 重建前记录滚动位置（像素），重建后恢复，保持用户浏览视图不变
        prev_scroll = None
        if preserve_scroll is not None:
            prev_scroll = preserve_scroll
        elif not scroll_to_playing and self._playlist_list.count() > 0:
            prev_scroll = self._playlist_list.verticalScrollBar().value()
        self._playlist_list.clear()
        if not self._panel_queue:
            self._playlist_list.hide()
            self._playlist_empty_state.show()
            self._pp_title_label.setText(f"当前播放 <span style='color:#777777;font-weight:normal;font-size:{int(14*self.scale)}px;'>(0首)</span>")
            return
        self._playlist_list.show()
        self._playlist_empty_state.hide()
        songs = self._panel_queue
        self._pp_title_label.setText(
            f"当前播放 <span style='color:#777777;font-weight:normal;font-size:{int(14*self.scale)}px;'>({len(songs)}首)</span>")
        # 列表始终按队列真实顺序显示，切换歌曲时不重新排序整个列表，
        # 仅调整滚动位置，使正在播放的歌曲位于可视区域第一格
        display_order = list(range(len(songs)))
        if not (0 <= self._playing_row < len(songs)):
            # _playing_row 越界（队列已变更），重置为无播放状态
            self._playing_row = -1

        for visual_idx, actual_idx in enumerate(display_order):
            s = songs[actual_idx]
            name = s.get('name', '未知')
            singer = s.get('singer', '')
            text = f"{name}"
            if singer:
                text += f" - {singer}"
            item = QListWidgetItem()
            item.setData(Qt.UserRole, actual_idx)  # 存储实际 _panel_queue 索引
            # 构建自定义行 widget（带背景和文字悬停效果）
            row_widget = QWidget()
            row_widget.setAttribute(Qt.WA_Hover, True)
            reg_theme(row_widget, f"""
                QWidget {{
                    background: transparent; border-radius: {int(4*self.scale)}px;
                }}
                QWidget:hover {{
                    background-color: #F0F0F2;
                }}
                QWidget:hover QLabel {{
                    color: #EC4141;
                }}
            """)
            row_layout = QHBoxLayout(row_widget)
            row_layout.setContentsMargins(int(12*self.scale), int(14*self.scale), int(10*self.scale), int(14*self.scale))
            row_layout.setSpacing(int(4*self.scale))
            # 歌曲文本（仅正在播放且超长时才自动横向滚动）
            is_playing = (actual_idx == self._playing_row)
            if is_playing:
                label = ScrollLabel(text)
                label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                reg_theme(label, f"""
                    QLabel {{
                        font-size:{int(14*self.scale)}px; color:#EC4141;
                        background:transparent; border:none; font-weight:bold;
                        padding: 0 {int(6*self.scale)}px 0 0;
                    }}
                """)
            else:
                label = QLabel(text)
                label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                label.setStyleSheet(f"""
                    QLabel {{
                        font-size:{int(14*self.scale)}px; color:#1A1A1A;
                        background:transparent; border:none;
                        padding: 0 {int(6*self.scale)}px 0 0;
                    }}
                """)
            row_layout.addWidget(label, 1)
            # 叉按钮（所有曲目均显示，点击后移除该曲目）
            btn = QPushButton("✕")
            btn.setFixedSize(int(20*self.scale), int(20*self.scale))
            btn.setCursor(Qt.PointingHandCursor)
            reg_theme(btn, f"""
                QPushButton {{ background:transparent; border:none;
                    font-size:{int(10*self.scale)}px; color:#BBBBBB;
                    border-radius:{int(10*self.scale)}px;
                }}
                QPushButton:hover {{ background-color:#E0E0E0; color:#EC4141; }}
            """)
            btn.song_info = s
            btn.clicked.connect(lambda checked, si=s: self._remove_from_playlist(si))
            row_layout.addWidget(btn)
            row_widget.setFixedHeight(int(46 * self.scale))
            item.setSizeHint(QSize(0, int(46 * self.scale)))
            self._playlist_list.addItem(item)
            self._playlist_list.setItemWidget(item, row_widget)
        # 切换歌曲/打开面板时：滚动使当前播放曲目尽量位于可视区域第一格。
        # 注意：面板隐藏时 viewport 尺寸异常会导致滚动计算失效，
        # 因此滚动统一交由 _scroll_playlist_to_playing 处理（内部会跳过隐藏状态）。
        if scroll_to_playing:
            self._scroll_playlist_to_playing()
        elif prev_scroll is not None:
            # 保持用户浏览位置：重建后恢复原滚动值（行数变化时 Qt 自动收敛）
            self._playlist_list.verticalScrollBar().setValue(prev_scroll)
        # 确保没有任何残留的选中或焦点高亮
        self._playlist_list.clearSelection()

    def _scroll_playlist_to_playing(self):
        """滚动列表使当前播放曲目尽量位于可视区域第一格。

        若当前播放曲目位于最后 b 首（b = 可视行数，向上取整）范围内，
        则改为贴底滚动：最后一项底部对齐视口底部，确保最后 b 首完整显示、
        列表无法继续下滚，避免底部留白或播放曲目被遮挡。
        例：[a,b,c,d,e] 可显示 3 首 → 播放 d/e 时停在 [c,d,e]（d/e 位于第二/三格）。

        面板不可见时（viewport 尺寸异常）直接跳过，等待 _show_playlist_panel
        在面板显示后通过 QTimer.singleShot 再次调用本方法。"""
        songs = self._panel_queue
        if not songs or not (0 <= self._playing_row < len(songs)):
            return
        # 面板未显示时跳过（隐藏状态 viewport 高度异常，滚动计算会失效）
        panel = getattr(self, '_playlist_panel', None)
        if panel is not None and not panel.isVisible():
            return
        n = len(songs)
        last_item = self._playlist_list.item(n - 1)
        if last_item is None:
            return
        row_h = self._playlist_list.visualItemRect(last_item).height()
        vp_h = self._playlist_list.viewport().height()
        visible = max(1, (vp_h + row_h - 1) // row_h) if row_h > 0 else 1
        last_block_start = max(0, n - visible)
        if self._playing_row >= last_block_start:
            # 播放曲目位于最后 b 首内 → 贴底（最后一项底部对齐视口底部，
            # 此时滚动条处于最大值，列表无法继续下滚，无底部留白）
            self._playlist_list.scrollToItem(last_item, QAbstractItemView.PositionAtBottom)
        else:
            # 播放曲目位于中前部 → 滚动到其顶部（可视区域第一格）
            self._playlist_list.scrollToItem(
                self._playlist_list.item(self._playing_row),
                QAbstractItemView.PositionAtTop)

    def _remove_from_playlist(self, song_info, show_toast=True):
        """从播放列表面板移除歌曲（不删除文件，不影响 playlist_data）。
        show_toast=False 时由调用方统一提示，避免删除操作同时弹出两个提示。"""
        if not self._panel_queue:
            return
        sid = song_info.get('song_id')
        fp = song_info.get('filepath')
        for i, s in enumerate(self._panel_queue):
            match = False
            if sid and s.get('song_id') == sid:
                match = True
            elif not sid and fp and s.get('filepath') == fp:
                match = True
            if match:
                self._panel_queue.pop(i)
                name = song_info.get('name', '')
                print(f"🗑️ 已从面板队列移除：{name}")
                if i < self._playing_row:
                    self._playing_row -= 1
                    self.current_playing_row -= 1
                    # 同步刷新面板和主表格（非当前播放曲目，立即更新避免视觉延迟；
                    # 保持浏览位置不跳动，滚动定位留给下次打开面板）
                    self._refresh_playlist_list()
                    self._update_table_playing_indicator()
                elif i == self._playing_row:
                    # 移除当前播放曲目：不先重置 UI 到空白状态，
                    # 而是直接切换到下一首，消除中间闪烁
                    if self._panel_queue:
                        next_idx = i if i < len(self._panel_queue) else 0
                        # 直接同步播放下一首，_play_prepared 内部会刷新面板和表格
                        self._play_queue_index(next_idx, auto_advance=True)
                    else:
                        # 队列已空，重置到空白状态
                        self._playing_row = -1
                        self.current_playing_row = -1
                        self._song_ready = False
                        if HAS_PYGAME:
                            try:
                                pygame.mixer.music.stop()
                            except:
                                pass
                        self.btn_play.set_icon_type(MediaButton.ICON_PLAY)
                        self.progress_bar.setValue(0)
                        self.time_label.setText("00:00 / 00:00")
                        self.label_song_name.setText("未播放")
                        self.label_song_artist.setText("")
                        self.update_cover(None)
                        self._refresh_playlist_list()
                        self._update_table_playing_indicator()
                else:
                    # i > self._playing_row：移除播放曲目之后的曲目，不影响播放，只需刷新列表
                    self._refresh_playlist_list()
                if show_toast:
                    self._show_toast(f"已移除「{name}」", 2000)
                return
        if show_toast:
            self._show_toast("歌曲不在当前列表中", 2000)

    def _remove_from_playlist_data(self, song_info):
        """从当前自定义歌单中移除歌曲（不删除文件，不影响播放列表面板）"""
        menu = getattr(self, '_current_playlist_menu', None)
        if not menu or not song_info:
            return
        playlist_name = menu[len("📋 "):] if menu.startswith("📋 ") else menu
        name = song_info.get('name', '')
        sid = song_info.get('song_id')
        # 1. 从 playlist_data 中移除（内存展示副本）
        songs = self.playlist_data.get(menu, [])
        if song_info not in songs:
            self._show_toast("歌曲不在当前歌单中", 2000)
            return
        songs.remove(song_info)
        self.playlist_data[menu] = songs
        # 2. 从源数据 _playlists 中同步移除（按 song_id 优先，否则 name+singer 匹配）
        src = self._playlists.get(playlist_name, [])
        for i, s in enumerate(src):
            if sid and s.get('song_id') == sid:
                src.pop(i)
                break
            if not sid and s.get('name') == song_info.get('name') \
                    and s.get('singer') == song_info.get('singer'):
                src.pop(i)
                break
        self._playlists[playlist_name] = src
        self._playlist_meta[playlist_name] = time.time()  # 更新修改时间
        # 3. 保存歌单数据到文件
        self._save_playlists()
        # 4. 刷新歌单表格（仅影响歌单，不动播放列表面板）
        self.display_playlist(menu)
        self._show_toast(f"已从歌单删除「{name}」", 2000)

    def _on_playlist_item_clicked(self, item):
        """点击列表面板中的歌曲（保持面板展开状态）"""
        idx = item.data(Qt.UserRole)
        if idx is None or idx < 0 or idx >= len(self._panel_queue):
            return
        self._play_queue_index(idx)

    def _clear_current_playlist(self):
        """清空播放列表面板（不影响 playlist_data）"""
        self._panel_queue = []
        self._panel_queue_source = None
        self._playlist_list.clear()
        if HAS_PYGAME:
            try:
                pygame.mixer.music.stop()
            except:
                pass
        self._playing_row = -1
        self.current_playing_row = -1
        self._song_ready = False
        self.btn_play.set_icon_type(MediaButton.ICON_PLAY)
        self.progress_bar.setValue(0)
        self.time_label.setText("00:00 / 00:00")
        self.label_song_name.setText("未播放")
        self.label_song_artist.setText("")
        # 清空后不再恢复当前歌曲高亮（列表本身仍保留）
        self.last_playing = None
        self._save_settings()
        self.update_cover(None)
        # 清空后更新主表格中的播放行高亮（移除高亮）
        self._update_table_playing_indicator()
        # 更新标题计数
        self._pp_title_label.setText(f"当前播放 <span style='color:#777777;font-weight:normal;font-size:{int(14*self.scale)}px;'>(0首)</span>")
        self._refresh_playlist_list()
        self._show_toast("已清空", 1500)

    def _sync_playlist_order(self):
        """拖拽排序后将列表显示顺序同步到播放队列（_panel_queue）：
        1) 更新 _panel_queue（播放队列）
        2) 重新定位当前播放曲目
        3) 清空已失效的随机播放索引
        4) 同步主表格中当前播放行的高亮（按 song_id 查找，与顺序无关）

        状态隔离：拖拽排序严格限定在播放列表组件内部，只改动 _panel_queue，
        不写入来源歌单（_playlists / playlist_data），不刷新主表格顺序，
        也不写回 playlists.json。播放列表组件与主表格组件各自维护独立的
        歌曲列表数据源，边界互不越界。

        列表自身已由 Qt 完成 item 重排，此处不重建列表，保证拖拽流畅无闪烁。
        快速连续拖拽时：每次 drop 后都会立即刷新 UserRole 与队列，
        因此下一次拖拽读取到的始终是最新的索引数据。
        """
        if not self._panel_queue:
            return
        n = self._playlist_list.count()
        if n != len(self._panel_queue):
            return
        # 读取拖拽后每项对应的原始队列索引（UserRole 在 _refresh_playlist_list 中写入）
        indices = []
        for i in range(n):
            old_row = self._playlist_list.item(i).data(Qt.UserRole)
            if old_row is None or old_row < 0 or old_row >= n:
                return  # 索引异常（外部数据干扰），放弃本次同步
            indices.append(old_row)
        # 拖拽前后显示顺序一致（放回原位 / 快速重复拖拽）→ 数据层无需变更，
        # 跳过队列同步与持久化。但 Qt 的 InternalMove 即使最终顺序不变，
        # 也会对被拖 item 执行移除/重插，setItemWidget 绑定的行 widget 无法
        # 跟随 item 移动（该行显示为空白），因此仍须重建列表恢复显示。
        # 与拖拽前快照（_pre_drag_order）比较而非队列内容，兼容任何显示顺序
        if getattr(self._playlist_list, '_pre_drag_order', None) == indices:
            self._refresh_playlist_list(
                preserve_scroll=getattr(self._playlist_list, '_drop_scroll_pos', None))
            return
        # 按新显示顺序重建队列
        reordered = [self._panel_queue[i] for i in indices]
        # 记录当前播放歌曲（用旧队列查找，拖拽后其位置会变化）
        playing_song_id = None
        if 0 <= self._playing_row < len(self._panel_queue):
            playing_song_id = self._panel_queue[self._playing_row].get('song_id')
        self._panel_queue = reordered
        # 更新每项的 UserRole 为最新索引，保证后续点击播放与再次拖拽正确
        for i in range(n):
            self._playlist_list.item(i).setData(Qt.UserRole, i)
        # 重新定位当前播放曲目
        if playing_song_id is not None:
            for i, s in enumerate(reordered):
                if s.get('song_id') == playing_song_id:
                    self._playing_row = i
                    self.current_playing_row = i
                    break
        # 随机播放的剩余索引基于旧队列，已失效，清空以便下次重建
        if self._shuffle_queue:
            self._shuffle_queue = []
        # 同步主表格中当前播放行的高亮（按 song_id 查找，与列表顺序无关）
        self._update_table_playing_indicator()
        # 拖拽后强制重建列表：修复 setItemWidget + InternalMove 拖拽下
        # 被移动 item 的 widget 显示异常（该行空白）的 Qt 已知问题。
        # 重建按新队列顺序渲染所有 item 与 widget，并恢复 drop 前快照的
        # 滚动位置（自动滚动效果不回弹）。
        self._refresh_playlist_list(
            preserve_scroll=getattr(self._playlist_list, '_drop_scroll_pos', None))

    def _on_add_clicked(self, row, button=None):
        """表格 ⋯ 点击 → 弹出歌曲子菜单"""
        menu = getattr(self, '_current_playlist_menu', None) or self.current_menu
        songs = self.playlist_data.get(menu, [])
        if row >= len(songs):
            return
        song_info = songs[row]
        # 获取按钮位置
        tbl = self.song_table
        cell_widget = tbl.cellWidget(row, 6)
        btn = cell_widget or button
        if btn:
            # 仅限浏览自定义歌单（_current_playlist_menu 不为 None）时才显示"从歌单删除"
            show_remove = getattr(self, '_current_playlist_menu', None) is not None
            self._show_song_menu(song_info, btn,
                                 show_delete=(menu == "📁 本地下载"),
                                 show_remove_from_playlist=show_remove)
        else:
            self._choose_playlist(song_info)

    def _on_add_btn_clicked(self):
        """底栏 ⋯ 点击 → 弹出当前播放歌曲的子菜单

        当前播放歌必须经播放列表面板（_panel_queue）真实引用定位，
        不依赖 playlist_data 索引——从歌单删除歌曲只会改 playlist_data/json，
        不影响 _panel_queue，因此删除后下载/菜单仍作用于正在播放的那首。"""
        if self._playing_row < 0 or self._playing_row >= len(self._panel_queue):
            return
        song_info = self._panel_queue[self._playing_row]
        self._show_song_menu(song_info, self.add_btn)

    def _choose_playlist(self, song_info):
        """弹窗选择或新建歌单，将歌曲加入"""
        self._load_playlists()
        names = list(self._playlists.keys())
        # 选择已有歌单
        if names:
            dlg = QDialog(self)
            dlg.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
            dlg.setAttribute(Qt.WA_TranslucentBackground, True)
            dlg.setFixedSize(int(280*self.scale), int(280*self.scale))
            container = QWidget(dlg)
            container.setStyleSheet(f"""
                QWidget#dialogContainer {{
                    background-color: #FFFFFF; border-radius: {int(10*self.scale)}px;
                    border: 1px solid #DCDCDC;
                }}
            """)
            dlg.setStyleSheet("QDialog { background: transparent; }")
            container.setObjectName("dialogContainer")
            container.setGeometry(0, 0, dlg.width(), dlg.height())
            layout = QVBoxLayout(container)
            layout.setContentsMargins(10, 10, 10, 14)  # 折中：比8宽、比12窄
            # 标题行
            title_row = QHBoxLayout()
            title_lbl = QLabel("添加到歌单")
            title_lbl.setStyleSheet(f"font-size:{int(16*self.scale)}px; font-weight:bold; color:#1A1A1A; background:transparent; border:none;")
            title_row.addWidget(title_lbl)
            title_row.addStretch(1)
            btn_close = QPushButton("✕")
            btn_close.setFixedSize(int(22*self.scale), int(22*self.scale))
            btn_close.setCursor(Qt.PointingHandCursor)
            btn_close.setStyleSheet(f"""
                QPushButton {{ background:transparent; border:none;
                    font-size:{int(12*self.scale)}px; color:#999999;
                    border-radius:{int(11*self.scale)}px;
                }}
                QPushButton:hover {{ background-color:#E0E0E0; color:#1A1A1A; }}
            """)
            btn_close.clicked.connect(dlg.reject)
            title_row.addWidget(btn_close)
            layout.addLayout(title_row)
            # 单列表格
            tbl = QTableWidget()
            tbl.setColumnCount(1)
            tbl.horizontalHeader().setVisible(False)
            tbl.verticalHeader().setVisible(False)
            tbl.setShowGrid(False)
            tbl.setSelectionMode(QTableWidget.NoSelection)
            tbl.verticalScrollBar().setStyleSheet("width:0;")
            tbl.setStyleSheet(f"""
                QTableWidget {{ background:transparent; border:none; font-size:{int(14*self.scale)}px; }}
                QTableWidget::item {{ padding:{int(6*self.scale)}px {int(8*self.scale)}px; border-bottom:1px solid #F0F0F0; }}
                QTableWidget::item:hover {{ background-color:#F5F5F7; }}
            """)
            tbl.setRowCount(len(names) + 1)
            tbl.setColumnWidth(0, int(264*self.scale))
            # 第一行：新建歌单
            new_item = QTableWidgetItem("＋  新建歌单")
            new_item.setForeground(QColor(_theme_color()))
            new_item.setTextAlignment(Qt.AlignCenter)
            tbl.setItem(0, 0, new_item)
            # 其余行：已有歌单（显示名称和歌曲数）
            for i, n in enumerate(names):
                count = len(self._playlists.get(n, []))
                text = f"{n}（{count}首）"
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignCenter)
                item.setForeground(QColor("#1A1A1A"))
                tbl.setItem(i + 1, 0, item)
            layout.addWidget(tbl)
            row_h = int(40 * self.scale)
            tbl.verticalHeader().setDefaultSectionSize(row_h)
            for r in range(tbl.rowCount()):
                tbl.setRowHeight(r, row_h)
            chosen = [None]
            def on_cell_clicked(row, col):
                if row == 0:
                    name = self._ask_playlist_name(self)
                    if name:
                        chosen[0] = name
                        dlg.accept()
                    else:
                        return
                elif row - 1 < len(names):
                    chosen[0] = names[row - 1]
                    dlg.accept()
            tbl.cellClicked.connect(on_cell_clicked)
            if dlg.exec_() != QDialog.Accepted or not chosen[0]:
                return
            item = chosen[0]
        else:
            name = self._ask_playlist_name(self)
            if not name:
                return
            item = name
        self._add_to_playlist(song_info, item)

    def _ask_playlist_name(self, parent, title="新建歌单"):
        dlg = QDialog(parent)
        dlg.setWindowFlags(Qt.FramelessWindowHint | Qt.Dialog)
        dlg.setAttribute(Qt.WA_TranslucentBackground, True)
        dlg.setFixedSize(int(260*self.scale), int(160*self.scale))
        dlg.setStyleSheet("QDialog { background: transparent; }")
        container = QWidget(dlg)
        container.setStyleSheet(f"""
            QWidget#dialogContainer {{
                background-color: #FFFFFF; border-radius: {int(10*self.scale)}px;
                border: 1px solid #DCDCDC;
            }}
        """)
        container.setObjectName("dialogContainer")
        container.setGeometry(0, 0, dlg.width(), dlg.height())
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 16)  # 底部留白，避免按钮贴边
        # 标题行（含关闭按钮）
        title_row = QHBoxLayout()
        title_lbl = QLabel(title)
        title_lbl.setStyleSheet(f"font-size:{int(16*self.scale)}px; font-weight:bold; color:#1A1A1A; background:transparent; border:none;")
        title_row.addWidget(title_lbl)
        title_row.addStretch(1)
        btn_close = QPushButton("✕")
        btn_close.setFixedSize(int(22*self.scale), int(22*self.scale))
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.setStyleSheet(f"""
            QPushButton {{ background:transparent; border:none;
                font-size:{int(12*self.scale)}px; color:#999999;
                border-radius:{int(11*self.scale)}px;
            }}
            QPushButton:hover {{ background-color:#E0E0E0; color:#1A1A1A; }}
        """)
        btn_close.clicked.connect(dlg.reject)
        title_row.addWidget(btn_close)
        layout.addLayout(title_row)
        lbl = QLabel("歌单名称（20字以内）：")
        lbl.setStyleSheet(f"font-size:{int(14*self.scale)}px; background:transparent; border:none;")
        inp = QLineEdit()
        inp.setStyleSheet(f"font-size:{int(15*self.scale)}px;padding:4px;")
        inp.setFixedHeight(int(32 * self.scale))
        MAX_NAME_LEN = 20
        inp.textChanged.connect(lambda text: btn_ok.setEnabled(bool(text.strip())))
        inp.textChanged.connect(lambda text: (
            inp.blockSignals(True), inp.setText(text[:MAX_NAME_LEN]), inp.blockSignals(False)
        ) if len(text) > MAX_NAME_LEN else None)
        layout.addWidget(lbl)
        layout.addWidget(inp)
        layout.addSpacing(int(16 * self.scale))  # 输入框与按钮之间保持间距
        layout.addStretch(1)
        # 按钮行（与删除弹窗样式统一）
        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        btn_cancel = QPushButton("取消")
        btn_cancel.setFixedSize(int(60*self.scale), int(32*self.scale))
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.setStyleSheet(f"""
            QPushButton {{ background:#F0F0F0; border:none; border-radius:{int(16*self.scale)}px;
                font-size:{int(16*self.scale)}px; color:#666666; }}
            QPushButton:hover {{ background:#E0E0E0; }}
        """)
        btn_cancel.clicked.connect(dlg.reject)
        btn_ok = QPushButton("确定")
        btn_ok.setFixedSize(int(60*self.scale), int(32*self.scale))
        btn_ok.setCursor(Qt.PointingHandCursor)
        btn_ok.setEnabled(False)
        reg_theme(btn_ok, f"""
            QPushButton {{ background:#EC4141; border:none; border-radius:{int(16*self.scale)}px;
                font-size:{int(16*self.scale)}px; color:#FFFFFF; }}
            QPushButton:hover {{ background:THEME_DARK; }}
            QPushButton:disabled {{ background:#E0E0E0; color:#AAAAAA; }}
        """)
        btn_ok.clicked.connect(dlg.accept)
        # 全局拦截回车键：弹窗内任何组件获得焦点时按回车，确定按钮禁用则播放提示音，可用则直接确认
        class _EnterFilter(QObject):
            def eventFilter(self, obj, event):
                if event.type() == QEvent.KeyPress and event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    w = obj if isinstance(obj, QWidget) else None
                    while w is not None:
                        if w == dlg:
                            if btn_ok.isEnabled():
                                dlg.accept()
                            else:
                                QApplication.beep()
                            return True
                        w = w.parent()
                return super().eventFilter(obj, event)
        dlg._enter_filter = _EnterFilter(dlg)
        QApplication.instance().installEventFilter(dlg._enter_filter)
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_ok)
        layout.addLayout(btn_row)
        if dlg.exec_() == QDialog.Accepted and inp.text().strip():
            return inp.text().strip()
        return None

    # ---------- 收藏 ----------
    def _is_fav(self, song_info):
        sid = song_info.get('song_id')
        for fav in self._favorites:
            if fav.get('song_id') == sid:
                return True
        return False

    def _on_table_cell_clicked(self, row, col):
        """表格点击"""
        self.song_table.clearSelection()
        # 排行榜浏览模式
        if getattr(self, '_toplist_browsing', False):
            names = list(TOPLIST_IDS.keys())
            if 0 <= row < len(names):
                name = names[row]
                toplist_id = TOPLIST_IDS.get(name)
                if toplist_id:
                    self._show_toplist_songs(toplist_id, name)
            return
        # 歌单浏览模式
        if getattr(self, '_playlist_browsing', False):
            if row == 0:
                self._create_new_playlist()
            else:
                names = list(self._playlists.keys())
                if row - 1 < len(names):
                    self._open_playlist(names[row - 1])
            return
        # 普通歌曲列表：第 5 列为收藏切换
        if col != 5:
            return
        menu = getattr(self, '_current_playlist_menu', None) or self.current_menu
        songs = self.playlist_data.get(menu, [])
        if row >= len(songs):
            return
        song_info = songs[row]
        self._toggle_fav_for_song(song_info, row)

    def _on_table_cell_double_clicked(self, row, col):
        """表格双击（仅响应双击，不干扰单击逻辑）：
        - 歌名列(1)：执行与列表中播放按钮相同的播放操作
        - 歌手列(2)：跳转搜索页面，按「歌手」类别搜索该歌手
        - 专辑列(3)：跳转搜索页面，按「专辑」类别搜索该专辑
        - 其余列：不响应
        """
        # 浏览模式（排行榜/歌单列表）不参与双击操作
        if getattr(self, '_toplist_browsing', False) or getattr(self, '_playlist_browsing', False):
            return
        if col not in (1, 2, 3):
            return
        menu = getattr(self, '_current_playlist_menu', None) or self.current_menu
        songs = self.playlist_data.get(menu, [])
        if row >= len(songs):
            return
        song_info = songs[row]
        if col == 1:
            self.play_song(row)
        elif col == 2:
            singer = (song_info.get('singer') or '').strip()
            if singer:
                self._goto_search_page("歌手", "artist", singer)
        elif col == 3:
            album = (song_info.get('album') or '').strip()
            if album:
                self._goto_search_page("专辑", "album", album)

    def _goto_search_page(self, mode_text, mode, keyword):
        """跳转到搜索页面（发现音乐），切换搜索类别并自动搜索关键词"""
        # 左侧菜单切换到「发现音乐」（复用完整切换逻辑：保存子状态、
        # 显示搜索工具栏等）
        for i in range(self.left_menu.count()):
            it = self.left_menu.item(i)
            if (it.data(Qt.UserRole) or it.text()) == "🎧 发现音乐":
                self.left_menu.setCurrentRow(i)
                self.on_menu_clicked(it)
                break
        # 填入关键词并切换搜索类别（_set_search_mode 在搜索框已有
        # 内容时会自动触发 on_search）
        self.search_input.setText(keyword)
        self._set_search_mode(mode_text, mode)

    def _load_favorites_playlist(self):
        """从 favorites.json 加载收藏列表展示（按用户选择的排序方式排列）"""
        menu_text = "❤️ 我喜欢的音乐"
        self._load_favorites()  # 重新读取文件
        songs = []
        for fav in self._favorites:
            songs.append({
                "song_id": fav.get("song_id"),
                "name": fav.get("name", ""),
                "singer": fav.get("singer", ""),
                "album": fav.get("album", ""),
                "duration": fav.get("duration", "--:--"),
                "duration_sec": fav.get("duration_sec", 0),
                "cover_url": fav.get("cover_url", ""),
                "filepath": None,
            })
        # 按用户选择的排序方式排列（仅影响主表格显示顺序）
        self._sort_favorites_songs(songs)
        self.playlist_data[menu_text] = songs
        self._show_favorites_toolbar()
        self.display_playlist(menu_text)
        print(f"💖 加载收藏列表：{len(songs)} 首歌曲")

    def _sort_favorites_songs(self, songs):
        """按 _favorites_sort 对收藏列表排序（仅影响主表格显示顺序）"""
        mode = self._favorites_sort
        def fav_time(s):
            return next((f.get("added_at", "") for f in self._favorites
                         if f.get("song_id") == s.get("song_id")), "")
        if mode == "time_desc":
            songs.sort(key=fav_time, reverse=True)   # 收藏时间倒序（最新在前）
        elif mode == "time_asc":
            songs.sort(key=fav_time)                 # 收藏时间正序（最早在前）
        elif mode == "name_desc":
            songs.sort(key=lambda s: s.get("name", ""), reverse=True)  # 曲名倒序
        elif mode == "singer_desc":
            songs.sort(key=lambda s: s.get("singer", ""), reverse=True)  # 歌手倒序
        elif mode == "singer_asc":
            songs.sort(key=lambda s: s.get("singer", ""))                # 歌手正序
        else:  # name_asc
            songs.sort(key=lambda s: s.get("name", ""))                # 曲名正序

    def _apply_favorites_sort(self, mode):
        """应用用户选择的排序方式：保存偏好并重新加载收藏列表"""
        if mode == self._favorites_sort:
            return
        self._favorites_sort = mode
        self._save_settings()  # 实时写入配置文件
        self._load_favorites_playlist()

    def _show_favorites_toolbar(self):
        """显示"我喜欢的音乐"页面的排序工具栏（按钮样式与"换一批"一致）"""
        self.toolbar.show()
        while self.toolbar_layout.count():
            item = self.toolbar_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
        btn = QPushButton("排序")
        reg_theme(btn, f"""
            QPushButton {{
                background: transparent; border: none;
                font-size: {int(14*self.scale)}px; color: #EC4141;
                text-decoration: underline;
            }}
            QPushButton:hover {{
                color: #CC3333;
            }}
        """)
        btn.setCursor(Qt.PointingHandCursor)
        # 菜单样式与歌曲子菜单（_show_song_menu）保持一致
        menu = QMenu(self.toolbar)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint)
        reg_theme(menu, f"""
            QMenu {{
                background: #FFFFFF; border: 1px solid #E0E0E0;
                border-radius: {int(6*self.scale)}px;
                padding: {int(5*self.scale)}px {int(6*self.scale)}px;
                font-size: {int(14*self.scale)}px;
            }}
            QMenu::item {{
                padding: {int(8*self.scale)}px {int(24*self.scale)}px;
                margin: {int(1*self.scale)}px 0;
                color: #1A1A1A;
                border-radius: {int(4*self.scale)}px;
            }}
            QMenu::item:hover, QMenu::item:selected {{
                background-color: #F0F0F2;
                color: #EC4141;
            }}
            QMenu::separator {{
                height: {int(1*self.scale)}px;
                background: #EBEBEB;
                margin: {int(4*self.scale)}px {int(12*self.scale)}px;
            }}
        """)
        options = [
            ("收藏时间（新→旧）", "time_desc"),
            ("收藏时间（旧→新）", "time_asc"),
            ("曲名（A→Z）", "name_asc"),
            ("曲名（Z→A）", "name_desc"),
            ("歌手（A→Z）", "singer_asc"),
            ("歌手（Z→A）", "singer_desc"),
        ]
        for label, mode in options:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setData(mode)
            act.setChecked(self._favorites_sort == mode)
        btn.clicked.connect(lambda: self._pop_favorites_sort_menu(btn, menu))
        self.toolbar_layout.addStretch(1)
        self.toolbar_layout.addWidget(btn, 0)
        # 右侧留白，避免按钮紧贴工具栏右边缘
        self.toolbar_layout.addSpacing(int(26 * self.scale))

    def _pop_favorites_sort_menu(self, btn, menu):
        """在按钮附近弹出排序菜单，并保证不超出主窗口（与歌曲子菜单定位一致）"""
        # 弹出前刷新勾选状态（排序方式可能已被其他入口改变）
        for act in menu.actions():
            act.setChecked(act.data() == self._favorites_sort)
        menu.adjustSize()
        win_geo = self.geometry()
        mw, mh = menu.width(), menu.height()
        # 水平：菜单右边缘距窗口右边缘 15px（不伸出窗口右侧）
        pt_x = win_geo.x() + win_geo.width() - mw - int(15 * self.scale)
        if pt_x < win_geo.x():
            pt_x = win_geo.x() + int(10 * self.scale)
        # 垂直：按钮在窗口下半部则向上弹出，否则向下
        btn_top = btn.mapToGlobal(QPoint(0, 0)).y()
        btn_center = btn_top + btn.height() / 2
        win_center = win_geo.y() + win_geo.height() / 2
        if btn_center > win_center:
            pt_y = btn_top - mh
        else:
            pt_y = btn_top + btn.height()
        # 不超出窗口上下边界
        if pt_y < win_geo.y() + int(4 * self.scale):
            pt_y = win_geo.y() + int(4 * self.scale)
        if pt_y + mh > win_geo.y() + win_geo.height():
            pt_y = win_geo.y() + win_geo.height() - mh - int(4 * self.scale)
        chosen = menu.exec_(QPoint(pt_x, pt_y))
        if chosen is not None:
            mode = chosen.data()
            if mode:
                self._apply_favorites_sort(mode)

    def _apply_local_sort(self, mode):
        """应用本地下载排序方式：保存偏好并重新加载本地文件列表"""
        if mode == self._local_sort:
            return
        self._local_sort = mode
        self._save_settings()  # 实时写入配置文件
        self.load_local_folder()

    def _show_local_toolbar(self):
        """显示"本地下载"页面的排序工具栏（样式与收藏排序一致）"""
        self.toolbar.show()
        while self.toolbar_layout.count():
            item = self.toolbar_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
        btn = QPushButton("排序")
        reg_theme(btn, f"""
            QPushButton {{
                background: transparent; border: none;
                font-size: {int(14*self.scale)}px; color: #EC4141;
                text-decoration: underline;
            }}
            QPushButton:hover {{
                color: #CC3333;
            }}
        """)
        btn.setCursor(Qt.PointingHandCursor)
        # 菜单样式与歌曲子菜单（_show_song_menu）保持一致
        menu = QMenu(self.toolbar)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint)
        reg_theme(menu, f"""
            QMenu {{
                background: #FFFFFF; border: 1px solid #E0E0E0;
                border-radius: {int(6*self.scale)}px;
                padding: {int(5*self.scale)}px {int(6*self.scale)}px;
                font-size: {int(14*self.scale)}px;
            }}
            QMenu::item {{
                padding: {int(8*self.scale)}px {int(24*self.scale)}px;
                margin: {int(1*self.scale)}px 0;
                color: #1A1A1A;
                border-radius: {int(4*self.scale)}px;
            }}
            QMenu::item:hover, QMenu::item:selected {{
                background-color: #F0F0F2;
                color: #EC4141;
            }}
            QMenu::separator {{
                height: {int(1*self.scale)}px;
                background: #EBEBEB;
                margin: {int(4*self.scale)}px {int(12*self.scale)}px;
            }}
        """)
        options = [
            ("曲名（A→Z）", "name_asc"),
            ("曲名（Z→A）", "name_desc"),
            ("歌手（A→Z）", "singer_asc"),
            ("歌手（Z→A）", "singer_desc"),
            ("添加时间（新→旧）", "time_desc"),
            ("添加时间（旧→新）", "time_asc"),
        ]
        for label, mode in options:
            act = menu.addAction(label)
            act.setCheckable(True)
            act.setData(mode)
            act.setChecked(self._local_sort == mode)
        btn.clicked.connect(lambda: self._pop_local_sort_menu(btn, menu))
        self.toolbar_layout.addStretch(1)
        self.toolbar_layout.addWidget(btn, 0)
        # 右侧留白，避免按钮紧贴工具栏右边缘
        self.toolbar_layout.addSpacing(int(26 * self.scale))

    def _pop_local_sort_menu(self, btn, menu):
        """在按钮附近弹出本地下载排序菜单，并保证不超出主窗口"""
        # 弹出前刷新勾选状态（排序方式可能已被其他入口改变）
        for act in menu.actions():
            act.setChecked(act.data() == self._local_sort)
        menu.adjustSize()
        win_geo = self.geometry()
        mw, mh = menu.width(), menu.height()
        # 水平：菜单右边缘距窗口右边缘 15px（不伸出窗口右侧）
        pt_x = win_geo.x() + win_geo.width() - mw - int(15 * self.scale)
        if pt_x < win_geo.x():
            pt_x = win_geo.x() + int(10 * self.scale)
        # 垂直：按钮在窗口下半部则向上弹出，否则向下
        btn_top = btn.mapToGlobal(QPoint(0, 0)).y()
        btn_center = btn_top + btn.height() / 2
        win_center = win_geo.y() + win_geo.height() / 2
        if btn_center > win_center:
            pt_y = btn_top - mh
        else:
            pt_y = btn_top + btn.height()
        # 不超出窗口上下边界
        if pt_y < win_geo.y() + int(4 * self.scale):
            pt_y = win_geo.y() + int(4 * self.scale)
        if pt_y + mh > win_geo.y() + win_geo.height():
            pt_y = win_geo.y() + win_geo.height() - mh - int(4 * self.scale)
        chosen = menu.exec_(QPoint(pt_x, pt_y))
        if chosen is not None:
            mode = chosen.data()
            if mode:
                self._apply_local_sort(mode)

    def _toggle_fav(self):
        """切换当前播放歌曲的收藏状态

        当前播放歌经 _panel_queue 真实引用定位（不依赖 playlist_data 索引）。"""
        if self._playing_row < 0 or self._playing_row >= len(self._panel_queue):
            return
        song_info = self._panel_queue[self._playing_row]
        self._toggle_fav_for_song(song_info, self._playing_row)

    def _toggle_fav_for_song(self, song_info, row):
        """切换指定歌曲的收藏状态并更新UI"""
        sid = song_info.get('song_id')
        if not sid:
            self._show_toast("仅支持在线歌曲收藏", 2000)
            return
        if self._is_fav(song_info):
            self._favorites = [f for f in self._favorites if f.get('song_id') != sid]
            is_fav = False
        else:
            self._favorites.append({
                "song_id": sid,
                "name": song_info.get('name', ''),
                "singer": song_info.get('singer', ''),
                "album": song_info.get('album', ''),
                "duration": song_info.get('duration', ''),
                "duration_sec": song_info.get('duration_sec', 0),
                "cover_url": song_info.get('cover_url', ''),
                "added_at": datetime.now().isoformat(),
            })
            is_fav = True
        self._save_favorites()
        self._update_fav_ui(is_fav, song_info, row)
        self._mascot_say_event("on_favorite_add" if is_fav else "on_favorite_remove")

    def _render_fav_icon_pixmap(self, path, size):
        """将 svg 渲染为保持纵横比、居中的 QPixmap。
        关键点：
        1) 按设备像素比(DPR)放大物理分辨率并标定 devicePixelRatio，
           避免高分屏(125%/150%/200%)下位图被显示层放大产生锯齿/像素点；
        2) 启用 SmoothPixmapTransform，让 SVG 缩放后边缘平滑；
        3) 当 SVG 路径实际几何边界超出 viewBox（被裁切）时，动态扩展
           viewBox 使路径完整显示，并基于路径实际边界做缩放居中，
           保证心形完整、清晰且视觉居中。
        size 为逻辑像素尺寸，调用方无需感知 DPR。"""
        from PyQt5.QtSvg import QSvgRenderer
        import re

        dpr = self.devicePixelRatio() or 1.0
        pm_w = max(1, int(round(size.width() * dpr)))
        pm_h = max(1, int(round(size.height() * dpr)))
        pm = QPixmap(pm_w, pm_h)
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        # try/finally 确保 QPainter 必定 end()，杜绝 QPixmap 在被绘制
        # 状态下销毁（QPaintDevice: Cannot destroy paint device that is
        # being painted）
        try:
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.SmoothPixmapTransform)

            renderer = QSvgRenderer(path)
            if renderer.isValid():
                vb = renderer.viewBox()
                bounds = renderer.boundsOnElement("")
                # 路径实际边界超出 viewBox（任一方向被裁切）时，临时扩展 viewBox
                if (bounds.isValid() and vb.isValid()
                        and (bounds.left() < vb.left() - 0.001
                             or bounds.top() < vb.top() - 0.001
                             or bounds.right() > vb.right() + 0.001
                             or bounds.bottom() > vb.bottom() + 0.001)):
                    try:
                        with open(path, 'r', encoding='utf-8') as f:
                            svg_text = f.read()
                        # 让 viewBox 精确等于 path 实际边界，纵横比与 path 一致，
                        # 按 vb 缩放即按 path 缩放，path 居中即 vb 居中
                        new_left = bounds.left()
                        new_top = bounds.top()
                        new_w = bounds.width()
                        new_h = bounds.height()
                        new_vb_str = (f'{new_left:g} {new_top:g} '
                                      f'{new_w:g} {new_h:g}')
                        new_svg = re.sub(
                            r'viewBox\s*=\s*"[^"]*"',
                            f'viewBox="{new_vb_str}"',
                            svg_text, count=1,
                        )
                        r2 = QSvgRenderer(new_svg.encode('utf-8'))
                        if r2.isValid():
                            renderer = r2
                            vb = renderer.viewBox()
                    except Exception:
                        pass

                # 以 viewBox 作为缩放基准（已扩展时 vb == bounds，
                # 保持图标纵横比且与边界对齐，渲染时清晰不变形）
                if not vb.isValid() or vb.width() <= 0 or vb.height() <= 0:
                    vb = QRectF(QPointF(0, 0), renderer.defaultSize())
                if vb.width() > 0 and vb.height() > 0:
                    s = min(size.width() / vb.width(),
                            size.height() / vb.height())
                    w = vb.width() * s
                    h = vb.height() * s
                    x = (size.width() - w) / 2.0
                    y = (size.height() - h) / 2.0
                    renderer.render(p, QRectF(x, y, w, h))
                else:
                    renderer.render(p, QRectF(0, 0, size.width(), size.height()))
        finally:
            p.end()
        return pm

    def _update_fav_btn_style(self, is_fav):
        img = "like.svg" if is_fav else "dislike.svg"
        path = os.path.join(self.icons_folder, img)
        if not os.path.exists(path):
            return
        sz = self.fav_btn.size()
        self.fav_btn.setIcon(QIcon(self._render_fav_icon_pixmap(path, sz)))
        self.fav_btn.setIconSize(sz)
        self.fav_btn.setStyleSheet("""
            QPushButton { background: transparent; border: none; }
        """)

    def _update_fav_ui(self, is_fav, song_info, row=None):
        """更新收藏按钮和表格中对应行的状态"""
        target_row = row if row is not None else self.current_playing_row
        # 底栏按钮只随当前播放歌曲变化
        if target_row == self.current_playing_row:
            self._update_fav_btn_style(is_fav)
        # 同步表格
        if self.current_menu and target_row >= 0:
            item = self.song_table.item(target_row, 5)
            if item:
                item.setText("")
                fav_img = "like.svg" if is_fav else "dislike.svg"
                path = os.path.join(self.icons_folder, fav_img)
                if os.path.exists(path):
                    icon_size = int(16 * self.scale)
                    item.setIcon(QIcon(self._render_fav_icon_pixmap(path, QSize(icon_size, icon_size))))

    def _set_search_mode(self, text, mode):
        """搜索模式切换：更新按钮文本与搜索框提示，并在搜索框已有内容时按新模式自动重新搜索"""
        self._search_mode = mode
        self.search_mode_btn.setText(text)
        ph = {"song": "输入歌曲名搜索...",
              "artist": "输入歌手名搜索...",
              "album": "输入专辑名搜索..."}.get(mode, "输入歌曲名搜索...")
        self.search_input.setPlaceholderText(ph)
        # 切换模式后自动按新模式重新搜索（搜索框已有内容时）
        if self.search_input.text().strip():
            self.on_search()

    def _show_search_mode_menu(self):
        """点击搜索模式按钮：在按钮下方弹出模式菜单"""
        pos = self.search_mode_btn.mapToGlobal(QPoint(0, self.search_mode_btn.height() + 2))
        self._search_mode_menu.exec_(pos)

    def on_search(self):
        """执行搜索（后台线程）"""
        if not self.online_api:
            self._show_toast("在线音乐服务不可用", 3000)
            return
        keywords = self.search_input.text().strip()
        if not keywords:
            return
        mode = self._search_mode
        self._search_seq += 1
        seq = self._search_seq
        self.setWindowTitle(f"VeryGoodPlayer · 搜索: {keywords}")
        print(f"🔍 正在搜索「{keywords}」(模式: {self.search_mode_btn.text()})...")
        # 正在后台搜索 → 仅显示遮罩，不重复发起请求（与其他遮罩逻辑一致）
        if self._search_loading:
            self._show_loading("搜索中", "search")
            return
        self._search_loading = True
        self._show_loading("搜索中", "search")
        self._run_in_thread(self.online_api.search,
                            lambda result: self._on_search_done(result, keywords, seq),
                            keywords, mode=mode)

    def _on_search_done(self, result, keywords, seq):
        self._search_loading = False
        # 期间又发起了更新的搜索（如切换模式），丢弃本次过期结果
        if seq != self._search_seq:
            return
        if result is None:
            self._hide_loading("search")
            print(f"❌ 搜索失败")
            return
        songs, err = result
        menu_text = "🎧 发现音乐"
        self.playlist_data[menu_text] = songs
        # 若已切换到其他侧栏：只缓存结果，不自动切回渲染，避免干扰当前浏览位置
        # （与其他遮罩一致的后台加载逻辑）
        cur_menu = getattr(self, '_current_playlist_menu', None) or self.current_menu
        if cur_menu != menu_text:
            self._hide_loading("search")
            print(f"✅ 搜索结果已缓存 {len(songs)} 首（已在其他侧栏，不自动切回）")
            return
        self.display_playlist(menu_text)
        self._hide_loading("search")
        if err:
            print(f"❌ {err}")
        else:
            print(f"✅ 找到 {len(songs)} 首歌曲")

    def _show_toplist_songs(self, toplist_id, name):
        """加载指定排行榜并显示歌曲列表（含返回按钮）。

        采用与猜你喜欢一致的遮罩加载逻辑：加载期间显示半透明遮罩（下层保留
        排行榜卡片网格，不被清空）；防重复请求；切换侧栏不中断后台任务；
        结果返回时若已离开排行榜页，则只缓存数据、不自动切回渲染。
        """
        if not self.online_api:
            self._show_toast("在线音乐服务不可用", 3000)
            return
        self._current_toplist_name = name
        self.setWindowTitle(f"VeryGoodPlayer · {name}")
        # 正在后台加载 → 仅显示遮罩（下层保留卡片网格），不重复发起请求
        if self._toplist_loading:
            self._show_loading("加载中…", "toplist")
            return
        print(f"📊 正在加载排行榜「{name}」...")
        self._toplist_loading = True
        self._show_loading("加载中…", "toplist")
        self._run_in_thread(self.online_api.get_toplist_songs,
                            lambda result: self._on_toplist_done(result, name),
                            toplist_id)

    def _on_toplist_done(self, result, name):
        self._toplist_loading = False
        self._hide_loading("toplist")
        if result is None:
            print(f"❌ 加载排行榜失败")
            return
        songs, err = result
        menu_text = "📊 排行榜"
        self.playlist_data[menu_text] = songs
        # 若已切换到其他侧栏：只缓存结果，不自动切回，避免干扰当前浏览位置；
        # 但记录已进入歌曲页状态，用户切回排行榜时直接显示歌曲列表而非母列表
        cur_menu = getattr(self, '_current_playlist_menu', None) or self.current_menu
        if cur_menu != "📊 排行榜":
            self._toplist_viewing_songs = True
            self._toplist_browsing = False
            print(f"✅ 排行榜已缓存 {len(songs)} 首（已在其他侧栏，不自动切回）")
            return
        self._toplist_viewing_songs = True
        self._toplist_browsing = False
        # 隐藏卡片容器、恢复歌曲表格
        self._toplist_cards_widget.hide()
        self.song_table.show()
        # 一次性切换表格列显隐 + 表头 + 内容（用户无感知中间态）
        for c in range(7):
            self.song_table.setColumnHidden(c, False)
        self.song_table.horizontalHeaderItem(1).setText("歌曲名")
        self._show_toplist_toolbar(name)
        self.display_playlist(menu_text)
        if err:
            print(f"❌ {err}")
        else:
            print(f"✅ 已加载 {len(songs)} 首歌曲")

    def _show_toplist_toolbar(self, name):
        """为排行榜歌曲页面显示 返回 + 标题 工具栏"""
        self.toolbar.show()
        if hasattr(self, '_toplist_back_btn'):
            for w in [self._toplist_back_btn, self._toplist_title_lbl]:
                try: w.deleteLater()
                except: pass
        self._toplist_back_btn = QPushButton("返回", self.toolbar)
        reg_theme(self._toplist_back_btn, f"""
            QPushButton {{ background: transparent; border: none;
                font-size: {int(14*self.scale)}px; color: #EC4141;
                padding: {int(4*self.scale)}px {int(8*self.scale)}px;
            }}
            QPushButton:hover {{ color: #CC3333; }}
        """)
        back_icon = self._render_svg_icon("caret-left.svg", self.theme_color,
                                          int(18*self.scale))
        if back_icon is not None:
            self._toplist_back_btn.setIcon(back_icon)
            self._toplist_back_btn.setIconSize(
                QSize(int(18*self.scale), int(18*self.scale)))
        self._toplist_back_btn.setCursor(Qt.PointingHandCursor)
        self._toplist_back_btn.clicked.connect(self._back_to_toplist_list)
        self._toplist_title_lbl = QLabel(name, self.toolbar)
        self._toplist_title_lbl.setAlignment(Qt.AlignCenter)
        self._toplist_title_lbl.setStyleSheet(f"""
            font-size: {int(14*self.scale)}px; font-weight: bold;
            color: #1A1A1A; border: none; background: transparent;
        """)
        while self.toolbar_layout.count():
            item = self.toolbar_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
        self.toolbar_layout.addWidget(self._toplist_back_btn, 0)
        self.toolbar_layout.addStretch(1)
        self.toolbar_layout.addWidget(self._toplist_title_lbl, 0)
        self.toolbar_layout.addStretch(1)
        self._toplist_back_btn.show()
        self._toplist_title_lbl.show()

    # ---------- 标题栏拖拽 ----------
    def _titlebar_mouse_press(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def _titlebar_mouse_move(self, event):
        if event.buttons() & Qt.LeftButton and hasattr(self, '_drag_pos'):
            self.move(event.globalPos() - self._drag_pos)
            event.accept()

    def _titlebar_mouse_dblclick(self, event):
        # 双击标题栏：最大化 / 还原（无边框窗口无原生标题栏，需手动实现）
        if event.button() == Qt.LeftButton:
            self._toggle_maximize()
            event.accept()

    def _toggle_maximize(self):
        # 最大化按钮与标题栏双击共用的切换逻辑
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _render_titlebar_icon(self, svg_name, flip=False):
        """渲染标题栏窗口按钮 SVG 图标（resources/icons/square*.svg）。

        统一着色为标题栏前景色 #1A1A1A；flip=True 时水平镜像（还原态
        square2 图标按设计要求左右翻转）。按 DPR 放大物理分辨率，高分屏清晰。"""
        path = os.path.join(self.icons_folder, svg_name)
        if not os.path.exists(path):
            return QIcon()
        try:
            import re
            from PyQt5.QtSvg import QSvgRenderer
            with open(path, 'r', encoding='utf-8') as f:
                svg_text = re.sub(r'fill:\s*#[0-9a-fA-F]{3,8}',
                                  'fill:#1A1A1A', f.read())
            sz = int(14 * self.scale)
            dpr = self.devicePixelRatio() or 1.0
            pm = QPixmap(max(1, int(round(sz * dpr))),
                         max(1, int(round(sz * dpr))))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            try:
                p.setRenderHint(QPainter.Antialiasing)
                p.setRenderHint(QPainter.SmoothPixmapTransform)
                renderer = QSvgRenderer(svg_text.encode('utf-8'))
                if renderer.isValid():
                    renderer.render(p, QRectF(0, 0, sz, sz))
            finally:
                p.end()
            if flip:
                pm = pm.transformed(QTransform().scale(-1.0, 1.0))
                pm.setDevicePixelRatio(dpr)
            return QIcon(pm)
        except Exception:
            return QIcon()

    def _sync_max_btn_icon(self):
        # 普通状态显示 square（最大化），最大化状态显示左右翻转的 square2（还原）
        if hasattr(self, '_btn_max'):
            if self.isMaximized():
                self._btn_max.setIcon(
                    self._render_titlebar_icon("square2.svg", flip=True))
            else:
                self._btn_max.setIcon(self._render_titlebar_icon("square.svg"))

    # ---------- 事件过滤 ----------
    def eventFilter(self, obj, event):
        # 初始化尚未完成时跳过所有事件（部分控件此时可能还未创建）
        if not hasattr(self, 'mode_btn'):
            return super().eventFilter(obj, event)

        # ---- Tooltip 统一控制 ----
        # 拦截所有 ToolTip 事件，由自定义定时器统一管理
        if event.type() == QEvent.ToolTip:
            return True

        # 排行榜卡片容器尺寸变化 → 动态调整棋盘边距/间距（列数固定无需重建）
        if obj is getattr(self, '_toplist_cards_widget', None) \
                and event.type() == QEvent.Resize:
            self._adjust_toplist_spacing()

        # 歌单卡片滚动区宽度变化 → 布局(列数/卡片尺寸)变化时才重排。
        # 监听滚动区自身（其宽度只随窗口变化，与滚动条显隐解耦，不会振荡）
        _pl_scroll = getattr(self, '_playlist_cards_scroll', None)
        if _pl_scroll is not None and obj is _pl_scroll \
                and event.type() == QEvent.Resize:
            if self._playlist_layout_metrics() != self._playlist_layout:
                self._relayout_playlist_cards()

        # 右侧面板尺寸变化 → 同步可见覆盖层位置（加载遮罩/猜你喜欢失败提示层）
        if obj is getattr(self, 'right_panel', None) \
                and event.type() == QEvent.Resize:
            for overlay in (getattr(self, 'loading_overlay', None),
                            getattr(self, '_recommend_error_overlay', None)):
                if overlay is None:
                    continue
                try:
                    if overlay.isVisible():
                        overlay.setGeometry(self.right_panel.rect())
                except RuntimeError:
                    pass  # 覆盖层已被销毁

        # 表格 viewport：鼠标移动检测单元格 → 启动/重置 tooltip 定时器
        if obj == self.song_table.viewport():
            if event.type() == QEvent.MouseMove:
                idx = self.song_table.indexAt(event.pos())
                if idx.isValid() and idx.column() in (1, 2, 3):
                    item = self.song_table.itemFromIndex(idx)
                    if item and item.text():
                        text = item.text()
                        key = (obj, idx.row(), idx.column())
                        if (self._tooltip_owner != key or
                                self._tooltip_text != text):
                            self._cancel_tooltip()
                            cell_rect = self.song_table.visualItemRect(item)
                            self._tooltip_owner = key
                            self._tooltip_text = text
                            self._tooltip_anchor = QRect(
                                self.song_table.viewport().mapToGlobal(
                                    cell_rect.topLeft()),
                                cell_rect.size())
                            self._tooltip_timer.start(1000)
                    else:
                        self._cancel_tooltip()
                else:
                    self._cancel_tooltip()
                return False
            if event.type() == QEvent.Leave:
                self._cancel_tooltip()
                return False

        # 播放模式按钮：进入/离开控制 tooltip
        if obj == self.mode_btn:
            if event.type() == QEvent.Enter:
                self._cancel_tooltip()
                self._tooltip_owner = (obj,)
                self._tooltip_text = obj.toolTip()
                btn_rect = obj.geometry()
                self._tooltip_anchor = QRect(
                    obj.mapToGlobal(btn_rect.topLeft()), btn_rect.size())
                self._tooltip_timer.start(1000)
                return False
            if event.type() == QEvent.Leave:
                self._cancel_tooltip()
                return False

        # ---- 鼠标点击 ----
        if event.type() == QEvent.MouseButtonPress:
            # 底部封面/歌名点击 → 打开详情；已打开则关闭（触发区域与打开区域相同）
            if obj in (self.cover_label, self.song_info_widget):
                panel = getattr(self, '_detail_panel', None)
                open_now = False
                if panel is not None:
                    try:
                        open_now = panel.isVisible() and not panel._closed
                    except RuntimeError:
                        # 面板 C++ 对象已被删除（滑出动画结束后 deleteLater）
                        open_now = False
                        self._detail_panel = None
                if open_now:
                    panel._slide_out()
                else:
                    self._open_detail()
                return True
            # 面板打开时，点击外部 → 关闭面板
            if self._playlist_panel.isVisible() and obj != self.playlist_btn:
                global_pos = event.globalPos()
                panel_rect = QRect(
                    self._playlist_panel.mapToGlobal(QPoint(0, 0)),
                    self._playlist_panel.mapToGlobal(
                        self._playlist_panel.rect().bottomRight()
                    )
                )
                if not panel_rect.contains(global_pos):
                    self._hide_playlist_panel()
        return super().eventFilter(obj, event)

    def _cancel_tooltip(self):
        """立即隐藏 tooltip 并重置所有状态"""
        self._tooltip_timer.stop()
        self._tooltip_owner = None
        self._tooltip_text = ""
        self._tooltip_anchor = QRect()
        QToolTip.hideText()

    def _on_tooltip_timer(self):
        """定时器到期：在锚点上方显示 tooltip，空间不足时自动转为下方"""
        if not self._tooltip_text or self._tooltip_anchor.isNull():
            return
        tip_font = QFont()
        tip_font.setPixelSize(int(14 * self.scale))
        fm = QFontMetrics(tip_font)
        pad_h = int(8 * self.scale) * 2
        pad_v = int(4 * self.scale) * 2
        tip_w = fm.horizontalAdvance(self._tooltip_text) + pad_h
        tip_h = fm.height() + pad_v
        # 默认：锚点上方居中，间距 10px
        tip_x = self._tooltip_anchor.x() + (
            self._tooltip_anchor.width() - tip_w) // 2
        tip_y = self._tooltip_anchor.y() - tip_h - int(10 * self.scale)
        # 屏幕边界裁剪
        screen = QApplication.primaryScreen().availableGeometry()
        tip_x = max(screen.left() + 5, min(tip_x, screen.right() - tip_w - 5))
        if tip_y < screen.top():
            # 上方空间不足，改为锚点下方显示
            tip_y = self._tooltip_anchor.bottom() + int(10 * self.scale)
        # 避让鼠标光标：若 tooltip 区域覆盖光标位置，垂直偏移以避免闪现
        cursor_pos = QCursor.pos()
        tip_rect = QRect(tip_x, tip_y, tip_w, tip_h)
        if tip_rect.contains(cursor_pos):
            if cursor_pos.y() - self._tooltip_anchor.center().y() < 0:
                # 光标在锚点上方 → tooltip 移到锚点下方
                tip_y = self._tooltip_anchor.bottom() + int(10 * self.scale)
            else:
                tip_y = self._tooltip_anchor.y() - tip_h - int(10 * self.scale)
        # 不传 rect 参数，避免 Qt 将 tooltip 重新定位到鼠标光标
        QToolTip.showText(QPoint(tip_x, tip_y), self._tooltip_text, self)

    def _is_child_of(self, widget, parent):
        """递归检查 widget 是否为 parent 的子孙"""
        while widget is not None:
            if widget == parent:
                return True
            widget = widget.parent()
        return False

    def _open_detail(self):
        """打开歌曲详情面板（右侧区域）"""
        if self.current_playing_row < 0:
            return
        # 已打开则静默忽略
        if hasattr(self, '_detail_panel') and self._detail_panel is not None:
            if not self._detail_panel._closed:
                return
        if self.current_playing_row >= len(self._panel_queue):
            return
        # 关闭旧面板（理论上不会走到这里）
        if hasattr(self, '_detail_panel') and self._detail_panel is not None:
            try:
                self._detail_panel._closed = True
                self._detail_panel._lyric_timer.stop()
                self._detail_panel.hide()
                self._detail_panel.deleteLater()
            except:
                pass
            self._detail_panel = None
        song_info = self._panel_queue[self.current_playing_row]
        song_info['_start_pos'] = self.current_start_pos
        self._detail_panel = DetailPanel(self.body_widget, self,
                                          song_info, self.online_api, self.scale)
        self._detail_panel._slide_in()

    # ---------- 最小化 / 恢复 ----------
    def _on_minimize_clicked(self):
        self._saved_geometry = self.geometry()
        self.showMinimized()

    def closeEvent(self, event):
        # 关闭主窗口时一并关闭看板娘
        try:
            m = getattr(self, 'mascot', None)
            if m is not None:
                m.close()
        except Exception:
            pass
        super().closeEvent(event)

    def changeEvent(self, event):
        if event.type() == QEvent.WindowStateChange:
            self._sync_max_btn_icon()  # 最大化/还原图标随窗口状态切换
            old = event.oldState()
            if (old & Qt.WindowMinimized) and not (self.windowState() & Qt.WindowMinimized):
                saved = getattr(self, '_saved_geometry', None)
                if saved and saved.width() >= 100:
                    self.setGeometry(saved)
                self.activateWindow()
                self.raise_()
        super().changeEvent(event)

    def nativeEvent(self, eventType, message):
        try:
            import ctypes
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == 0x0112:  # WM_SYSCOMMAND
                if msg.wParam == 0xF020:  # SC_MINIMIZE
                    self._hwnd_minimize()
                    return True, 0
                elif msg.wParam == 0xF120:  # SC_RESTORE
                    self._hwnd_restore()
                    return True, 0
            elif msg.message == 0x0084:  # WM_NCHITTEST
                # 无边框窗口默认无缩放边缘：鼠标位于窗口边缘热区时返回原生命中值，
                # 由 Windows 原生处理拖拽缩放（平滑且支持 Aero 贴边分屏）。
                # 注意：QLayout 布局本身支持拉伸，这里只需解锁窗口缩放能力。
                if not self.isMaximized() and not self.isFullScreen():
                    x = ctypes.c_short(msg.lParam & 0xFFFF).value
                    y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                    g = self.geometry()  # 无边框窗口 geometry 即原生窗口矩形
                    m = self._resize_margin
                    on_l = x - g.left() < m
                    on_r = g.right() - x < m
                    on_t = y - g.top() < m
                    on_b = g.bottom() - y < m
                    if on_t and on_l:
                        return True, 13  # HTTOPLEFT
                    if on_t and on_r:
                        return True, 14  # HTTOPRIGHT
                    if on_b and on_l:
                        return True, 16  # HTBOTTOMLEFT
                    if on_b and on_r:
                        return True, 17  # HTBOTTOMRIGHT
                    if on_l:
                        return True, 10  # HTLEFT
                    if on_r:
                        return True, 11  # HTRIGHT
                    if on_t:
                        return True, 12  # HTTOP
                    if on_b:
                        return True, 15  # HTBOTTOM
        except:
            pass
        return super().nativeEvent(eventType, message)

    def _hwnd_minimize(self):
        self._saved_geometry = self.geometry()
        self.showMinimized()

    def _hwnd_restore(self):
        saved = getattr(self, '_saved_geometry', None)
        self.showNormal()
        if saved and saved.width() >= 100:
            self.setGeometry(saved)
        self.activateWindow()
        self.raise_()

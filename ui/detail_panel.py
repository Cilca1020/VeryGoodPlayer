"""详情面板模块：歌曲详情、封面图、歌词展示与播放同步。"""
import os

from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *

from core.utils import parse_lrc, read_embedded_cover, read_embedded_lyric, reg_theme
from ui.widgets import BackArrow

class DetailPanel(QWidget):
    """全区域详情面板，覆盖 body_widget（左菜单+表格）"""
    def __init__(self, parent, player, song_info, api, scale):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._player = player       # MusicPlayer 引用，用于监听切歌
        self._song_info = song_info
        self._api = api
        self._scale = scale
        self._lyrics = []
        self._anim = None
        self._closed = False
        self._last_song_id = song_info.get('song_id') or id(song_info)
        self._lyric_reload_state = None  # 记录已重载的歌词状态，避免重复重载

        self.setStyleSheet("DetailPanel { background-color: #FFFFFF; }")
        self._setup_ui()
        self._load_lyrics()
        self._lyric_timer = QTimer(self)
        self._lyric_timer.timeout.connect(self._sync_lyrics)
        self._lyric_timer.start(200)

    def _setup_ui(self):
        pw, ph = self.parent().width(), self.parent().height()
        self.setGeometry(0, ph, pw, ph)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(int(28*self._scale), int(12*self._scale) + 28,
                                  int(12*self._scale), int(12*self._scale))
        layout.setSpacing(int(28*self._scale))

        # ▼ 返回箭头
        self._back_btn = BackArrow(self)
        self._back_btn.clicked.connect(self._slide_out)
        self._back_btn.move(int(16*self._scale), int(12*self._scale))

        # 封面
        try:
            cover_size = int(200 * self._scale)
            self._cover_label = QLabel()
            self._cover_label.setFixedSize(cover_size, cover_size)
            self._cover_label.setScaledContents(True)
            # 灰色模糊阴影
            shadow = QGraphicsDropShadowEffect()
            shadow.setBlurRadius(int(20 * self._scale))
            shadow.setColor(QColor(0, 0, 0, 50))
            shadow.setOffset(0, int(4 * self._scale))
            self._cover_label.setGraphicsEffect(shadow)
            self._load_cover()
        except:
            pass

        # 右侧
        right = QWidget()
        right.setStyleSheet("background: transparent; border: none;")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(int(8*self._scale))
        name = str(self._song_info.get('name', '')) if self._song_info.get('name') is not None else ''
        singer = str(self._song_info.get('singer', '')) if self._song_info.get('singer') is not None else ''
        self._name_label = QLabel(name)
        self._name_label.setAlignment(Qt.AlignCenter)
        self._name_label.setStyleSheet(f"""
            font-size: {int(22*self._scale)}px; font-weight: bold;
            color: #1A1A1A; border: none; background: transparent;
        """)
        self._artist_label = QPushButton(singer)
        self._artist_label.setCursor(Qt.PointingHandCursor)
        self._artist_label.setFlat(True)
        reg_theme(self._artist_label, f"""
            QPushButton {{
                font-size: {int(16*self._scale)}px; color: #999999;
                border: none; background: transparent; padding: 0;
            }}
            QPushButton:hover {{
                color: #EC4141;
            }}
        """)
        self._artist_label.clicked.connect(self._on_artist_clicked)
        self._lyric_list = QListWidget()
        reg_theme(self._lyric_list, f"""
            QListWidget {{ background: transparent; border: none;
                font-size: {int(16*self._scale)}px; color: #666666; }}
            QListWidget::item {{
                padding: {int(8*self._scale)}px 0; border: none;
                text-align: center;
            }}
            QListWidget::item:selected {{
                background: transparent; color: #EC4141;
                font-weight: bold; font-size: {int(18*self._scale)}px;
            }}
        """)
        self._lyric_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._lyric_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._lyric_list.verticalScrollBar().setStyleSheet(
            "QScrollBar:vertical { width: 0; margin:0; border:none; background:transparent; }"
        )
        self._lyric_list.setSelectionMode(QListWidget.NoSelection)
        # 滚轮检测：用户滚动后暂停自动居中 2 秒
        self._user_scrolled = False
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._on_scroll_timeout)
        self._lyric_list.viewport().installEventFilter(self)
        rl.addWidget(self._name_label)
        rl.addWidget(self._artist_label)
        rl.addSpacing(int(16*self._scale))
        # 歌词独立容器，与歌名区分
        self._lyric_container = QWidget()
        self._lyric_container.setStyleSheet("background: transparent; border: none;")
        lc_layout = QVBoxLayout(self._lyric_container)
        lc_layout.setContentsMargins(0, 0, 0, 0)
        lc_layout.addWidget(self._lyric_list, 1)
        rl.addWidget(self._lyric_container, 1)
        # 左侧封面容器（居中）
        left_side = QWidget()
        left_side.setStyleSheet("background: transparent; border: none;")
        left_layout = QVBoxLayout(left_side)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addStretch(1)
        try:
            left_layout.addWidget(self._cover_label, 0, Qt.AlignCenter)
        except:
            pass
        left_layout.addStretch(1)
        layout.addWidget(left_side, 0)
        layout.addWidget(right, 1)
        # 父窗口尺寸变化时同步缩放
        self.parent().installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj == self.parent() and event.type() == QEvent.Resize:
            if not self._closed and self._anim is None:
                self.setGeometry(0, 0, obj.width(), obj.height())
        if obj == self._lyric_list.viewport():
            if event.type() == QEvent.Wheel:
                self._user_scrolled = True
                self._scroll_timer.start(2000)
                return super().eventFilter(obj, event)  # 仅放行滚轮
            # 禁用歌词区所有鼠标操作（点击/双击/按下/释放/拖动），只保留滚轮
            if event.type() in (
                QEvent.MouseButtonPress,
                QEvent.MouseButtonRelease,
                QEvent.MouseButtonDblClick,
                QEvent.MouseMove,
            ):
                return True  # 吞掉所有鼠标交互事件
        return super().eventFilter(obj, event)

    def _on_scroll_timeout(self):
        """用户停止滚动 2 秒后，恢复自动居中"""
        self._user_scrolled = False

    def _show_cover_placeholder(self):
        """立即显示占位封面（🎵 灰底），不阻塞面板打开。"""
        try:
            self._cover_label.setText("🎵")
            self._cover_label.setAlignment(Qt.AlignCenter)
            self._cover_label.setStyleSheet(f"""
                font-size: {int(50*self._scale)}px;
                background-color: #F0F0F2;
                border-radius: {int(12*self._scale)}px;
            """)
        except:
            pass

    def _render_cover_pixmap(self, pix):
        """把封面 QPixmap 以设备像素比(DPR)高清渲染到封面标签（圆角裁切）。"""
        s = self._cover_label.width()
        try:
            if not pix.isNull():
                # 按设备像素比(DPR)放大物理分辨率并标定，避免高分屏
                # (125%/150%/200%) 下位图被显示层放大产生像素点/模糊；
                # 启用平滑缩放，非整数倍缩放时边缘平滑无锯齿
                dpr = self.devicePixelRatio() or 1.0
                r = QPixmap(max(1, int(round(s * dpr))),
                            max(1, int(round(s * dpr))))
                r.setDevicePixelRatio(dpr)
                r.fill(Qt.transparent)
                p = QPainter(r)
                # 用 try/finally 保证 QPainter 必定 end()，
                # 避免绘制中途异常时 QPixmap 在仍被绘制状态下被销毁
                # （Qt 会报 QPaintDevice: Cannot destroy paint device...）
                try:
                    p.setRenderHint(QPainter.Antialiasing)
                    p.setRenderHint(QPainter.SmoothPixmapTransform)
                    ph = QPainterPath()
                    ph.addRoundedRect(0, 0, s, s, int(12*self._scale), int(12*self._scale))
                    p.setClipPath(ph)
                    # 注意：PyQt5 无 drawPixmap(QRectF, QPixmap) 重载，
                    # 必须用 QRect + QPixmap（s 为整数），否则抛 TypeError
                    p.drawPixmap(QRect(0, 0, s, s), pix)
                finally:
                    p.end()
                # 清除占位灰底样式（_show_cover_placeholder 设置的），
                # 还原透明背景，让封面阴影效果正常渲染
                self._cover_label.setStyleSheet("")
                self._cover_label.setPixmap(r)
                return True
        except Exception:
            pass
        return False

    def _load_cover(self):
        s = self._cover_label.width()
        cover_url = self._song_info.get('cover_url')
        sid = self._song_info.get('song_id')
        path = None
        embedded = None  # 内嵌封面字节（mp3/falc），优先使用
        try:
            if cover_url and self._api and sid:
                path = self._api.download_cover(cover_url, sid)
            if not path:
                fp = self._song_info.get('filepath')
                if fp:
                    # 优先从音频文件内嵌封面读取（封面已写入文件本体）
                    embedded = read_embedded_cover(fp)
                    if embedded is None:
                        # 兼容：同目录同名图片文件
                        base = os.path.splitext(fp)[0]
                        for e in ['.jpg', '.jpeg', '.png']:
                            c = base + e
                            if os.path.exists(c):
                                path = c; break
        except:
            path = None
        if embedded is not None:
            try:
                pix = QPixmap()
                if pix.loadFromData(embedded) and not pix.isNull():
                    self._render_cover_pixmap(pix)
                    return
            except:
                pass
        if path and os.path.exists(path):
            try:
                pix = QPixmap(path)
                if not pix.isNull():
                    self._render_cover_pixmap(pix)
                    return
            except:
                pass
        try:
            self._cover_label.setText("🎵")
            self._cover_label.setAlignment(Qt.AlignCenter)
            self._cover_label.setStyleSheet(f"""
                font-size: {int(50*self._scale)}px;
                background-color: #F0F0F2;
                border-radius: {int(12*self._scale)}px;
            """)
            self.setStyleSheet("DetailPanel { background-color: #FFFFFF; }")
        except:
            pass

    def _load_lyrics(self):
        lrc_text = ""
        try:
            fp = self._song_info.get('filepath')
            if fp:
                # 0) 优先从音频文件内嵌歌词读取（歌词已写入文件本体）
                if not lrc_text:
                    lrc_text = read_embedded_lyric(fp)
                # 1) 同目录同名 .lrc（兼容旧下载/外部导入）
                if not lrc_text:
                    lp = os.path.splitext(fp)[0] + ".lrc"
                    if os.path.exists(lp):
                        with open(lp, 'r', encoding='utf-8', errors='ignore') as f:
                            lrc_text = f.read()
                # 2) songs 目录下同名 .lrc
                if not lrc_text:
                    dl_dir = os.path.dirname(fp)
                    alt = os.path.join(dl_dir, os.path.splitext(os.path.basename(fp))[0] + ".lrc")
                    if alt != lp and os.path.exists(alt):
                        with open(alt, 'r', encoding='utf-8', errors='ignore') as f:
                            lrc_text = f.read()
            if not lrc_text:
                # 歌词已在歌曲加载期间后台预取并缓存到 _lyric_text，
                # 无需再同步联网（避免阻塞面板打开）
                lrc_text = self._song_info.get('_lyric_text', '')
        except Exception as e:
            print(f"加载歌词异常：{e}")
        self._lyrics = parse_lrc(lrc_text)
        self._lyric_list.clear()
        self._lyric_list.setSelectionMode(QListWidget.SingleSelection)
        if self._lyrics:
            for _, t in self._lyrics:
                it = QListWidgetItem(t)
                it.setTextAlignment(Qt.AlignCenter)
                self._lyric_list.addItem(it)
            self._last_center_idx = -1
            QTimer.singleShot(0, self._adjust_lyrics_center)
        else:
            # 仅当后台歌词预取已明确完成（_lyric_loaded）但仍无文本时，
            # 才判定为“真的没有歌词”；否则显示加载中，等待后台结果回来后重载
            if self._song_info.get('_lyric_loaded'):
                tip = "暂无歌词"
            else:
                tip = "歌词加载中…"
            it = QListWidgetItem(tip)
            it.setTextAlignment(Qt.AlignCenter)
            it.setFlags(it.flags() & ~Qt.ItemIsSelectable)
            self._lyric_list.addItem(it)

    def _adjust_lyrics_center(self):
        """根据视口高度调整歌词列表的垂直居中"""
        if self._closed or not self._lyrics:
            return
        vp_h = self._lyric_list.viewport().height()
        if vp_h <= 0:
            QTimer.singleShot(50, self._adjust_lyrics_center)
            return
        base_style = f"""
            QListWidget {{ background: transparent; border: none;
                font-size: {int(16*self._scale)}px; color: #666666; }}
            QListWidget::item {{
                padding: {int(8*self._scale)}px 0; border: none;
                text-align: center;
            }}
            QListWidget::item:selected {{
                background: transparent; color: #EC4141;
                font-weight: bold; font-size: {int(18*self._scale)}px;
            }}
        """
        total_h = self._lyric_list.sizeHintForRow(0) * self._lyric_list.count()
        if total_h < vp_h:
            pad = (vp_h - total_h) // 2
            base_style += f"""
                QListWidget::item:first {{ padding-top: {pad}px; }}
            """
        self._lyric_list.setStyleSheet(base_style)

    def _on_artist_clicked(self):
        """点击歌手：跳转到搜索页搜索该歌手（与双击主列表歌手列效果一致）"""
        singer = (self._song_info.get('singer') or '').strip()
        if singer and hasattr(self._player, '_goto_search_page'):
            self._slide_out()  # 跳转前先收起面板，避免遮挡搜索结果
            self._player._goto_search_page("歌手", "artist", singer)

    def _reload_for_new_song(self, cur_song):
        """切歌时更新面板：标题、封面、歌词"""
        self._song_info = cur_song
        self._last_song_id = cur_song.get('song_id') or id(cur_song)
        self._lyric_reload_state = None  # 新歌重置重载状态，允许重新加载歌词
        cur_song['_start_pos'] = self._player.current_start_pos
        # 更新歌名、歌手
        self._name_label.setText(str(cur_song.get('name', '')))
        self._artist_label.setText(str(cur_song.get('singer', '')))
        self._load_cover()
        self._load_lyrics()

    def _sync_lyrics(self):
        if self._closed:
            return

        # 检测切歌（仅当新歌真正就绪时才切换）
        try:
            p = self._player
            row = p.current_playing_row
            if 0 <= row < len(p._panel_queue) and p._song_ready:
                cur_song = p._panel_queue[row]
                cur_id = cur_song.get('song_id') or id(cur_song)
                if cur_id != self._last_song_id:
                    print(f"🔄 切歌 → 重载面板")
                    self._reload_for_new_song(cur_song)
                    return
        except:
            pass

        if not self._lyrics:
            # 后台歌词预加载可能刚完成：检测 _lyric_text 是否已就绪，或预取已结束
            # （_lyric_loaded 为真但无文本）以把“歌词加载中…”切换为“暂无歌词”
            if self._song_info.get('_lyric_text') or self._song_info.get('_lyric_loaded'):
                # 仅当歌词状态相比上次重载发生变化时才重载，避免在线无歌词时无限循环重载
                state_key = (self._song_info.get('_lyric_text'), self._song_info.get('_lyric_loaded'))
                if state_key != self._lyric_reload_state:
                    self._lyric_reload_state = state_key
                    print("🔄 后台歌词就绪，重新加载")
                    self._load_lyrics()
            return
        try:
            import pygame
            pos_ms = pygame.mixer.music.get_pos()
            if pos_ms < 0:
                return
            # 使用播放器的权威起始位置（切歌/进度跳转都会更新 current_start_pos），
            # 避免依赖 song_info['_start_pos'] 的引用同步，确保跳转后歌词高亮正确
            cur = self._player.current_start_pos + pos_ms / 1000.0
        except:
            return
        idx = 0
        for i, (t, _) in enumerate(self._lyrics):
            if t <= cur:
                idx = i
        try:
            if self._lyric_list.count() > idx:
                self._lyric_list.setCurrentRow(idx)
                # 用户自发滚动后暂停 2 秒自动居中；期间跳过滚动
                if self._user_scrolled:
                    return
                target = self._lyric_list.item(idx)
                if target is not None:
                    self._center_on_item(target)
        except:
            pass

    def _center_on_item(self, item):
        """平滑地将指定歌词行滚动到列表可视中心（QListWidget.PositionAtCenter）。"""
        sb = self._lyric_list.verticalScrollBar()
        if sb is None or sb.maximum() <= 0:
            # 没有可滚动空间，直接定位即可
            self._lyric_list.scrollToItem(item, QListWidget.PositionAtCenter)
            return
        orig = sb.value()
        self._lyric_list.scrollToItem(item, QListWidget.PositionAtCenter)
        target_val = sb.value()
        if orig == target_val:
            return
        sb.setValue(orig)  # 先回退到原位置，再用动画过渡
        if getattr(self, '_scroll_anim', None) is not None:
            self._scroll_anim.stop()
        anim = QPropertyAnimation(sb, b"value")
        anim.setDuration(300)
        anim.setStartValue(orig)
        anim.setEndValue(target_val)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.finished.connect(lambda: setattr(self, '_scroll_anim', None))
        anim.start()
        self._scroll_anim = anim

    def _slide_in(self):
        pw, ph = self.parent().width(), self.parent().height()
        start_y = int(ph * 0.85)
        self.setGeometry(0, start_y, pw, ph)
        self.show()
        self.raise_()
        anim = QPropertyAnimation(self, b"geometry")
        anim.setDuration(300)
        anim.setStartValue(QRect(0, start_y, pw, ph))
        anim.setEndValue(QRect(0, 0, pw, ph))
        anim.finished.connect(lambda: self._on_slide_in_done())
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        self._anim = anim

    def _on_slide_in_done(self):
        """滑入动画完成后回调：清理动画引用 + 异步加载封面。"""
        self._anim = None
        if not self._closed:
            self._load_cover()

    def _slide_out(self):
        if self._closed:
            return
        self._closed = True
        self._anim = None
        self._lyric_timer.stop()
        pw, ph = self.parent().width(), self.parent().height()
        start_y = int(ph * 0.85)
        anim = QPropertyAnimation(self, b"geometry")
        anim.setDuration(250)
        anim.setStartValue(QRect(0, 0, pw, ph))
        anim.setEndValue(QRect(0, start_y, pw, ph))
        anim.setEasingCurve(QEasingCurve.InCubic)
        # 延迟到事件循环下一轮再删除，避免动画最后一帧尚未渲染完
        # 就销毁正在绘制的 paint device（QPaintDevice 错误）
        anim.finished.connect(lambda: QTimer.singleShot(0, self.deleteLater))
        anim.start()
        self._anim = anim

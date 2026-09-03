"""基础 UI 控件模块：滚动标签、加载遮罩、启动画面、媒体控制按钮、
表格播放按钮、通用线程、返回箭头、可点击滑杆与歌单列表控件。"""
import os
import re

from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *

from core.utils import (_theme_color, _theme_rgb, resource_path, icon_path,
                        dark_paint_hex)

# 开启高分屏自适应（需在 QApplication 创建之前设置，全局生效）
QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)

class ScrollLabel(QLabel):
    """文字超长时来回滚动"""
    def __init__(self, text="", step=1, interval=30, parent=None):
        super().__init__(text, parent)
        self._step = step
        self._interval = interval  # 毫秒
        self._offset = 0
        self._text_width = 0
        self._forward = True
        self._pause = 0  # 剩余暂停帧数
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self.setAttribute(Qt.WA_Hover, True)
        self._hovered = False

    def enterEvent(self, event):
        self._hovered = True
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        super().leaveEvent(event)

    def setText(self, text):
        super().setText(text)
        self._offset = 0
        self._forward = True
        self._pause = 80  # 新文字出现后等待一会儿再滚
        self._update_scroll()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_scroll()

    def _update_scroll(self):
        if self.width() <= 0:
            return
        fm = self.fontMetrics()
        self._text_width = fm.horizontalAdvance(self.text()) + 10
        if self._text_width > self.width():
            self._timer.start(self._interval)
        else:
            self._timer.stop()
            self._offset = 0
        self.update()

    def _tick(self):
        if self._hovered:
            return
        if self._pause > 0:
            self._pause -= 1
            return

        max_offset = max(0, self._text_width - self.width())

        if self._forward:
            self._offset += self._step
            if self._offset >= max_offset:
                self._offset = max_offset
                self._forward = False
                self._pause = 50  # 到末尾停一下
        else:
            self._offset -= self._step
            if self._offset <= 0:
                self._offset = 0
                self._forward = True
                self._pause = 50  # 回到开头停一下
        self.update()

    def paintEvent(self, event):
        if self._text_width <= self.width():
            super().paintEvent(event)
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        p.setClipRect(rect)
        p.setFont(self.font())
        color = self.palette().color(self.foregroundRole())
        p.setPen(color)
        p.drawText(-self._offset, 0, self._text_width, rect.height(),
                   Qt.AlignVCenter | Qt.AlignLeft, self.text())
        p.end()

class LoadingOverlay(QWidget):
    """旋转加载动画 + 半透明遮罩，居中显示；加载期间拦截鼠标事件，
    防止点击穿透到下方的主表格等控件"""
    def __init__(self, parent=None):
        super().__init__(parent)
        # 不设置 WA_TransparentForMouseEvents：遮罩需挡住下层控件交互
        self.setAttribute(Qt.WA_NoSystemBackground, False)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._rotate)
        self._text = "加载中..."
        self._mask_enabled = True  # 默认半透明遮罩；可关闭仅显示转圈
        self.hide()

    # ---- 加载期间禁用下层交互：吞掉所有鼠标/滚轮事件 ----
    def mousePressEvent(self, event):
        event.accept()

    def mouseReleaseEvent(self, event):
        event.accept()

    def mouseDoubleClickEvent(self, event):
        event.accept()

    def mouseMoveEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        event.accept()

    def showEvent(self, event):
        self._angle = 0
        if self.parent():
            self.setGeometry(self.parent().rect())
        self._timer.start(30)
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _rotate(self):
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # 半透明遮罩（可关闭：仅显示转圈动画）
        if self._mask_enabled:
            p.fillRect(self.rect(), QColor(0, 0, 0, 70))
        # 居中绘制旋转圆环
        cx, cy = self.width() // 2, self.height() // 2
        ring_r = 18
        if self._mask_enabled:
            # 遮罩模式：白色系（深色遮罩上清晰可见）
            track_c, text_c = QColor(255, 255, 255, 50), QColor("#FFFFFF")
        else:
            # 无遮罩模式：深色系（浅色/深色页面上经映射保持可见）
            track_c, text_c = QColor(0, 0, 0, 40), QColor(dark_paint_hex("#666666"))
        # 固定轨迹圆（浅色）
        p.setPen(QPen(track_c, 3, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(cx - ring_r, cy - ring_r - 10, ring_r * 2, ring_r * 2, 0, 360 * 16)
        # 旋转弧（深色）
        p.setPen(QPen(QColor(_theme_color()), 3, Qt.SolidLine, Qt.RoundCap))
        p.drawArc(cx - ring_r, cy - ring_r - 10, ring_r * 2, ring_r * 2,
                  self._angle * 16, 270 * 16)
        # 转圈下方一行字（字号调小，避免显得过大）
        p.setPen(text_c)
        f = p.font()
        f.setPointSize(9)
        p.setFont(f)
        p.drawText(QRect(cx - 70, cy + ring_r + 8, 140, 24),
                   Qt.AlignCenter, self._text)
        p.end()

    def set_text(self, text):
        self._text = text
        self.update()

    def set_mask_enabled(self, enabled):
        """控制是否绘制半透明遮罩：False 时仅显示居中转圈动画。"""
        self._mask_enabled = enabled
        self.update()

class SplashScreen(QWidget):
    """开屏画面：作为主窗口内容区（标题栏下方）的覆盖层显示 parts/splash_screen.jpg，
    铺满整个内容区，播放淡出动画后自我删除。

    - 图片 Cover 铺满：放大至塞满整个内容区，多出的部分居中裁剪掉（比例不变形）；
    - 高分屏：按当前 DPR 放大物理分辨率绘制，避免 2K/4K 屏下拉伸模糊或锯齿；
    - 淡出：子控件 windowOpacity 无效，改用 QGraphicsOpacityEffect 驱动 opacity 动画；
    - 点击任意处立即淡出；淡出结束 deleteLater 释放。
    """

    # 淡出动画时长（毫秒）
    _FADE_MS = 250

    def __init__(self, parent, image_path):
        super().__init__(parent)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._image_path = image_path
        self._pixmap = None
        self._fading = False
        # 覆盖父容器（主窗口内容区，不含标题栏）并置顶
        self.setGeometry(parent.rect())
        self._load_image()
        # 用图形效果实现透明度淡出（子控件不能用 windowOpacity）
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)
        self.raise_()
        self.show()
        # 父容器尺寸变化时同步缩放
        parent.installEventFilter(self)

    def _load_image(self):
        """读取图片并预缩放：Cover 铺满内容区——取宽高比中较大的缩放系数，
        两个方向都塞满后居中裁掉超出部分（比例不变形）；
        避免每帧缩放造成锯齿。"""
        try:
            pix = QPixmap(self._image_path)
            if pix.isNull():
                print(f"⚠️ 开屏图片加载失败：{self._image_path}")
                return
            dpr = self.devicePixelRatioF() or 1.0
            parent_w = self.parentWidget().width()
            parent_h = self.parentWidget().height()
            # 物理分辨率 = 逻辑尺寸 × DPR
            phys_w = max(1, int(parent_w * dpr))
            phys_h = max(1, int(parent_h * dpr))
            # Cover：取较大的缩放系数，保证两个方向都铺满
            ratio = max(phys_w / pix.width(), phys_h / pix.height())
            target_w = max(1, int(pix.width() * ratio))
            target_h = max(1, int(pix.height() * ratio))
            scaled = pix.scaled(
                target_w, target_h,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation)
            # 居中裁剪到目标物理尺寸（缩放系数取 max 后不会小于目标）
            x = max(0, (scaled.width() - phys_w) // 2)
            y = max(0, (scaled.height() - phys_h) // 2)
            scaled = scaled.copy(x, y,
                                 min(scaled.width(), phys_w),
                                 min(scaled.height(), phys_h))
            scaled.setDevicePixelRatio(dpr)
            self._pixmap = scaled
        except Exception as e:
            print(f"⚠️ 开屏图片处理失败：{e}")

    def eventFilter(self, obj, event):
        if obj == self.parentWidget() and event.type() == QEvent.Resize:
            self.setGeometry(self.parentWidget().rect())
            self._load_image()
            self.update()
        return super().eventFilter(obj, event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self._pixmap is not None:
            # 图片已铺满并居中裁剪，直接绘制整个窗口区域
            p.drawPixmap(self.rect(), self._pixmap)
        else:
            # 图片缺失时给个底色，不崩溃
            p.fillRect(self.rect(), QColor(dark_paint_hex("#FFFFFF")))
        p.end()

    def _fade_and_close(self):
        """播放淡出动画后自我删除；动画期间忽略重复触发。"""
        if self._fading:
            return
        self._fading = True
        anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        anim.setDuration(self._FADE_MS)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.InOutQuad)
        anim.finished.connect(self.deleteLater)
        self._fade_anim = anim
        anim.start()

    def mousePressEvent(self, event):
        """点击开屏画面立即开始淡出，不阻塞用户"""
        self._fade_and_close()

class MediaButton(QPushButton):
    """扁平化媒体控制按钮，使用 QPainter 绘制标准 SVG 风格图标，加载时显示旋转动画"""
    ICON_PLAY = 0
    ICON_PAUSE = 1
    ICON_PREV = 2
    ICON_NEXT = 3
    ICON_REPEAT_ALL = 4
    ICON_REPEAT_ONE = 5
    ICON_SHUFFLE = 6

    def __init__(self, icon_type=ICON_PLAY, parent=None):
        super().__init__("", parent)
        self._icon_type = icon_type
        self._hovered = False
        self._loading = False
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._svg_cache = {}  # (svg名, 颜色) -> QPixmap，hover/常驻双色缓存

    def set_icon_type(self, icon_type):
        """切换图标类型（PLAY / PAUSE / PREV / NEXT）"""
        if self._icon_type != icon_type:
            self._icon_type = icon_type
            self.update()

    @property
    def is_playing(self):
        """当前是否处于播放状态"""
        return self._icon_type == self.ICON_PAUSE

    def set_loading(self, loading):
        self._loading = loading
        if loading:
            self._angle = 0
            self._timer.start(30)
        else:
            self._timer.stop()
        self.update()

    def _tick(self):
        self._angle = (self._angle + 6) % 360
        self.update()

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event):
        if self._loading:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            # 圆角背景
            path = QPainterPath()
            path.addRoundedRect(QRectF(self.rect()), 20, 20)
            p.setClipPath(path)
            p.fillRect(self.rect(), QColor(dark_paint_hex("#F0F0F2")))
            # 轨迹圆
            cx, cy = self.width() // 2, self.height() // 2
            r = min(cx, cy) - 6
            p.setPen(QPen(QColor(dark_paint_hex("#DCDCDC")), 2, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(cx - r, cy - r, r * 2, r * 2, 0, 360 * 16)
            # 旋转红色弧
            p.setPen(QPen(QColor(_theme_color()), 3, Qt.SolidLine, Qt.RoundCap))
            p.drawArc(cx - r, cy - r, r * 2, r * 2, self._angle * 16, 270 * 16)
            p.end()
        else:
            super().paintEvent(event)
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            # hover 图标颜色跟随全局主题色（切换主题后自动生效）
            color = _theme_color() if self._hovered else dark_paint_hex("#666666")
            svg_name = self._icon_svg_name()
            if svg_name:
                pm = self._load_svg_pixmap(svg_name, color)
                if pm is not None:
                    pw = pm.width() / pm.devicePixelRatio()
                    ph = pm.height() / pm.devicePixelRatio()
                    p.drawPixmap(int((self.width() - pw) / 2),
                                 int((self.height() - ph) / 2), pm)
                    p.end()
                    return
            # 循环/随机图标或 SVG 缺失时回退到 QPainter 绘制
            self._draw_icon(p, QColor(color))
            p.end()

    def _icon_svg_name(self):
        """播放/暂停/上一首/下一首对应的 SVG 图标名（循环/随机返回 None）"""
        if self._icon_type == self.ICON_PLAY:
            return "play.svg"
        if self._icon_type == self.ICON_PAUSE:
            return "pause.svg"
        if self._icon_type == self.ICON_PREV:
            return "backward.svg"
        if self._icon_type == self.ICON_NEXT:
            return "forward.svg"
        return None

    def _load_svg_pixmap(self, svg_name, color_hex):
        """渲染 SVG 为指定颜色的 QPixmap（DPR 放大、抗锯齿、结果缓存）"""
        key = (svg_name, color_hex)
        if key in self._svg_cache:
            return self._svg_cache[key]
        path = icon_path(svg_name)
        if not os.path.exists(path):
            return None
        try:
            import re
            with open(path, 'r', encoding='utf-8') as f:
                svg_text = f.read()
            from PyQt5.QtSvg import QSvgRenderer
            size = max(8, int(min(self.width(), self.height()) * 0.6))
            dpr = self.devicePixelRatio() or 1.0
            colored = re.sub(r'fill:#[0-9a-fA-F]{3,8}',
                             f'fill:{color_hex}', svg_text)
            pm = QPixmap(max(1, int(round(size * dpr))),
                         max(1, int(round(size * dpr))))
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            # try/finally 确保 QPainter 必定 end()
            try:
                p.setRenderHint(QPainter.Antialiasing)
                p.setRenderHint(QPainter.SmoothPixmapTransform)
                renderer = QSvgRenderer(colored.encode('utf-8'))
                if renderer.isValid():
                    renderer.render(p, QRectF(0, 0, size, size))
            finally:
                p.end()
            self._svg_cache[key] = pm
            return pm
        except Exception:
            return None

    def _draw_icon(self, p, color):
        cx = self.width() / 2
        cy = self.height() / 2
        s = min(self.width(), self.height()) * 0.38

        if self._icon_type == self.ICON_PLAY:
            # 播放三角形（右侧箭头）
            path = QPainterPath()
            path.moveTo(cx - s * 0.35, cy - s * 0.48)
            path.lineTo(cx + s * 0.52, cy)
            path.lineTo(cx - s * 0.35, cy + s * 0.48)
            path.closeSubpath()
            p.fillPath(path, color)

        elif self._icon_type == self.ICON_PAUSE:
            bw = s * 0.24
            gap = s * 0.16
            p.fillRect(QRectF(cx - gap - bw, cy - s * 0.48, bw, s * 0.96), color)
            p.fillRect(QRectF(cx + gap, cy - s * 0.48, bw, s * 0.96), color)

        elif self._icon_type == self.ICON_PREV:
            bw = s * 0.12
            p.fillRect(QRectF(cx - s * 0.5, cy - s * 0.45, bw, s * 0.9), color)
            path = QPainterPath()
            path.moveTo(cx + s * 0.15, cy - s * 0.45)
            path.lineTo(cx - s * 0.38, cy)
            path.lineTo(cx + s * 0.15, cy + s * 0.45)
            path.closeSubpath()
            p.fillPath(path, color)

        elif self._icon_type == self.ICON_NEXT:
            bw = s * 0.12
            p.fillRect(QRectF(cx + s * 0.5 - bw, cy - s * 0.45, bw, s * 0.9), color)
            path = QPainterPath()
            path.moveTo(cx - s * 0.15, cy - s * 0.45)
            path.lineTo(cx + s * 0.38, cy)
            path.lineTo(cx - s * 0.15, cy + s * 0.45)
            path.closeSubpath()
            p.fillPath(path, color)

        elif self._icon_type == self.ICON_REPEAT_ALL:
            self._draw_repeat(p, cx, cy, s, color, False)

        elif self._icon_type == self.ICON_REPEAT_ONE:
            self._draw_repeat(p, cx, cy, s, color, True)

        elif self._icon_type == self.ICON_SHUFFLE:
            self._draw_shuffle(p, cx, cy, s, color)

    def _draw_repeat(self, p, cx, cy, s, color, show_one):
        """循环图标：∩ 形路径 + 箭头（show_one 时显示数字 1）"""
        pw = max(2.0, s * 0.15)
        s2 = s * 0.38
        p.setPen(QPen(color, pw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(Qt.NoBrush)
        # 左竖线
        p.drawLine(QPointF(cx - s2, cy - s2), QPointF(cx - s2, cy + s2))
        # 上半圆弧（∩形，从左侧经过上方到右侧）
        arc_rect = QRectF(cx - s2, cy - s2 * 2, s2 * 2, s2 * 2)
        p.drawArc(arc_rect, 180 * 16, -180 * 16)
        # 右竖线（较短，留出箭头位置）
        p.drawLine(QPointF(cx + s2, cy - s2), QPointF(cx + s2, cy + s2 * 0.2))
        # 向下的箭头
        aw = s * 0.15
        p.setBrush(color)
        p.setPen(Qt.NoPen)
        arrow = QPainterPath()
        arrow.moveTo(cx + s2, cy + s2 * 0.2 + aw * 0.4)
        arrow.lineTo(cx + s2 - aw * 0.5, cy + s2 * 0.2)
        arrow.lineTo(cx + s2 + aw * 0.5, cy + s2 * 0.2)
        arrow.closeSubpath()
        p.fillPath(arrow, color)
        # 单曲循环时在中心显示 "1"
        if show_one:
            f = QFont(self.font())
            f.setPixelSize(int(s * 0.52))
            f.setBold(True)
            p.setFont(f)
            p.setPen(color)
            p.setBrush(Qt.NoBrush)
            p.drawText(QRectF(cx - s * 0.3, cy - s * 0.3, s * 0.6, s * 0.6),
                       Qt.AlignCenter, "1")

    def _draw_shuffle(self, p, cx, cy, s, color):
        """随机图标：两条交叉的线段 + 箭头"""
        pw = max(2.0, s * 0.14)
        s2 = s * 0.4
        p.setPen(QPen(color, pw, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        # 第一条线：左下 → 右上
        p.drawLine(QPointF(cx - s2, cy + s2 * 0.2), QPointF(cx + s2 * 0.15, cy - s2))
        # 第二条线：左上 → 右下
        p.drawLine(QPointF(cx - s2 * 0.4, cy - s2 * 0.3), QPointF(cx + s2, cy + s2 * 0.5))
        # 两个箭头（填充三角形）
        aw = s * 0.12
        p.setBrush(color)
        p.setPen(Qt.NoPen)
        # 右上箭头
        a1 = QPainterPath()
        a1.moveTo(cx + s2 * 0.15, cy - s2)
        a1.lineTo(cx + s2 * 0.15 - aw, cy - s2 + aw)
        a1.lineTo(cx + s2 * 0.15, cy - s2 + aw * 0.5)
        a1.closeSubpath()
        p.fillPath(a1, color)
        # 右下箭头
        a2 = QPainterPath()
        a2.moveTo(cx + s2, cy + s2 * 0.5)
        a2.lineTo(cx + s2 - aw, cy + s2 * 0.5 - aw * 0.5)
        a2.lineTo(cx + s2 - aw * 0.5, cy + s2 * 0.5)
        a2.closeSubpath()
        p.fillPath(a2, color)

class TablePlayButton(QPushButton):
    """表格行内播放按钮：hover 圆形背景 + SVG 图标全部自绘居中，
    避免 stylesheet 与 icon 组合在部分平台下背景右侧被裁剪的问题"""
    def __init__(self, parent=None):
        super().__init__("", parent)
        self._hovered = False
        self._playing = False
        self._pm_gray = None
        self._pm_red = None
        self.setCursor(Qt.PointingHandCursor)

    def set_pixmaps(self, gray, red):
        self._pm_gray = gray
        self._pm_red = red
        self.update()

    def enterEvent(self, e):
        self._hovered = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # 按下 / hover 圆形背景（居中，完整圆形）
        if self.isDown():
            p.setBrush(QColor(dark_paint_hex("#CCCCCC")))
            p.setPen(Qt.NoPen)
        elif self._hovered:
            p.setBrush(QColor(dark_paint_hex("#E0E0E0")))
            p.setPen(Qt.NoPen)
        if self.isDown() or self._hovered:
            d = min(self.width(), self.height())
            p.drawEllipse(QRectF((self.width() - d) / 2.0,
                                 (self.height() - d) / 2.0, d, d))
        # SVG 图标（居中绘制，播放中的行保持红色）
        pm = self._pm_red if (self._hovered or self._playing) else self._pm_gray
        if pm is not None:
            pw = pm.width() / pm.devicePixelRatio()
            ph = pm.height() / pm.devicePixelRatio()
            p.drawPixmap(int((self.width() - pw) / 2),
                         int((self.height() - ph) / 2), pm)
        p.end()

class GenericThread(QThread):
    """在子线程执行阻塞操作，通过 done 信号返回结果"""
    done = pyqtSignal(object)

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
            self.done.emit(result)
        except Exception as e:
            self.done.emit(None)

class BackArrow(QPushButton):
    """纯图形返回箭头按钮，正确覆写虚方法"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self._hovered = False
        self.setFixedSize(18, 18)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet("background: transparent; border: none;")

    def enterEvent(self, e):
        self._hovered = True
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self.update()
        super().leaveEvent(e)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(_theme_color()) if self._hovered else QColor(dark_paint_hex("#BBBBBB"))
        p.setPen(QPen(c, 1.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        cx, cy = 7, 9
        p.drawLine(cx - 5, cy - 3, cx, cy + 3)
        p.drawLine(cx, cy + 3, cx + 5, cy - 3)
        p.end()

class ClickableSlider(QSlider):
    """点击进度条任意位置直接跳转，同时支持拖拽，带越界回弹动画"""
    def __init__(self, orientation, parent=None):
        super().__init__(orientation, parent)
        self._bouncing = False      # 回弹动画进行中标记
        self._bounce_anim = None    # 回弹动画对象引用

    def animateToValue(self, target_val, duration=120):
        """平滑动画到目标值（用于越界回弹）"""
        if self._bounce_anim and self._bounce_anim.state() == QAbstractAnimation.Running:
            self._bounce_anim.stop()
        self._bounce_anim = QPropertyAnimation(self, b"value")
        self._bounce_anim.setDuration(duration)
        self._bounce_anim.setStartValue(self.value())
        self._bounce_anim.setEndValue(target_val)
        self._bounce_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._bounce_anim.start()
        return self._bounce_anim

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # 根据点击位置计算值
            ratio = event.x() / self.width()
            ratio = max(0.0, min(1.0, ratio))
            new_val = int(self.minimum() + (self.maximum() - self.minimum()) * ratio)
            self.setValue(new_val)
            # 手动设置为按下状态，这样 update_progress 不会干扰
            self.setSliderDown(True)
            self.sliderPressed.emit()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if event.buttons() & Qt.LeftButton and self.isSliderDown():
            ratio = event.x() / self.width()
            ratio = max(0.0, min(1.0, ratio))
            new_val = int(self.minimum() + (self.maximum() - self.minimum()) * ratio)
            self.setValue(new_val)
            self.sliderMoved.emit(new_val)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.isSliderDown():
            self.setSliderDown(False)
            self.sliderReleased.emit()
            return
        super().mouseReleaseEvent(event)

class _PlaylistListWidget(QListWidget):
    """支持拖拽排序和点击播放的播放列表

    交互约定：
    - 按下后直接松开（未超过拖拽阈值）→ 视为点击，播放该歌曲
    - 按下后拖动超过阈值 → 进入拖拽排序；若拖到列表外部被取消，列表自动还原
    - 拖放位置统一映射为"目标歌曲的上半部分"（即插到目标歌曲之前）。
      视觉反馈自绘：目标行淡红高亮 + 行上方红色指示线；被拖歌曲原位置的
      "下一首"为非法放置区（红色禁止底色、无指示线、光标禁止）
    - 点击行内按钮（✕）只触发按钮逻辑，不触发行点击播放
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._player_ref = None
        self._press_pos = None       # 按下位置（用于区分点击与拖拽）
        self._press_item = None      # 按下时的列表项
        self._was_drag = False       # 是否已进入拖拽
        self._drag_start_row = None  # 拖拽开始时被拖歌曲的行号
        # ---- 拖拽视觉反馈状态（自绘） ----
        self._drag_active = False        # 拖拽进行中
        self._drag_target_row = None     # 当前目标行（None = 列表末尾）
        self._drag_target_valid = False  # 目标是否合法（非法区 = False）
        self._drop_indicator_y = None    # 插入指示线的 y 坐标
        self._drag_pos = None            # 拖拽中的鼠标位置（用于边缘自动滚动）
        self._scroll_dir = 0             # 自动滚动方向
        self._scroll_speed = 0           # 自动滚动速度（随距边缘距离动态调整）
        self._drop_scroll_pos = None     # drop 前记录的滚动位置（重建列表后恢复，避免回弹）
        self._auto_scroll_timer = QTimer(self)
        self._auto_scroll_timer.setInterval(40)
        self._auto_scroll_timer.timeout.connect(self._do_auto_scroll)
        # 关闭 Qt 默认的细线指示器，改用自绘的醒目指示线
        self.setDropIndicatorShown(False)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_pos = event.pos()
            self._press_item = self.itemAt(event.pos())
            self._was_drag = False
            # 点击在按钮（✕）上时不视为行点击，避免松手时误播放
            if self._press_item is not None:
                widget = self.itemWidget(self._press_item)
                if widget:
                    local_pos = widget.mapFrom(self.viewport(), event.pos())
                    child = widget.childAt(local_pos)
                    if child and isinstance(child, QPushButton):
                        self._press_item = None
        # 始终交给基类处理，确保 InternalMove 拖拽能正常启动
        super().mousePressEvent(event)

    def startDrag(self, actions):
        """进入拖拽：记录被拖歌曲原位置，使用自绘半透明卡片作为拖拽快照"""
        self._was_drag = True
        self._drop_scroll_pos = None  # 新拖拽开始，清空上次 drop 的滚动快照
        sel = self.selectedIndexes()
        self._drag_start_row = sel[0].row() if sel else self.currentRow()
        if not sel or self._drag_start_row is None or self._drag_start_row < 0:
            super().startDrag(actions)
            return
        self._drag_active = True
        self._drag_pos = None
        item = self.item(self._drag_start_row)
        drag = QDrag(self)
        if item is not None:
            text = self._get_item_display_text(self._drag_start_row)
            pm = self._make_drag_pixmap(text)
            drag.setPixmap(pm)
            drag.setHotSpot(QPoint(pm.width() // 2, int(min(10, pm.height() // 2))))
        drag.setMimeData(self.model().mimeData(sel))
        drag.exec_(actions, self.defaultDropAction())
        # 拖拽结束（无论成功/取消/拒绝），清理视觉状态
        self._reset_drag_feedback()

    def _get_item_display_text(self, row):
        """获取行显示的歌曲文本。
        列表 item 本身不设文本（文本只存在于行 widget 的 QLabel 中），
        需从 widget 提取，供拖拽快照显示。"""
        item = self.item(row)
        if item is None:
            return ""
        widget = self.itemWidget(item)
        if widget is not None:
            for lb in widget.findChildren(QLabel):
                t = lb.text()
                if t:
                    return t
        return item.text() or ""

    def _make_drag_pixmap(self, text):
        """生成跟随鼠标的歌曲信息文本框快照：
        半透明浅底圆角小块 + 深色文字，宽度随文字自适应并限制最大宽度。
        物理分辨率按设备像素比(DPR)放大，高分屏下文字依旧锐利清晰。"""
        scale = getattr(self._player_ref, 'scale', 1) if self._player_ref else 1
        dpr = self.devicePixelRatio() or 1.0
        font = QFont(self.font())
        fm = QFontMetrics(font)
        pad_x = int(10 * scale)
        pad_y = int(5 * scale)
        radius = int(4 * scale)
        max_w = int(260 * scale)
        text_w = fm.horizontalAdvance(text)
        w = text_w + pad_x * 2
        if w > max_w:
            w = max_w
            text = fm.elidedText(text, Qt.ElideRight, w - pad_x * 2)
        h = fm.height() + pad_y * 2
        # 用 逻辑尺寸 × DPR 创建物理像素，再标定 DPR：
        # QDrag 显示时按逻辑尺寸呈现，但绘制分辨率已提升，避免放大模糊
        pm = QPixmap(int(w * dpr), int(h * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)  # 文字抗锯齿，边缘更锐利
        p.setOpacity(0.85)
        p.setPen(QPen(QColor(0, 0, 0, 45), 1))
        p.setBrush(QColor(255, 255, 255))
        p.drawRoundedRect(QRectF(0.5, 0.5, w - 1, h - 1), radius, radius)
        p.setOpacity(1.0)
        p.setPen(QColor(26, 26, 26))
        p.setFont(font)
        p.drawText(QRect(pad_x, 0, w - pad_x * 2, h), Qt.AlignVCenter | Qt.AlignLeft, text)
        p.end()
        return pm

    def _is_tail_drop(self, pos):
        """是否视为"拖到列表末尾"：
        1) 落在最后一行下方空白（indexAt 无效）
        2) 或落在最后一行 rect 的下半部分
        （最后一行下半部代表"放到该行之后"，使歌曲可拖到最后一首）"""
        idx = self.indexAt(pos)
        if not idx.isValid():
            return True
        if idx.row() == self.count() - 1:
            r = self.visualItemRect(self.item(idx.row()))
            return pos.y() > r.center().y()
        return False

    def _is_forbidden_drop_target(self, pos):
        """被拖歌曲原位置的"下一首"为非法放置区：
        拖到那里会被映射为插到其之前（= 被拖歌曲原位，顺序不变），
        而 Qt 的内部移动会破坏该行 widget 显示，故直接拒绝。
        末尾放置（最后一行下半部/末尾空白）是合法移动，不在禁止之列；
        仅当被拖歌曲本就在末尾且又拖回末尾（= 原位放回）时拒绝。"""
        if self._drag_start_row is None:
            return False
        # 末尾放置：除"原位放回"外一律合法
        if self._is_tail_drop(pos):
            return self._drag_start_row == self.count() - 1
        idx = self.indexAt(pos)
        return idx.isValid() and idx.row() == self._drag_start_row + 1

    def _update_drag_feedback(self, pos):
        """根据鼠标位置刷新目标行与指示线位置（自绘用）"""
        if self._is_tail_drop(pos):
            # 拖到末尾 → 指示线画在最后一行下方（表示插入到末尾）
            self._drag_target_row = None
            if self.count() > 0:
                self._drop_indicator_y = self.visualItemRect(self.item(self.count() - 1)).bottom()
            else:
                self._drop_indicator_y = 2
        else:
            idx = self.indexAt(pos)
            self._drag_target_row = idx.row()
            self._drop_indicator_y = self.visualItemRect(self.item(idx.row())).top()
        self._drag_target_valid = not self._is_forbidden_drop_target(pos)
        self.viewport().update()

    def _reset_drag_feedback(self):
        self._drag_active = False
        self._drag_target_row = None
        self._drag_target_valid = False
        self._drop_indicator_y = None
        self._drag_pos = None
        self._scroll_dir = 0
        self._auto_scroll_timer.stop()
        self.viewport().update()

    def _update_auto_scroll(self):
        """拖拽接近列表顶部/底部边缘时启动定时滚动，使长列表也能拖到位。
        滚动速度随指针距边缘的距离动态调整：越贴近边缘滚得越快。
        返回边缘区域外则停止滚动。"""
        if self._drag_pos is None:
            return
        vp = self.viewport()
        y = self._drag_pos.y()
        margin = 40
        scale = getattr(self._player_ref, 'scale', 1) if self._player_ref else 1
        if y < margin:
            # 顶部边缘：距离越近（dist 越大）速度越快
            dist = max(0, margin - y)
            self._scroll_dir = -1
            self._scroll_speed = int((4 + (dist / margin) * 36) * scale)
            if not self._auto_scroll_timer.isActive():
                self._auto_scroll_timer.start()
        elif y > vp.height() - margin:
            # 底部边缘
            dist = max(0, y - (vp.height() - margin))
            self._scroll_dir = 1
            self._scroll_speed = int((4 + (dist / margin) * 36) * scale)
            if not self._auto_scroll_timer.isActive():
                self._auto_scroll_timer.start()
        else:
            self._auto_scroll_timer.stop()

    def _do_auto_scroll(self):
        sb = self.verticalScrollBar()
        sb.setValue(sb.value() + self._scroll_dir * self._scroll_speed)
        # 滚动后立即重算目标行与指示线位置，保证视觉反馈与滚动同步
        if self._drag_pos is not None:
            self._update_drag_feedback(self._drag_pos)

    def dragEnterEvent(self, event):
        # 内部拖拽：声明为移动语义（MoveAction），
        # 否则 Qt 会按复制语义显示"箭头+加号"光标
        if event.source() is self:
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        # 仅处理列表自身的内部拖拽；完全自绘视觉，不依赖 Qt 默认指示器
        if event.source() is self:
            self._drag_active = True
            self._drag_pos = event.pos()
            self._update_drag_feedback(event.pos())
            self._update_auto_scroll()
            if self._drag_target_valid:
                event.setDropAction(Qt.MoveAction)  # 合法区：纯移动光标（无加号）
                event.accept()
            else:
                event.ignore()  # 非法区：光标变为禁止
            return
        super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        # 拖出列表（拖拽可能被取消）时立即清除高亮与指示线
        self._reset_drag_feedback()
        super().dragLeaveEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._drag_active:
            return
        p = QPainter(self.viewport())
        p.setRenderHint(QPainter.Antialiasing)
        accent = QColor(*_theme_rgb())
        # 目标行高亮（合法浅红 / 非法深红禁止底色）
        if self._drag_target_row is not None and 0 <= self._drag_target_row < self.count():
            r = self.visualItemRect(self.item(self._drag_target_row))
            if self._drag_target_valid:
                p.fillRect(r, QColor(*_theme_rgb(), 26))
            else:
                p.fillRect(r, QColor(*_theme_rgb(), 60))
        # 插入指示线（目标行上方 2px 红粗线 + 左侧圆点）
        if self._drop_indicator_y is not None and self._drag_target_valid:
            vw = self.viewport().width()
            y = self._drop_indicator_y
            p.setPen(QPen(accent, 2))
            p.drawLine(8, y, vw - 8, y)
            p.setPen(Qt.NoPen)
            p.setBrush(accent)
            p.drawEllipse(QPoint(12, y), 4, 4)
        p.end()

    def mouseReleaseEvent(self, event):
        click_item = None
        if (event.button() == Qt.LeftButton and not self._was_drag
                and self._press_item is not None and self._press_pos is not None):
            # 未发生拖拽且松手位置仍在同一列表项上 → 视为点击播放
            if (event.pos() - self._press_pos).manhattanLength() <= QApplication.startDragDistance():
                item = self.itemAt(event.pos())
                if item is self._press_item:
                    click_item = item
        self._press_pos = None
        self._press_item = None
        super().mouseReleaseEvent(event)
        if click_item is not None and self._player_ref:
            self._player_ref._on_playlist_item_clicked(click_item)

    def dropEvent(self, event):
        # 仅接受列表自身的内部移动；拖到列表外部时 Qt 会取消拖拽（不进入这里）
        if event.source() is not self:
            event.ignore()
            return
        # 非法放置区（被拖歌曲原位置的下一首）：拒绝放置
        if self._is_forbidden_drop_target(event.pos()):
            event.ignore()
            return
        # 快照拖拽前的显示顺序（UserRole 序列），供 _sync_playlist_order 判断
        # 拖拽是否真的改变了顺序（显示顺序 ≠ 队列顺序，不能直接比较队列）
        self._pre_drag_order = [self.item(i).data(Qt.UserRole)
                                for i in range(self.count())]
        start_row = self._drag_start_row if self._drag_start_row is not None else self.currentRow()
        if start_row is None or start_row < 0 or start_row >= self.count():
            event.ignore()
            return
        # 目标位置：拖到某首歌上 → 统一映射为其上半部分（插到该歌之前）；
        # 拖到末尾（最后一行下半部分 / 末尾空白）→ 插入到列表末尾
        if self._is_tail_drop(event.pos()):
            insert_row = self.count()
        else:
            insert_row = self.indexAt(event.pos()).row()
        # 移动 item 前快照当前滚动位置：takeItem/insertItem 会使 Qt 临时
        # 调整滚动条值，若此后重建列表仍以"当前值"为准，自动滚动效果会
        # 丢失（滚动条回弹）。此处快照用于重建后强制恢复。
        self._drop_scroll_pos = self.verticalScrollBar().value()
        # 手动执行内部移动（不交给 Qt 的 InternalMove，从根源上避免
        # 其移动 setItemWidget 行导致的空白显示问题）
        item = self.takeItem(start_row)
        if insert_row > start_row:
            insert_row -= 1
        self.insertItem(insert_row, item)
        event.accept()
        self._reset_drag_feedback()
        if self._player_ref:
            self._player_ref._sync_playlist_order()


class ColorSwatch(QPushButton):
    """主题色预设色块（自绘圆形）。

    设计要点：所有色块采用同一 widget 固定尺寸，选中态仅在自绘时缩小
    圆半径（圆心始终位于 widget 几何中心），保证同行色块在竖直方向上
    严格对齐——避免 QPushButton 在 Windows 默认样式下用 QSS `border-radius`
    加 `border: none` 时圆角背景填充不生效（呈矩形），以及选中态缩 widget
    尺寸时 QHBoxLayout 对 fixed-size 子项竖直居中精度差的问题。
    """
    def __init__(self, color, scale=1.0, parent=None):
        super().__init__(parent)
        self._color = color
        self._scale = scale
        self._selected = False
        self._hovered = False
        self.setFixedSize(int(28 * scale), int(28 * scale))
        self.setCursor(Qt.PointingHandCursor)
        self.setAttribute(Qt.WA_Hover, True)

    def set_selected(self, selected):
        if self._selected != bool(selected):
            self._selected = bool(selected)
            self.update()

    def set_color(self, color):
        self._color = color
        self.update()

    def enterEvent(self, event):
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hovered = False
        self.update()
        super().enterEvent(event)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        cx = self.width() / 2.0
        cy = self.height() / 2.0
        side = min(self.width(), self.height())
        # 未选中：填满圆；选中：圆心不动，半径内缩一圈（视觉上"内缩"高亮）
        r = (side / 2.0 - 3 * self._scale) if self._selected else (side / 2.0)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(self._color))
        p.drawEllipse(QPointF(cx, cy), r, r)
        if self._hovered and not self._selected:
            p.setBrush(Qt.NoBrush)
            p.setPen(QPen(QColor(dark_paint_hex("#999999")), max(1, 1.5 * self._scale)))
            p.drawEllipse(QPointF(cx, cy), r, r)
        p.end()


# ---------- 可设置背景图（cover 等比裁剪）的列表 / 表格 ----------
class _BgImageMixin:
    """底色 + cover 等比裁剪背景图的绘制混入。

    设计要点：
    - 使用方式：控件 QSS 背景设为 transparent，paintEvent 中先自绘
      底色 + 背景图，再调用父类 paintEvent，使条目/文字叠在图片之上；
    - cover 模式：等比缩放至完全铺满目标区域后居中裁剪，绝不拉伸变形；
    - 按目标尺寸缓存缩放结果，避免滚动/hover 高频重绘时反复缩放大图；
    - 底色经 dark_paint_hex 映射，深浅模式下与原 QSS 底色保持一致。
    """

    def _init_bg_image(self, base_color):
        self._bg_base = base_color          # 底色（透明度低时露出）
        self._bg_pixmap = QPixmap()         # 原图（空 = 未设置背景图）
        self._bg_scaled = None              # cover 裁剪结果缓存
        self._bg_scaled_size = None         # 缓存对应的目标尺寸
        self._bg_opacity = 1.0              # 图片不透明度 0.0~1.0
        # 滚动类控件（有 viewport）：底色/背景图在 viewport 自己的绘制周期
        # 开头绘制（见 eventFilter）；BgWidget 无 viewport，走自身 paintEvent
        if hasattr(self, "viewport"):
            self.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        # 注意：底色+背景图必须画在 viewport 自身的 paint 周期内。若在父控件
        # 的 paintEvent 里画到 viewport 上，离屏 grab() 有效，但真实渲染时
        # viewport 独立重绘、拿不到那层内容（表现为默认白底）。事件过滤器在
        # 原生绘制之前执行，条目/文字随后叠加，顺序有保证。
        if obj is self.viewport() and event.type() == QEvent.Paint:
            self._paint_bg(obj)
        return super().eventFilter(obj, event)

    def set_bg_pixmap(self, pixmap):
        """设置/更换背景图（传空 QPixmap 表示移除）"""
        self._bg_pixmap = QPixmap(pixmap)
        self._bg_scaled = None
        self.update()

    def set_bg_opacity(self, opacity):
        """设置背景图不透明度（0.0~1.0，超出范围自动截断）"""
        self._bg_opacity = max(0.0, min(1.0, float(opacity)))
        self.update()

    def _bg_cover_pixmap(self, w, h):
        """返回按 (w, h) cover 裁剪后的缓存图（无图时返回 None）。

        高分屏适配：缩放目标是物理像素（逻辑尺寸 × devicePixelRatio），并把
        结果 DPR 设回控件值——否则 1x 图被二次拉伸到物理分辨率导致发糊。
        缓存键含 DPR，跨屏拖动（DPR 变化）时自动重建。"""
        if self._bg_pixmap.isNull() or w <= 0 or h <= 0:
            return None
        dpr = self.devicePixelRatioF() or 1.0
        key = (w, h, dpr)
        if self._bg_scaled is None or self._bg_scaled_size != key:
            # 取较大缩放比才能完全铺满（KeepAspectRatioByExpanding 语义），
            # 多出部分由目标区域自然裁掉，实现"适应裁剪、不拉伸"
            s = max(w / self._bg_pixmap.width(), h / self._bg_pixmap.height())
            tw = max(1, round(self._bg_pixmap.width() * s * dpr))
            th = max(1, round(self._bg_pixmap.height() * s * dpr))
            self._bg_scaled = self._bg_pixmap.scaled(
                tw, th, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            self._bg_scaled.setDevicePixelRatio(dpr)
            self._bg_scaled_size = key
        return self._bg_scaled

    def _paint_bg(self, target):
        """在 target（通常是 viewport）上绘制底色与背景图"""
        p = QPainter(target)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(target.rect(), QColor(dark_paint_hex(self._bg_base)))
        dpr = target.devicePixelRatioF() or 1.0
        pm = self._bg_cover_pixmap(target.width(), target.height())
        if pm is not None and self._bg_opacity > 0:
            p.setOpacity(self._bg_opacity)
            # drawPixmap 按逻辑坐标绘制；PM 自带 DPR，实际输出为物理分辨率
            lw = pm.width() / dpr
            lh = pm.height() / dpr
            p.drawPixmap(round((target.width() - lw) / 2),
                         round((target.height() - lh) / 2), pm)
        p.end()


class BgTableWidget(_BgImageMixin, QTableWidget):
    """带 cover 背景图的表格（配套 QSS 背景需为 transparent）"""
    def __init__(self, base_color, parent=None):
        QTableWidget.__init__(self, parent)
        self._init_bg_image(base_color)


class BgScrollArea(_BgImageMixin, QScrollArea):
    """带 cover 背景图的滚动区（背景固定不随内容滚动；内层 widget 背景需为 transparent）"""
    def __init__(self, base_color, parent=None):
        QScrollArea.__init__(self, parent)
        self._init_bg_image(base_color)


class BgWidget(_BgImageMixin, QWidget):
    """带 cover 背景图的普通容器（自身 paintEvent 直接绘制，无 viewport）"""
    def __init__(self, base_color, parent=None):
        QWidget.__init__(self, parent)
        self._init_bg_image(base_color)
        # 关键：普通 QWidget 的 QSS 背景（如 transparent）需 WA_StyledBackground
        # 才参与绘制；这里由 _paint_bg 自绘底色，需关掉 styled background，
        # 否则 QSS 底色会盖在 paintEvent 内容之上
        self.setAttribute(Qt.WA_StyledBackground, False)

    def paintEvent(self, event):
        self._paint_bg(self)
        super().paintEvent(event)

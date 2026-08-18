"""吉祥物（看板娘）模块：说话气泡、控制按钮、控制面板与独立悬浮窗口。"""
import os
import re
import random

from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *

from core.utils import reg_theme, resource_path, mascot_dir

class _MascotBubble(QWidget):
    """看板娘说话气泡：圆角白底 + 底部小三角。
    容量较大（多行显示），超长时最后一行省略号截断。"""
    MAX_W = 400      # 最大气泡宽度（逻辑像素）
    MAX_LINES = 6    # 最多显示行数，超出省略
    PAD_X = 20       # 水平内边距
    PAD_Y = 12       # 垂直内边距
    TRI_H = 8        # 底部三角高度

    def __init__(self, parent=None, scale=1.0):
        super().__init__(parent)
        self._text = ""
        self._lines = []
        self._scale = scale
        # 气泡字体（比主窗口正文略大，看板娘说话更清晰）
        f = QApplication.font()
        f.setPixelSize(int(16 * scale))
        self.setFont(f)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def set_text(self, text, max_w=None):
        self._text = text
        fm = self.fontMetrics()
        limit = int(self.MAX_W * self._scale) if max_w is None else max_w
        line_h = fm.height()
        content_w = max(int(30 * self._scale),
                        limit - int(self.PAD_X * self._scale) * 2)
        # 按宽度换行；超出最大行数时，最后一行始终以省略号结尾
        lines = self._wrap_text(text, fm, content_w)
        if len(lines) > self.MAX_LINES:
            last_full = lines[self.MAX_LINES - 1]  # 取原第 MAX_LINES 行
            lines = lines[:self.MAX_LINES - 1]
            # 手动截断并补省略号（elidedText 在此环境可能不带省略号）
            s = last_full
            while s and fm.horizontalAdvance(s + "…") > content_w:
                s = s[:-1]
            lines.append((s + "…") if s else "…")
        self._lines = lines
        max_line_w = max((fm.horizontalAdvance(l) for l in self._lines), default=0)
        w = min(max_line_w + int(self.PAD_X * self._scale) * 2, limit)
        h = len(self._lines) * line_h + int(self.PAD_Y * self._scale) * 2 \
            + int(self.TRI_H * self._scale)
        self.setFixedSize(max(w, int(40 * self._scale)),
                          max(h, int(28 * self._scale)))
        self.update()

    def text(self):
        return self._text

    # 不得出现在行首的标点符号（含中英文后引号、后括号、后书名号等）。
    # 注意：前书名号《 〈 应允许在行首、禁止在行尾，故不在此集合中。
    _LINE_START_PUNCT = set("，。！？；：、,.!?;:'\"」』）】)]}>》〉~～")

    @staticmethod
    def _tokenize(text):
        """将文本切分为换行单元：英文单词/数字整体、CJK 逐字、标点符号单字、空格为分隔。
        返回 (tokens, is_space) 列表，is_space 标记该 token 是空格分隔符。"""
        tokens = []
        buf = ""
        for ch in text:
            if ch.isspace():
                if buf:
                    tokens.append((buf, False))
                    buf = ""
                tokens.append((" ", True))
            elif ch.isascii() and (ch.isalnum() or ch == "'"):
                # ASCII 字母/数字/缩写撇号累积为单词单元（不在中间截断）
                buf += ch
            else:
                if buf:
                    tokens.append((buf, False))
                    buf = ""
                tokens.append((ch, False))
        if buf:
            tokens.append((buf, False))
        # 去掉首尾多余空格分隔符，连续空格合并
        merged = []
        for tok, sp in tokens:
            if sp:
                if merged and not merged[-1][1]:
                    merged.append((" ", True))
            else:
                merged.append((tok, False))
        if merged and merged[0][1]:
            merged.pop(0)
        if merged and merged[-1][1]:
            merged.pop()
        return merged

    @staticmethod
    def _wrap_text(text, fm, max_width):
        """智能换行：中英文混排、英文单词不截断、行首禁标点、标点紧贴文字无多余空格。"""
        tokens = _MascotBubble._tokenize(text)
        lines = []
        cur = ""
        cur_w = 0
        for tok, is_space in tokens:
            if is_space:
                # 空格是词边界：作为可断点，但仅在行非空时附加
                if cur:
                    cur += " "
                    cur_w = fm.horizontalAdvance(cur)
                continue
            tok_w = fm.horizontalAdvance(tok)
            # 中英文混排：左侧为 CJK 字符、右侧为英文单词时自动补一个词间空格
            prev_char = cur[-1] if cur else ""
            need_space = bool(prev_char and not prev_char.isspace()
                              and not prev_char.isascii()
                              and tok[:1].isascii() and tok[:1].isalnum())
            sep = " " if need_space else ""
            candidate = cur + sep + tok
            cand_w = fm.horizontalAdvance(candidate)
            if cur and cand_w > max_width:
                # 当前行放不下，先 flush
                lines.append(cur)
                cur = tok
                cur_w = tok_w
            else:
                cur = candidate
                cur_w = cand_w
        if cur:
            lines.append(cur)

        # 二次修正：行首若为禁行首标点，则强制前移到上一行（后引号/后括号必须紧贴前文，
        # 即便上一行略微超宽也不保留在行首；气泡整体宽度会随之自适应）
        fixed = []
        for i, ln in enumerate(lines):
            if ln and ln[0] in _MascotBubble._LINE_START_PUNCT and fixed:
                fixed[-1] = fixed[-1] + ln[0]
                tail = ln[1:]
                if tail:
                    fixed.append(tail)
                continue
            fixed.append(ln)
        lines = [ln for ln in fixed if ln != ""]
        return lines or [""]

    def paintEvent(self, e):
        if not self._text:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        r = 10.0
        body_h = max(1, rect.height() - int(self.TRI_H * self._scale) - 1)
        top, left, right = rect.y(), rect.x(), rect.x() + rect.width()
        bottom = top + body_h
        # 底部小三角（指向下方看板娘）
        tri_w = int(10 * self._scale)
        cx = rect.center().x()
        tip_y = bottom + int(self.TRI_H * self._scale)
        # 单一连续路径：圆角矩形（底部中央留缺口）+ 三角，避免接缝处出现双重描边
        path = QPainterPath()
        path.moveTo(left + r, top)
        path.lineTo(right - r, top)
        path.quadTo(right, top, right, top + r)
        path.lineTo(right, bottom - r)
        path.quadTo(right, bottom, right - r, bottom)
        # 右下角到三角右点
        path.lineTo(cx + tri_w, bottom)
        path.lineTo(cx, tip_y)
        path.lineTo(cx - tri_w, bottom)
        # 三角左点到左下角
        path.lineTo(left + r, bottom)
        path.quadTo(left, bottom, left, bottom - r)
        path.lineTo(left, top + r)
        path.quadTo(left, top, left + r, top)
        p.fillPath(path, QColor("#FFFFFF"))
        p.setPen(QPen(QColor("#E0E0E0"), 1))
        p.drawPath(path)
        # 多行文本（完整显示，不省略）
        fm = self.fontMetrics()
        p.setPen(QColor("#333333"))
        line_h = fm.height()
        y = int(self.PAD_Y * self._scale)
        for line in self._lines:
            p.drawText(QRectF(0, y, self.width(), line_h), Qt.AlignCenter, line)
            y += line_h
        p.end()

class _CtlButton(QPushButton):
    """小组件按钮：禁用态画一个很淡的圆形占位；图标始终居中绘制。无 hover 效果。"""
    def __init__(self, icon, icon_w, icon_h, scale, disabled=False):
        super().__init__(None)
        self._icon = icon
        self._icon_w = icon_w
        self._icon_h = icon_h
        self._scale = scale
        self._disabled = disabled
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setCursor(Qt.ForbiddenCursor if disabled else Qt.PointingHandCursor)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = self.width() / 2.0, self.height() / 2.0
        r = min(self.width(), self.height()) / 2.0 - 2 * self._scale
        if not self.isEnabled():
            # 禁用态：画一个很淡的圆作为置灰占位
            p.setBrush(QColor(255, 255, 255, 12))
            p.setPen(Qt.NoPen)
            p.drawEllipse(QPointF(cx, cy), r, r)
        if self._icon is not None:
            mode = QIcon.Disabled if not self.isEnabled() else QIcon.Normal
            self._icon.paint(
                p,
                int(cx - self._icon_w / 2), int(cy - self._icon_h / 2),
                self._icon_w, self._icon_h, Qt.AlignCenter, mode)
        p.end()

class _MascotControls(QWidget):
    """看板娘控制小组件：固定显示在看板娘下方的一排操作按钮。

    - 位置固定在看板娘窗口正下方（由 MascotWindow 定时同步，保持相对位置不变）；
    - 显示/隐藏与看板娘同步（showEvent/hideEvent 联动）；
    - 无浮动效果；
    - 全部外观由资源包 controls/settings.json 驱动：gap（与看板娘间距）、
      spacing（按钮间距）、icon_size（图标尺寸，可只填宽度）、color（矢量图着色）、
      background（无底图时的圆角矩形底色，含透明度）、bg_image（可选底图，宽度自适应）；
      按钮由 buttons 数组配置，动作类型通过 _ACTIONS 分发表注册。
    - 图标支持常见位图（png/jpg/...）与矢量图（svg）；矢量图才会被 color 着色，
      位图原样显示。
    """
    # 位图扩展名：命中则视为位图（不替换 fill 颜色）；其余按矢量图处理
    _RASTER_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp")

    # 动作分发表：type -> handler(controls)。扩展新按钮类型在此注册即可。
    _ACTIONS = {
        "play_pause": lambda c: c._player.toggle_play() if c._player else None,
        "prev":       lambda c: c._player.prev_song() if c._player else None,
        "next":       lambda c: c._player.next_song() if c._player else None,
        "favorite":   lambda c: c._player._toggle_fav() if c._player else None,
    }

    @staticmethod
    def _parse_color(s):
        """解析 '#RRGGBB' / 'rgb(r,g,b)' / 'rgba(r,g,b,a)' 为 QColor"""
        s = (s or "").strip()
        m = re.match(r'rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)'
                     r'(?:\s*,\s*([\d.]+))?\s*\)', s)
        if m:
            r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
            a = float(m.group(4)) if m.group(4) is not None else 255
            return QColor(r, g, b, int(min(max(a, 0), 255)))
        c = QColor(s)
        return c if c.isValid() else QColor(20, 20, 20, 150)

    def __init__(self, mascot, player, pack):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._mascot = mascot
        self._player = player
        self._scale = mascot._scale
        self._pack_dir = pack['dir'] if pack else None
        cfg = ((pack['config'] or {}).get('controls') or {}) if pack else {}
        # 资源基准目录：controls/settings.json 所在目录，所有图标/底图相对它解析
        self._ctl_dir = cfg.get('_dir') or (os.path.join(self._pack_dir, "controls")
                                            if self._pack_dir else None)
        self._gap = int((cfg.get('gap', 6)) * self._scale)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setWindowTitle("看板娘控制")
        # 图标尺寸：支持只填宽度的字典（{"width":22} 或 {"width":22,"height":22}）
        sz_cfg = cfg.get('icon_size') or {}
        if isinstance(sz_cfg, dict):
            w = sz_cfg.get('width') or sz_cfg.get('w')
            h = sz_cfg.get('height') or sz_cfg.get('h') or w
        elif isinstance(sz_cfg, (int, float)):
            w = h = sz_cfg
        else:
            w = h = None
        self._icon_w = int((w or 22) * self._scale)
        self._icon_h = int((h or self._icon_w) * self._scale)
        # 外框（按钮）高度：frame_height 为配置的外框高度；按钮高度的
        # 最小合法值固定为 _MIN_FRAME_H，当配置值小于该值时自动钳到最小合法值
        self._MIN_FRAME_H = 28
        raw_h = cfg.get('frame_height')
        if isinstance(raw_h, (int, float)) and raw_h >= self._MIN_FRAME_H:
            frame_h = raw_h
        else:
            frame_h = self._MIN_FRAME_H
        self._frame_h = int(frame_h * self._scale)
        # 矢量图统一着色（仅矢量图生效；位图原样）
        self._color = cfg.get('color') or "#FFFFFF"
        # 收藏激活态红心色（覆盖 color）
        self._active_color = "#EC4141"
        # 底图：有则宽度自适应绘制；无则自动生成圆角矩形（background 含透明度）
        self._bg_image = self._load_bg_image(cfg.get('bg_image'))
        self._brush = self._parse_color(cfg.get('background', "rgba(20,20,20,150)"))
        self._radius = int(8 * self._scale)
        self._last_playing = None
        self._last_fav = None
        self._buttons = []   # (button, type, icon_normal, icon_active)
        self._build(cfg.get('buttons') or [])
        # 预先定位到看板娘下方，避免 show() 时先出现在屏幕中央再被定时器
        # 挪走而产生的闪烁
        self.adjustSize()
        self._sync_position()

    def _resolve_asset(self, name):
        """把配置中的图标/底图路径解析为相对 controls/ 目录的完整路径。

        所有资源路径都相对 controls/settings.json 所在目录（self._ctl_dir），
        即 json 文件在哪一层，资源就放哪一层——配置里写 'bg.png'、'play.svg'
        这类相对路径即可，无需写 controls/ 前缀。返回完整路径，失败返回 None。
        """
        if not name or not self._ctl_dir:
            return None
        path = os.path.join(self._ctl_dir, name)
        return path if os.path.exists(path) else None

    def _load_bg_image(self, name):
        """加载可选底图；无或加载失败返回 None。

        返回 dict：{'kind':'raster'/'svg', 'pixmap'/'text':..., 'w','h'}。
        - 位图（png/jpg/...）直接以 QPixmap 存入，绘制时按目标尺寸平滑缩放；
        - 矢量图（svg 等）保存原始文本与 viewBox 宽高比，绘制时由 QSvgRenderer
          按需以目标尺寸（含高分屏 dpr）直接渲染，避免先渲染成小位图再放大
          而产生的像素点/模糊。
        """
        path = self._resolve_asset(name)
        if not path:
            return None
        ext = os.path.splitext(name)[1].lower()
        if ext in self._RASTER_EXTS:
            pm = QPixmap(path)
            if pm.isNull():
                return None
            return {"kind": "raster", "pixmap": pm,
                    "w": pm.width(), "h": pm.height()}
        # 矢量图：保留文本，绘制时按目标尺寸精确渲染
        try:
            from PyQt5.QtSvg import QSvgRenderer
            with open(path, 'r', encoding='utf-8') as f:
                text = f.read()
            r = QSvgRenderer(text.encode('utf-8'))
            if not r.isValid():
                return None
            vb = r.viewBoxF()
            w = vb.width() or 1.0
            h = vb.height() or 1.0
            return {"kind": "svg", "text": text, "w": w, "h": h}
        except Exception:
            return None

    def _build(self, buttons):
        lay = QHBoxLayout(self)
        pad = int(4 * self._scale)
        lay.setContentsMargins(pad, pad, pad, pad)
        lay.setSpacing(int((buttons and 6 or 2) * self._scale))
        # 按钮组在窗体内水平居中：使用自定义底图时窗体会被加宽（见下方
        # _apply_bg_width），居中可避免按钮偏到一侧、与居中的底图错开。
        lay.setAlignment(Qt.AlignCenter)
        for spec in buttons:
            typ = spec.get('type')
            if typ not in self._ACTIONS:
                print(f"⚠️ 未知看板娘控制按钮类型：{typ}")
                continue
            tooltip = spec.get('tooltip') or typ
            if typ == "favorite":
                # 收藏按钮保持 SVG 原始颜色（红心/灰心），不被 color 配置改变
                ic_n = self._load_icon(spec.get('icon'), self._color, colorize=False)
                ic_a = self._load_icon(spec.get('icon_alt'), self._color, colorize=False)
            else:
                ic_n = self._load_icon(spec.get('icon'), self._color)
                ic_a = self._load_icon(spec.get('icon_alt'), self._color)
            btn_w = int(max(self._icon_w, self._icon_h) + 8 * self._scale)
            if ic_n is None:
                # 普通态图标资源缺失：按钮置灰禁用，不响应点击
                print(f"⚠️ 看板娘控制按钮 '{typ}' 图标缺失，已置灰禁用：{spec.get('icon')}")
                btn = _CtlButton(None, self._icon_w, self._icon_h,
                                self._scale, disabled=True)
                btn.setEnabled(False)
                btn.setFixedSize(btn_w, self._frame_h)
                btn.setToolTip(tooltip)
                lay.addWidget(btn)
                # 仍记录以保留布局顺序，但标记为禁用（ic_n=None 即禁用态）
                self._buttons.append((btn, typ, None, None))
                continue
            btn = _CtlButton(ic_n, self._icon_w, self._icon_h, self._scale)
            btn.setFixedSize(btn_w, self._frame_h)
            btn.setToolTip(tooltip)
            btn.clicked.connect(lambda _=False, t=typ: self._run_action(t))
            lay.addWidget(btn)
            self._buttons.append((btn, typ, ic_n, ic_a))
        self.adjustSize()
        # 使用自定义底图时，按底图宽高比加宽窗体宽度，让底图左右两端完整
        # 显示（不再被控件边界裁切）。仅当底图所需宽度大于按钮所需宽度时才
        # 加宽，按钮本身格局（尺寸/间距/内边距/圆角）完全不变。
        if self._bg_image is not None:
            bg = self._bg_image
            ar = (bg['w'] / bg['h']) if bg['h'] else 1.0
            want_w = int(self.height() * ar)
            if want_w > self.width():
                self.setFixedWidth(want_w)

    def _load_icon(self, name, color, colorize=True):
        """读取相对 controls/ 目录的图标（位图或矢量图），渲染为 QIcon。

        - 矢量图（svg 等）：colorize=True 时替换 fill 颜色为 color 后渲染；
          colorize=False 保留 SVG 原始颜色（如收藏红心）；
        - 位图（png/jpg/...）：按 icon_size 缩放后直接使用，不受 color 影响。
        """
        path = self._resolve_asset(name)
        if not path:
            return None
        try:
            dpr = self.devicePixelRatio() or 1.0
            w = max(1, int(self._icon_w * dpr))
            h = max(1, int(self._icon_h * dpr))
            ext = os.path.splitext(name)[1].lower()
            if ext in self._RASTER_EXTS:
                pm = QPixmap(path)
                if pm.isNull():
                    return None
                pm = pm.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                pm.setDevicePixelRatio(dpr)
                return QIcon(pm)
            # 矢量图：替换 fill 颜色后渲染（按 viewBox 宽高比居中，避免拉伸变形）
            from PyQt5.QtSvg import QSvgRenderer
            with open(path, 'r', encoding='utf-8') as f:
                svg_text = f.read()
            if colorize:
                svg_text = re.sub(r'fill:\s*#[0-9a-fA-F]{3,8}',
                                  f'fill:{color}', svg_text)
            pm = QPixmap(w, h)
            pm.setDevicePixelRatio(dpr)
            pm.fill(Qt.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            renderer = QSvgRenderer(svg_text.encode('utf-8'))
            if renderer.isValid():
                vb = renderer.viewBoxF()
                if vb.width() > 0 and vb.height() > 0:
                    # 在 icon_w×icon_h 画布内按比例缩放并居中（保持原图宽高比）
                    s = min(self._icon_w / vb.width(),
                            self._icon_h / vb.height())
                    dw = vb.width() * s
                    dh = vb.height() * s
                    dx = (self._icon_w - dw) / 2
                    dy = (self._icon_h - dh) / 2
                    renderer.render(p, QRectF(dx, dy, dw, dh))
            p.end()
            return QIcon(pm)
        except Exception:
            return None

    def _run_action(self, typ):
        handler = self._ACTIONS.get(typ)
        if handler:
            handler(self)

    def _sync_position(self):
        """定位在看板娘窗口正下方，保持相对位置不变。

        移动后用 update() 强制重绘透明背景：Windows DWM 下置顶透明窗
        经 move() 移动后旧位置不会被系统自动擦除，会留下残影，主动
        update() 可触发 Qt 重绘新区域并释放旧区域，消除拖影。
        """
        m = self._mascot
        if m is None:
            return
        x = m.x() + (m.width() - self.width()) // 2
        y = m.y() + m.height() + self._gap
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)
            self.update()

    def _refresh_state(self):
        """按播放器状态刷新切换类按钮图标（播放/暂停、收藏红心）"""
        player = self._player
        playing = False
        if player is not None:
            btn = getattr(player, 'btn_play', None)
            playing = bool(btn is not None and btn.is_playing)
        fav = False
        if player is not None:
            pr = getattr(player, '_playing_row', -1)
            queue = getattr(player, '_panel_queue', [])
            if 0 <= pr < len(queue):
                fav = bool(player._is_fav(queue[pr]))
        if playing == self._last_playing and fav == self._last_fav:
            return
        self._last_playing, self._last_fav = playing, fav
        for btn, typ, ic_n, ic_a in self._buttons:
            if typ == "play_pause" and ic_a is not None:
                btn._icon = ic_a if playing else ic_n
                btn.update()
            elif typ == "favorite" and ic_a is not None:
                btn._icon = ic_a if fav else ic_n
                btn.update()

    def sync_state(self):
        """由 MascotWindow 定时调用：同步位置与图标状态"""
        self._sync_position()
        self._refresh_state()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._bg_image is not None:
            # 底图：高度固定为控件高度（即 frame_height + 内边距），宽度按比例
            # 自适应（不拉伸不变形）；frame_height 因此对底图生效。宽度超出
            # 控件的部分由控件边界自然裁掉，宽度不足则左右留白并居中。
            dpr = self.devicePixelRatio() or 1.0
            target = self.rect()
            bg = self._bg_image
            ar = (bg['w'] / bg['h']) if bg['h'] else 1.0
            bh = target.height()
            bw = int(bh * ar)
            dx = target.x() + (target.width() - bw) / 2.0
            dy = target.y()  # 高度已满，与控件顶边对齐
            rect = QRectF(dx, dy, bw, bh)
            if bg['kind'] == 'svg':
                # 矢量底图：用 QSvgRenderer 按目标尺寸（含 dpr）直接渲染，
                # 始终高清，无像素点。
                from PyQt5.QtSvg import QSvgRenderer
                renderer = QSvgRenderer(bg['text'].encode('utf-8'))
                if renderer.isValid():
                    renderer.render(p, rect)
            else:
                # 位图底图：按目标尺寸平滑缩放绘制。
                pix = bg['pixmap']
                scaled = pix.scaled(int(bw * dpr), int(bh * dpr),
                                    Qt.KeepAspectRatio, Qt.SmoothTransformation)
                scaled.setDevicePixelRatio(dpr)
                p.drawPixmap(QPointF(dx, dy), scaled)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(self._brush)
            p.drawRoundedRect(r, self._radius, self._radius)
        p.end()
        super().paintEvent(e)

class MascotWindow(QWidget):
    """看板娘：独立透明置顶小窗。
    支持：待机浮动、拖拽移动、单击说话、右键菜单（隐藏/置顶/待机浮动/切换看板娘），
    以及与播放器联动（切歌/暂停/收藏时说话）。
    形象与对话统一由"资源包"提供：脚本同级 mascot/<包名>/ 目录下
    settings.json 声明立绘文件名与全部对话/触发条件，用户复制该目录
    即可自定义新看板娘（见 mascot/test/settings.json 示例）。
    """

    def __init__(self, player, scale=1.0, pack=None, pos=None,
                 float_enabled=True, topmost=True):
        """pos：切换看板娘时沿用旧窗口的屏幕位置；None 则按默认定位到右下角。
        float_enabled / topmost：从主窗口持久化配置读取的初始浮动/置顶状态。"""
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self._player = player
        self._scale = scale
        # 资源包：{'id','name','dir','config'}；pack=None 时回退到 mascot 根目录旧版素材
        self._pack = pack
        self._pack_dir = pack['dir'] if pack else None
        cfg = pack['config'] if pack else None
        self._dialogues = (cfg or {}).get('dialogues', {}) or {}
        self.setFont(QApplication.font())  # 与主窗口字体一致（Microsoft YaHei）
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("看板娘")
        self._dragging = False
        self._moved = False       # 本次按压是否发生了拖拽（用于保存位置）
        self._press_pos = QPoint()
        self._drag_offset = QPoint()
        self._float_angle = 0.0
        self._base_img_y = 0
        self._pm = None
        self._float_enabled = bool(float_enabled)
        self._topmost = bool(topmost)
        self._controls = None    # 控制小组件（在 _build_controls 中创建）
        if not self._topmost:
            # 移除置顶标志（show 前设置，重显时生效）
            self.setWindowFlag(Qt.WindowStaysOnTopHint, False)
        # 布局参数（_load_image 中赋值）
        self._img_w = 0
        self._img_y = 0
        self._img_x = 0        # 立绘水平居中位置（paintEvent 用）
        self._float_y = 0.0    # 立绘当前浮动 y（浮点，亚像素平滑）

        # 形象（鼠标穿透，交互由窗口统一处理）
        self._img_label = QLabel(self)
        self._img_label.setStyleSheet("background: transparent; border: none;")
        self._img_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setCursor(Qt.PointingHandCursor)

        # 说话气泡
        self._bubble = _MascotBubble(self, scale)
        self._bubble.hide()
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self._on_bubble_timeout)

        # 待机浮动（60fps：形象由 paintEvent 亚像素绘制，小摆幅也平滑）
        self._float_timer = QTimer(self)
        self._float_timer.timeout.connect(self._tick_float)
        if self._float_enabled:
            self._float_timer.start(16)

        # 右键菜单（去掉 Windows 系统级菜单阴影）
        self._menu = QMenu(self)
        self._menu.setWindowFlags(self._menu.windowFlags() | Qt.FramelessWindowHint)
        reg_theme(self._menu, f"""
            QMenu {{
                background: #FFFFFF; border: 1px solid #E0E0E0;
                border-radius: {int(6*scale)}px;
                padding: {int(5*scale)}px {int(6*scale)}px;
                font-size: {int(14*scale)}px;
            }}
            QMenu::item {{
                padding: {int(8*scale)}px {int(24*scale)}px;
                margin: {int(1*scale)}px 0;
                color: #1A1A1A;
                border-radius: {int(4*scale)}px;
            }}
            QMenu::item:hover, QMenu::item:selected {{
                background-color: #F0F0F2; color: #EC4141;
            }}
            QMenu::item:checked {{
                background-color: #F0F0F2; color: #EC4141;
            }}
            QMenu::separator {{
                height: {int(1*scale)}px; background: #EBEBEB;
                margin: {int(4*scale)}px {int(12*scale)}px;
            }}
        """)
        act_hide = QAction("隐藏看板娘", self._menu)
        act_hide.triggered.connect(self._on_hide_action)
        self._menu.addAction(act_hide)
        act_top = QAction("总在最前", self._menu)
        act_top.setCheckable(True)
        act_top.setChecked(self._topmost)
        act_top.toggled.connect(self._toggle_topmost)
        self._menu.addAction(act_top)
        act_float = QAction("待机浮动", self._menu)
        act_float.setCheckable(True)
        act_float.setChecked(self._float_enabled)
        act_float.toggled.connect(self._toggle_float)
        self._menu.addAction(act_float)
        # 小组件：资源包配置了 controls.buttons 才可用；否则置灰不可触发
        cfg = (self._pack['config'] or {}) if self._pack else {}
        has_controls = bool((cfg.get('controls') or {}).get('buttons'))
        act_ctl = QAction("小组件", self._menu)
        act_ctl.setCheckable(True)
        act_ctl.setEnabled(has_controls)
        if player is not None:
            act_ctl.setChecked(has_controls and player.mascot_controls)
        else:
            act_ctl.setChecked(has_controls)
        act_ctl.toggled.connect(self._toggle_controls)
        self._menu.addAction(act_ctl)
        # 切换看板娘：列出 player 发现的全部资源包，选择后重建看板娘窗口
        player = self._player
        packs = []
        if player is not None:
            packs = getattr(player, '_discover_mascot_packs', lambda: [])()
        if len(packs) > 1:
            self._menu.addSeparator()
            switch_menu = self._menu.addMenu("切换看板娘")
            switch_menu.setStyleSheet(self._menu.styleSheet())
            switch_menu.setWindowFlags(switch_menu.windowFlags() | Qt.FramelessWindowHint)
            cur_id = getattr(player, 'mascot_pack_id', None)
            for p in packs:
                a = QAction(p['name'], switch_menu)
                a.setCheckable(True)
                a.setChecked(p['id'] == cur_id)
                a.triggered.connect(
                    lambda checked=False, pid=p['id']:
                        self._request_switch_mascot(pid))
                switch_menu.addAction(a)

        self._load_image()
        if pos is not None:
            # 切换看板娘：沿用旧窗口位置（越界时拉回屏幕内）
            self.move(pos)
            self._clamp_to_screen()
        else:
            self._place_initial()

        # ---------- 看板娘控制小组件（资源包 controls 配置驱动，可空） ----------
        self._controls = None
        self._build_controls()
        self._controls_timer = QTimer(self)
        self._controls_timer.timeout.connect(self._sync_controls)
        self._controls_timer.start(40)

    def _build_controls(self):
        """按资源包 controls 配置构建控制小组件；未配置 buttons 或开关关闭则不创建"""
        player = self._player
        cfg = ((self._pack['config'] or {}).get('controls') or {}) if self._pack else {}
        if not cfg.get('buttons'):
            self._controls = None
            return
        if player is not None and not player.mascot_controls:
            self._controls = None
            return
        ctl = _MascotControls(self, self._player, self._pack)
        if not ctl._buttons:
            # 配置存在但没有可识别的按钮（如类型全非法），不显示空组件
            ctl.close()
            self._controls = None
            return
        self._controls = ctl
        if self.isVisible():
            self._controls.show()

    def _toggle_controls(self, checked):
        """右键『小组件』开关：创建/销毁控制小组件，并持久化偏好"""
        player = self._player
        if player is not None:
            player.mascot_controls = bool(checked)
            player._save_settings()
        if checked:
            if self._controls is None:
                self._build_controls()
                if self._controls is not None and self.isVisible():
                    self._controls.show()
        else:
            if self._controls is not None:
                try:
                    self._controls.close()
                except Exception:
                    pass
                self._controls = None

    def _sync_controls(self):
        """定时同步控制小组件：位置、图标状态、显示/隐藏与看板娘一致"""
        c = self._controls
        if c is None:
            return
        if not self.isVisible():
            if c.isVisible():
                c.hide()
            return
        c.sync_state()

    def showEvent(self, e):
        super().showEvent(e)
        c = getattr(self, '_controls', None)
        if c is not None and self.isVisible():
            c.show()

    def hideEvent(self, e):
        super().hideEvent(e)
        c = getattr(self, '_controls', None)
        if c is not None:
            c.hide()

    def closeEvent(self, e):
        c = getattr(self, '_controls', None)
        if c is not None:
            try:
                c.close()
            except Exception:
                pass
        super().closeEvent(e)

    def _request_switch_mascot(self, pack_id):
        """请求切换看板娘资源包：交给主窗口重建（本窗口随后被关闭）"""
        player = self._player
        if player is not None:
            cb = getattr(player, 'switch_mascot', None)
            if cb is not None:
                cb(pack_id)

    # ---------- 形象加载 ----------
    def _load_image(self):
        # 立绘文件：资源包 sprite.image 声明；无资源包时读 mascot 根目录的
        # idle.png（单张立绘，不搞多份兜底）。
        spr = ((self._pack['config'] or {}).get('sprite') or {}) if self._pack else {}
        name = spr.get('image') or "idle.png"
        pm = QPixmap()
        if self._pack_dir:
            path = os.path.join(self._pack_dir, name)
        else:
            path = os.path.join(mascot_dir(), name)
        if os.path.exists(path):
            cand = QPixmap(path)
            if not cand.isNull():
                pm = cand
        # 立绘目标逻辑高度（可由资源包 sprite.height 覆盖，默认 230）
        spr_h = int((spr.get('height') or 230) * self._scale)
        # 顶部为气泡预留足够空间（最多 MAX_LINES 行）：多行气泡向上生长时
        # 不会越出窗口、也不遮立绘；形象紧贴气泡区下方。
        line_h = self._bubble.fontMetrics().height()
        gap = int(4 * self._scale)
        self._img_y = int(self._bubble.MAX_LINES * line_h
                          + self._bubble.PAD_Y * self._scale * 2
                          + self._bubble.TRI_H * self._scale) + gap
        # 底部预留浮动摆幅（_tick_float 最大向下位移 4*scale），避免立绘最下缘
        # 在浮动到最低点时越过窗口底边被裁切（无透明留白的资源包尤为明显）
        self._float_pad = int(4 * self._scale)
        if pm.isNull():
            # 无素材兜底：显示文字占位
            self._pm = None
            self._img_w = 0
            self._img_label.setText("看板娘")
            self._img_label.setAlignment(Qt.AlignCenter)
            reg_theme(self._img_label, 
                "background: transparent; color: #EC4141; font-size: 14px;")
            self.setFixedSize(int(140*self._scale),
                              self._img_y + int(120*self._scale) + self._float_pad)
            self._img_label.setGeometry(0, self._img_y,
                                        self.width(), self.height() - self._img_y)
            self._base_img_y = self._img_y
            return
        if not pm.hasAlphaChannel():
            pm = self._strip_white_bg(pm)
        # 按屏幕 DPR 缩放物理像素并标记 DPR，避免高分屏下位图被强行放大出现像素感
        dpr = self.devicePixelRatio() or 1.0
        h = spr_h  # 目标逻辑高度（资源包可配）
        pm = pm.scaledToHeight(max(1, int(h * dpr)), Qt.SmoothTransformation)
        pm.setDevicePixelRatio(dpr)
        self._pm = pm
        img_w = pm.width() / dpr    # 逻辑宽（窗口用逻辑坐标）
        img_h = pm.height() / dpr   # 逻辑高
        self._img_w = img_w
        self.setFixedSize(int(img_w + int(16 * self._scale)),
                          int(self._img_y + img_h) + self._float_pad)
        self._img_x = (self.width() - int(img_w)) // 2
        self._float_y = float(self._img_y)
        self._base_img_y = self._img_y
        # 立绘改由 paintEvent 亚像素绘制（实现平滑浮动），隐藏 QLabel
        self._img_label.hide()

    def _clamp_to_screen(self):
        """窗口对称增宽后若超出屏幕可用区，则拉回边界内"""
        scr = None
        if hasattr(self, 'screen'):
            try:
                scr = self.screen()
            except Exception:
                scr = None
        if scr is None:
            scr = QApplication.primaryScreen()
        if scr is None:
            return
        g = scr.availableGeometry()
        margin = int(8 * self._scale)
        x = self.x()
        if x < g.left() + margin:
            x = g.left() + margin
        if x + self.width() > g.right() - margin:
            x = g.right() - margin - self.width()
        y = self.y()
        if y < g.top() + margin:
            y = g.top() + margin
        if y + self.height() > g.bottom() - margin:
            y = g.bottom() - margin - self.height()
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def _place_img(self):
        if self._img_w > 0:
            # 记录水平居中位置，paintEvent 亚像素绘制
            self._img_x = max(0, int((self.width() - self._img_w) // 2))
            self.update()

    @staticmethod
    def _strip_white_bg(pm):
        """去掉纯白背景（AI 生成图兜底）：从角落 flood-fill 移除白色，
        角色内部白色不受影响；换成自己画的透明 PNG 时不会走这里。"""
        mask = pm.createHeuristicMask(True)
        out = QPixmap(pm.size())
        out.fill(Qt.transparent)
        p = QPainter(out)
        try:
            p.drawPixmap(0, 0, pm)
            p.setCompositionMode(QPainter.CompositionMode_DestinationIn)
            p.drawPixmap(0, 0, mask)
        finally:
            p.end()
        return out

    # ---------- 初始定位 ----------
    def _place_initial(self):
        """初始位置：靠近屏幕右下角（不依赖主窗口位置，避免跑到屏幕中间）。
        主窗口尚未显示时延迟重试，避免在未布局完成时定位。"""
        player = self._player
        if player is None or not player.isVisible():
            QTimer.singleShot(300, self._place_initial)
            return
        scale = self._scale
        # 取主窗口所在屏幕（多显示器时看板娘与播放器同屏），失败回退主屏
        geo = player.geometry()
        scr = None
        if hasattr(QApplication, 'screenAt'):  # Qt 5.14+
            scr = QApplication.screenAt(geo.center())
        if scr is None:
            scr = QApplication.primaryScreen()
        screen = scr.availableGeometry()
        margin = int(12 * scale)
        # x：窗口右缘贴屏幕右缘（歌词条已移除，窗口宽度固定不再扩展）
        x = screen.right() - self.width() - margin
        x = max(x, screen.left() + margin)
        # y：窗口底边贴屏幕可用区底边（任务栏上方）
        y = screen.bottom() - self.height() - margin
        y = max(y, screen.top() + margin)
        self.move(x, y)

    # ---------- 浮动动画 ----------
    def paintEvent(self, e):
        if self._pm is not None:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setRenderHint(QPainter.SmoothPixmapTransform)
            # 浮点 y：亚像素位移，小摆幅下也平滑无跳变
            p.drawPixmap(QPointF(self._img_x, self._float_y), self._pm)
            p.end()
        else:
            super().paintEvent(e)

    def _tick_float(self):
        import math
        self._float_angle += 0.032  # 60fps 下保持与原 40ms 相同的摆动周期
        if self._pm is not None:
            self._float_y = self._base_img_y \
                + math.sin(self._float_angle) * 4 * self._scale
            self.update()
        else:
            off = int(math.sin(self._float_angle) * 4 * self._scale)
            g = self._img_label.geometry()
            self._img_label.move(g.x(), self._base_img_y + off)

    # ---------- 说话 ----------
    def say(self, text):
        if not text:
            return
        # 气泡最大宽度不超过窗口，超长自动换行完整显示
        max_w = max(int(40 * self._scale), self.width() - int(8 * self._scale))
        self._bubble.set_text(text, max_w)
        # 下边缘固定在立绘正上方（留小缝隙，小三角指向形象），
        # 多行文本向上生长，下边缘位置始终不变、不遮立绘。
        gap = int(4 * self._scale)
        bottom = self._img_y - gap
        top = bottom - self._bubble.height()
        self._bubble.move((self.width() - self._bubble.width()) // 2, top)
        self._bubble.show()
        self._bubble_timer.start(4000)

    def _on_bubble_timeout(self):
        """气泡消失"""
        self._bubble.hide()

    # ---------- 对话（由资源包 settings.json 的 dialogues 驱动） ----------
    def _pick_line(self, key, **ctx):
        """从资源包配置中取一条随机对话并替换 {字段} 占位符。
        key 对应 settings.json 中 dialogues 下的触发条件名。
        未配置时返回 None（调用方静默跳过）。"""
        spec = self._dialogues.get(key)
        if isinstance(spec, dict):
            lines = spec.get('lines') or []
        else:
            lines = spec or []
        if not lines:
            return None
        text = random.choice(list(lines))
        for k, v in ctx.items():
            text = text.replace("{" + k + "}", str(v))
        return text

    def say_event(self, key, **ctx):
        """按触发条件说一句话（如 on_play / on_pause ...），配置缺失时静默"""
        text = self._pick_line(key, **ctx)
        if text:
            self.say(text)

    def _random_talk(self):
        """单击看板娘：优先播放中彩蛋（按配置概率），否则随机待机语"""
        click_cfg = self._dialogues.get('on_click') or {}
        lines = click_cfg.get('lines') or []
        playing_lines = click_cfg.get('playing_lines') or []
        chance = click_cfg.get('playing_chance', 0.6)
        player = self._player
        name = ""
        if player is not None:
            btn = getattr(player, 'btn_play', None)
            label = getattr(player, 'label_song_name', None)
            if btn is not None and label is not None and btn.is_playing:
                name = label.text()
                if name == "未播放":
                    name = ""
        if name and playing_lines and random.random() < chance:
            self.say(random.choice(playing_lines).replace("{song}", name))
        elif lines:
            self.say(random.choice(lines))

    # ---------- 交互 ----------
    def _persist_config(self):
        """把当前位置/浮动/置顶状态写回主窗口并保存（配置持久化）"""
        player = self._player
        if player is None:
            return
        try:
            player.mascot_pos = {"x": self.x(), "y": self.y()}
            player.mascot_float = bool(self._float_timer.isActive())
            player.mascot_topmost = bool(self.windowFlags() & Qt.WindowStaysOnTopHint)
            player._save_settings()
        except Exception:
            pass

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self._moved = False
            self._press_pos = e.globalPos()
            self._drag_offset = e.globalPos() - self.frameGeometry().topLeft()
        elif e.button() == Qt.RightButton:
            self._menu.exec_(e.globalPos())

    def mouseMoveEvent(self, e):
        if self._dragging and (e.buttons() & Qt.LeftButton):
            self.move(e.globalPos() - self._drag_offset)
            self._moved = True
            # 拖动时实时把控制小组件跟到正下方，避免 40ms 定时器
            # 滞后造成脱节/拖影
            c = getattr(self, '_controls', None)
            if c is not None:
                c._sync_position()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._dragging:
            self._dragging = False
            if self._moved:
                self._persist_config()
            elif (e.globalPos() - self._press_pos).manhattanLength() \
                    <= QApplication.startDragDistance():
                self._random_talk()

    def _toggle_topmost(self, checked):
        self.setWindowFlag(Qt.WindowStaysOnTopHint, checked)
        self.show()
        self._persist_config()

    def _toggle_float(self, checked):
        """右键菜单待机浮动开关：关闭时停止浮动并回到基准位置"""
        if checked:
            self._float_timer.start(16)
        else:
            self._float_timer.stop()
            # 复位到基准位置（立绘亚像素 / 文字占位两种方式）
            if self._pm is not None:
                self._float_y = float(self._base_img_y)
                self.update()
            else:
                self._img_label.move(self._img_label.x(), self._base_img_y)
        self._persist_config()

    def _on_hide_action(self):
        """右键‘隐藏看板娘’：隐藏本窗口，并同步主窗口设置页开关"""
        self.hide()
        player = self._player
        if player is not None:
            cb = getattr(player, '_sync_mascot_enabled', None)
            if cb is not None:
                cb(False)

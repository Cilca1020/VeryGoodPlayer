"""通用工具模块：主题色系统、路径与音频工具、可选依赖探测。

被 widgets / mascot / detail_panel / player / main 等模块共享。
模块级共享状态：
- 主题色 _THEME 与已登记控件 _THEME_WIDGETS
- 可选依赖标志 HAS_PYGAME / HAS_REQUESTS / HAS_MUTAGEN / HAS_NETEASE
"""
import sys
import os
import re
import json

# 项目根目录：core/ 的上一级（resources/mascot/config/songs 等程序级目录均相对此处）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 确保 stdout/stderr 使用 UTF-8，避免在 GBK 控制台/重定向环境下打印 emoji 崩溃
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

def _qt_message_handler(mode, context, message):
    """过滤 Qt 字体引擎的无害警告（如 OpenType support missing 刷屏），
    其余消息照常输出到 stderr。"""
    try:
        if "OpenType support missing" in message:
            return
        sys.stderr.write(message + "\n")
    except Exception:
        pass

# ---------- 全局主题色（供子控件绘制时读取，运行时由 MusicPlayer 更新） ----------
_THEME = {"color": "#EC4141"}

def _theme_color():
    return _THEME["color"]

def _theme_rgb():
    h = _THEME["color"].lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

def _set_theme(color):
    _THEME["color"] = color


# 主题化控件全局登记表：(widget, css模板)
_THEME_WIDGETS = []

def _darken(hex_color, factor=0.8):
    """将颜色按 factor 变暗（factor<1 变暗），返回 #RRGGBB"""
    h = hex_color.lstrip("#")
    r = int(h[0:2], 16) * factor
    g = int(h[2:4], 16) * factor
    b = int(h[4:6], 16) * factor
    return "#%02X%02X%02X" % (int(r), int(g), int(b))

def _css_global(css):
    """把样式模板里的主题色占位（#EC4141 / 236, 65, 65 / THEME_DARK）替换为当前主题色"""
    r, g, b = _theme_rgb()
    css = css.replace("#EC4141", _theme_color())
    css = css.replace("236, 65, 65", f"{r}, {g}, {b}")
    css = css.replace("THEME_DARK", _darken(_theme_color(), 0.8))
    return css

def reg_theme(widget, css_template):
    """登记并应用一个主题化样式（任何控件均可调用）；改主题色时统一重刷"""
    try:
        widget.setStyleSheet(_css_global(css_template))
        _THEME_WIDGETS.append((widget, css_template))
    except Exception:
        pass


# ---------- 深色模式（纯字符串映射，不依赖 Qt；由 player.py 在运行时切换） ----------
_DARK = {"on": False}

def is_dark():
    """当前是否处于深色模式"""
    return _DARK["on"]

def set_dark_mode(on):
    """设置深色模式开关（只改状态；样式重刷由调用方负责）"""
    _DARK["on"] = bool(on)

# 浅色背景 → 深色背景。已深色的底（#1A1A1A / #2A2A2A 等）刻意不映射，
# 这些是左侧菜单/播放列表面板的"常暗"设计，深浅模式下保持一致。
_DARK_BG = {
    "#FFFFFF": "#2B2B31",
    "#FBFBFD": "#202025",
    "#FAFAFC": "#202025",
    "#F8F8F8": "#27272D",
    "#F5F5F7": "#26262C",
    "#F1F1F5": "#34343C",
    "#F0F0F0": "#2E2E35",
    "#F0F0F2": "#2E2E35",
    "#EDEDF2": "#34343C",
    "#EEEEEE": "#2E2E35",
    "#EAEAEF": "#34343C",
    "#E9E9EF": "#3C3C45",
    "#E8E8EC": "#38383F",
    "#E5E5E5": "#3A3A42",
    "#E5E5EA": "#3A3A42",
    "#E0E0E0": "#3A3A42",
    "#EBEBEB": "#3A3A42",
    "#DCDCDC": "#3F3F47",
    # 滚动条把手：常态中灰、hover 提亮一档（深色下反向：略亮于深底）
    "#AAAAAA": "#5A5A63",
    "#C8C8CC": "#4A4A52",
    "#A8A8AE": "#5A5A63",
}

# 深色文字 → 浅色文字（背景变深后文字需要提亮才能保持对比度）
_DARK_FG = {
    "#1A1A1A": "#E6E6EA",
    "#333333": "#CFCFD6",
    "#4A4A4A": "#B8B8C0",
    "#55555E": "#A8A8B2",
    "#666666": "#A6A6B0",
    "#8A8A93": "#8E8E98",
    "#888888": "#90909A",
    "#999999": "#9A9AA4",
    "#AAAAAA": "#75757F",
    "#BBBBBB": "#70707A",
    "#25314C": "#B9C0D0",  # search.svg 图标原色（深藏青），深色下提亮
}

# 浅色分隔线/边框 → 暗灰（介于深底之上、文字之下）
_DARK_BORDER = {
    "#F0F0F3": "#32323A",
    "#E4E4EA": "#3D3D45",
    "#E5E5EA": "#3D3D45",
    "#DCDCDC": "#3D3D45",
    "#E0E0E0": "#3A3A42",
    "#E5E5E5": "#3A3A42",
    "#EBEBEB": "#3A3A42",
    "#D0D0D0": "#4A4A52",
    "#CCCCCC": "#4A4A52",
}

def _hex_repl(mapping):
    def f(h):
        key = "#" + h.group(1).upper()
        return mapping.get(key, h.group(0))
    return f

def dark_map_css(css):
    """把浅色 QSS 按声明属性分类映射为深色等价物；未开深色模式时原样返回。

    - 背景/底色属性 → _DARK_BG（含 qlineargradient 里的 stop 色值）；
    - 文字属性（color / selection-color）→ _DARK_FG，但白色文字保留：
      主题色按钮、常暗面板上的文字不能被映射成深色；
    - 边框类属性（border* / gridline-color）→ _DARK_BORDER。
    只处理 #RRGGBB 六位色值（项目内样式均为六位写法）。"""
    if not _DARK["on"] or not css:
        return css

    def repl(m):
        prop = m.group(1).lower()
        val = m.group(2)
        new = val
        if prop.endswith("background-color") or prop == "background":
            new = re.sub(r'#([0-9A-Fa-f]{6})\b', _hex_repl(_DARK_BG), val)
        elif prop == "color" or prop == "selection-color":
            if "#FFFFFF" not in val.upper():
                new = re.sub(r'#([0-9A-Fa-f]{6})\b', _hex_repl(_DARK_FG), val)
        elif prop.startswith("border") or prop == "gridline-color":
            new = re.sub(r'#([0-9A-Fa-f]{6})\b', _hex_repl(_DARK_BORDER), val)
        # 无变化时保留原文（含 qlineargradient(x1:0 ...) 等内嵌冒号的值不被破坏）
        if new == val:
            return m.group(0)
        return m.group(0).replace(val, new, 1)

    return re.sub(r'([-\w]+)\s*:\s*([^;{}]+)', repl, css)

def dark_paint_hex(hex_color):
    """自绘 paint(QColor) 用色随深色模式映射：先查背景表、再文字表、再边框表。

    供 widgets.py 等绕过 QSS 直接 QPainter 绘制的控件使用。"""
    if not _DARK["on"]:
        return hex_color
    key = hex_color.upper()
    for table in (_DARK_BG, _DARK_FG, _DARK_BORDER):
        if key in table:
            return table[key]
    return hex_color

# ---------- 安全导入 ----------
try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    pygame = None
    HAS_PYGAME = False
    print("⚠️ 提示：未安装pygame，播放功能将不可用。请执行: pip install pygame")

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    requests = None
    HAS_REQUESTS = False
    print("⚠️ 提示：未安装requests，在线搜索功能将不可用。请执行: pip install requests")

try:
    from mutagen import File as MutagenFile
    HAS_MUTAGEN = True
except ImportError:
    MutagenFile = None
    HAS_MUTAGEN = False
    print("💡 提示：安装 mutagen 可读取音频时长和标签 (pip install mutagen)")

try:
    from core.netease_api import NeteaseAPI, HAS_NETEASE, TOPLIST_IDS
except ImportError:
    NeteaseAPI = None
    HAS_NETEASE = False
    TOPLIST_IDS = {}
    print("⚠️ 提示：未找到 netease_api.py，在线功能不可用")

# ---------- 歌词解析 ----------
def parse_lrc(text):
    """解析 LRC 文本为 [(秒, 文本), ...] 列表"""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("[ti:") or line.startswith("[ar:") or \
           line.startswith("[al:") or line.startswith("[by:") or \
           line.startswith("[offset:") or line.startswith("[re:"):
            continue
        import re
        matches = re.findall(r'\[(\d+):(\d+(?:\.\d+)?)[^\]]*\]', line)
        if not matches:
            continue
        # 去掉时间标签后的文本
        txt = re.sub(r'\[.*?\]', '', line).strip()
        for m in matches:
            sec = int(m[0]) * 60 + float(m[1])
            lines.append((sec, txt))
    lines.sort(key=lambda x: x[0])
    return lines

def read_embedded_cover(fpath):
    """从音频文件内嵌封面读取图片字节（mp3 的 APIC 帧 / flac 的 PICTURE）。

    封面已随歌曲写入文件本体，离线播放无需任何外部文件。读取失败或
    无内嵌封面返回 None。供 MusicPlayer 与 DetailPanel 共享。
    """
    if not HAS_MUTAGEN or not fpath or not os.path.exists(fpath):
        return None
    ext = fpath.lower()
    try:
        if ext.endswith('.mp3'):
            from mutagen.id3 import ID3
            tags = ID3(fpath)
            for key in tags.keys():
                if key.startswith('APIC'):
                    return tags[key].data
        elif ext.endswith('.flac'):
            from mutagen.flac import FLAC
            audio = FLAC(fpath)
            if audio.pictures:
                return audio.pictures[0].data
    except Exception as e:
        print(f"⚠️ 读取内嵌封面失败: {e}")
    return None

def read_embedded_lyric(fpath):
    """从音频文件内嵌歌词读取文本（mp3 的 USLT 帧 / flac 的 lyrics 标签）。

    歌词已随歌曲写入文件本体，离线播放无需任何外部文件。读取失败或无
    内嵌歌词返回空字符串。供 MusicPlayer 与 DetailPanel 共享。
    """
    if not HAS_MUTAGEN or not fpath or not os.path.exists(fpath):
        return ""
    ext = fpath.lower()
    try:
        if ext.endswith('.mp3'):
            from mutagen.id3 import ID3
            tags = ID3(fpath)
            for key in tags.keys():
                if key.startswith('USLT'):
                    text = tags[key].text
                    if isinstance(text, list):
                        text = "\n".join(text)
                    return text or ""
        elif ext.endswith('.flac'):
            from mutagen.flac import FLAC
            audio = FLAC(fpath)
            if 'lyrics' in audio:
                val = audio['lyrics']
                return val[0] if isinstance(val, list) and val else (val or "")
    except Exception as e:
        print(f"⚠️ 读取内嵌歌词失败: {e}")
    return ""

# ---------- 打包路径兼容 ----------
def resource_path(relative):
    """通用资源路径：PyInstaller 打包后从临时解压目录（sys._MEIPASS）读取
    随包分发的只读资源（resources/icons 图标、resources/images 默认封面等）；
    开发环境直接从脚本目录读取。relative 使用相对路径，如 "resources/icons/play.svg"。
    当资源缺失时返回该路径（由调用方自行判断 exists 并兜底），不会抛异常。"""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative)
    return os.path.join(PROJECT_ROOT, relative)


def app_data_dir():
    """用户数据目录：打包后为 exe 所在目录（sys._MEIPASS 是临时解压目录，
    每次运行会重建且可能被清理，绝不能存放收藏/歌单/下载/缓存等持久数据）；
    开发环境为项目根目录。"""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return PROJECT_ROOT


def config_dir():
    """用户配置文件目录：统一存放 settings.json / favorites.json / playlists.json
    等持久配置，避免污染数据根目录。首次访问时自动创建。"""
    d = os.path.join(app_data_dir(), "config")
    try:
        if not os.path.exists(d):
            os.makedirs(d)
    except Exception:
        pass
    return d


def mascot_dir():
    """看板娘资源目录（mascot/<包名>/...）：
    打包后优先读取 exe 同级 mascot/ 目录 —— 用户可直接复制/添加资源包，而不用
    去翻 _internal 依赖目录；该目录不存在时回退到打包内置目录（sys._MEIPASS），
    开发环境则始终为项目根目录。"""
    base = os.path.join(app_data_dir(), "mascot")
    if os.path.isdir(base):
        return base
    return resource_path("mascot")


# ---------- 只读资源目录（相对路径，配合 resource_path 兼容打包环境） ----------
ICONS_DIR = os.path.join("resources", "icons")     # SVG 图标、程序图标 .ico
IMAGES_DIR = os.path.join("resources", "images")   # PNG/JPG 图片资源


def icon_path(name):
    """图标资源路径（resources/icons/<name>），兼容打包环境。"""
    return resource_path(os.path.join(ICONS_DIR, name))


def image_path(name):
    """图片资源路径（resources/images/<name>），兼容打包环境。"""
    return resource_path(os.path.join(IMAGES_DIR, name))

def _splash_enabled_setting():
    """直接从 settings.json 读取开屏画面开关（主窗口构建前判断），默认开启。"""
    try:
        fp = os.path.join(config_dir(), "settings.json")
        if os.path.exists(fp):
            with open(fp, 'r', encoding='utf-8') as f:
                return bool(json.load(f).get("splash_enabled", True))
    except Exception:
        pass
    return True

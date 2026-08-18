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

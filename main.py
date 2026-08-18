"""VeryGoodPlayer 程序入口。

负责创建 QApplication、设置全局字体/图标并启动主窗口 MusicPlayer；
主窗口类与其余实现按功能拆分至 utils / widgets / mascot / detail_panel / player。
"""
import sys
import os
import time

from PyQt5.QtWidgets import *
from PyQt5.QtCore import *
from PyQt5.QtGui import *

from core.utils import _qt_message_handler, resource_path, image_path, _splash_enabled_setting
from ui.widgets import SplashScreen
from core.player import MusicPlayer

if __name__ == "__main__":
    # 先安装消息过滤器，再创建 QApplication，确保字体初始化期间的警告也被拦截
    qInstallMessageHandler(_qt_message_handler)
    app = QApplication(sys.argv)
    app.setEffectEnabled(Qt.UI_AnimateTooltip, False)   # 禁用 Tooltip 动画
    app.setApplicationDisplayName("VeryGoodPlayer")
    app_icon_path = image_path("icon.png")
    if os.path.exists(app_icon_path):
        app.setWindowIcon(QIcon(app_icon_path))
    # Windows 任务栏图标修复
    try:
        import ctypes
        myappid = f"musicplayer.app.1"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except:
        pass
    font = QFont()
    font.setFamily("Microsoft YaHei")
    font.setPointSize(10)
    app.setFont(font)

    window = MusicPlayer()
    window.show()
    # 再次设置窗口图标（确保任务栏更新）
    if os.path.exists(app_icon_path):
        window.setWindowIcon(QIcon(app_icon_path))
        app.setWindowIcon(QIcon(app_icon_path))

    # 开屏画面：覆盖主窗口内容区（标题栏下方 body_widget）显示，不遮标题栏
    # （设置里可关闭；图片缺失自动跳过），至少展示约 0.9 秒后开始淡出
    splash = None
    splash_t0 = time.time()
    try:
        if _splash_enabled_setting():
            splash_path = image_path("splash_screen.jpg")
            if os.path.exists(splash_path):
                splash = SplashScreen(window.body_widget, splash_path)
    except Exception as e:
        print(f"⚠️ 开屏画面启动失败：{e}")
        splash = None

    if splash is not None:
        remaining = max(0, int(0.9 * 1000 - (time.time() - splash_t0) * 1000))
        QTimer.singleShot(remaining, splash._fade_and_close)

    sys.exit(app.exec_())

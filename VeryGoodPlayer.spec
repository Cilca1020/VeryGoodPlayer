# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

datas = [('resources', 'resources')]
# 看板娘资源包（mascot/<id>/settings.json + 立绘）不打进 _internal：
# 运行时从 exe 同级 mascot/ 目录读取（见 core/utils.py 的 mascot_dir），
# 由构建脚本在打包完成后复制到 exe 旁，用户可自行添加/替换。
binaries = []
hiddenimports = ['diskcache']
tmp_ret = collect_all('NeteaseCloudMusic')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('py_mini_racer')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
# pycryptodome：netease_api.py 用 Python 端 eapi 加密修复中文搜索，需打包
tmp_ret = collect_all('Crypto')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]

# py_mini_racer 在 frozen 环境下通过 sys._MEIPASS 直接查找 mini_racer.dll
# （只查 _MEIPASS 根目录，不会查 py_mini_racer 子目录），因此必须将该 DLL
# 额外放到 _MEIPASS 根目录，否则打包后 V8 引擎加载失败，联网功能全部不可用。
for _b in list(binaries):
    if os.path.basename(_b[0]).lower() == 'mini_racer.dll':
        binaries.remove(_b)
        binaries.append((_b[0], '.'))


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='VeryGoodPlayer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['resources/icons/icon.ico'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=['mini_racer.dll'],
    name='VeryGoodPlayer',
)

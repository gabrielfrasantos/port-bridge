# PyInstaller spec for port-bridge-gui.
#
# Windows (onefile .exe — consumed by Inno Setup):
#   pyinstaller portbridge-gui.spec
#
# Linux (onedir — consumed by appimagetool):
#   pyinstaller portbridge-gui.spec
#
# The spec detects the platform and switches mode automatically.

import sys

_WINDOWS = sys.platform == "win32"
_ICON = "assets/icon.ico" if _WINDOWS else "assets/icon.png"

a = Analysis(
    ["portbridge/gui/__main__.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # portbridge modules
        "portbridge.bridge_server",
        "portbridge.can_server",
        "portbridge.candle_bus",
        "portbridge.list_can_interfaces",
        "portbridge.serial_server",
        "portbridge.server_errors",
        "portbridge.gui.bridge_controller",
        "portbridge.gui.main_window",
        "portbridge.gui.tray",
        "portbridge.gui.updater",
        # python-can loads interface backends via dynamic string import;
        # PyInstaller cannot detect these automatically.
        "can.interfaces",
        "can.interfaces.slcan",
        "can.interfaces.socketcan",
        "can.interfaces.pcan",
        "can.interfaces.gs_usb",
        "can.interfaces.virtual",
        "can.interfaces.udp_multicast",
        "can.interfaces.vector",
        "can.interfaces.kvaser",
        "can.interfaces.ixxat",
        "can.interfaces.cantact",
        "can.interfaces.seeedstudio",
        "can.interfaces.robotell",
        "can.interfaces.usb2can",
        "can.interfaces.neousys",
        "can.interfaces.etas",
        "can.interfaces.systec",
        "can.interfaces.nixnet",
        "can.interfaces.iscan",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# cipher and a.zlib were removed in PyInstaller 6.0.
pyz = PYZ(a.pure)

if _WINDOWS:
    # Single .exe — Inno Setup wraps it into the installer.
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="port-bridge-gui",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,  # no console window on Windows
        icon=_ICON,
        disable_windowed_traceback=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )
else:
    # One-directory bundle — appimagetool wraps it into an AppImage.
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="port-bridge-gui",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=True,
        console=False,
        icon=_ICON,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=True,
        upx_exclude=[],
        name="port-bridge-gui",
    )

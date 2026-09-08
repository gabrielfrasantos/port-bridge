#!/usr/bin/env python3
"""Generate assets/icon.png and assets/icon.ico used by PyInstaller and the installers.

Run once before building:
    python scripts/generate_icon.py

Requires: PySide6 (already a [gui] dependency).
Produces:
    assets/icon.png   — 256×256 RGBA, used by AppImage and .desktop
    assets/icon.ico   — multi-size ICO, used by the Windows installer and .exe
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ASSETS.mkdir(exist_ok=True)


def _render_pixmap(size: int) -> QPixmap:  # noqa: F821
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QFont, QPainter, QPixmap

    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    margin = max(2, size // 16)
    radius = max(4, size // 5)
    p.setBrush(QColor("#1565C0"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(margin, margin, size - 2 * margin, size - 2 * margin, radius, radius)
    font_size = max(6, int(size * 0.33))
    font = QFont("Arial", font_size, QFont.Weight.Bold)
    p.setFont(font)
    p.setPen(QColor("white"))
    p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "PB")
    p.end()
    return px


def _pixmap_to_png_bytes(px: QPixmap) -> bytes:  # noqa: F821
    from PySide6.QtCore import QBuffer, QIODevice

    buf = QBuffer()
    buf.open(QIODevice.OpenMode.WriteOnly)
    px.save(buf, "PNG")
    buf.close()
    return bytes(buf.data())


def _build_ico(png_sizes: dict[int, bytes]) -> bytes:
    """Pack multiple PNG blobs into an ICO file (PNG-compressed frames)."""
    num = len(png_sizes)
    # ICO header: 6 bytes
    header = struct.pack("<HHH", 0, 1, num)
    # Each directory entry: 16 bytes
    # Image data starts after header (6) + entries (16 * num)
    data_offset = 6 + 16 * num
    dir_entries = b""
    image_data = b""
    for size, png in sorted(png_sizes.items()):
        w = h = 0 if size >= 256 else size  # 0 means 256 in ICO
        entry = struct.pack(
            "<BBBBHHII",
            w,
            h,  # width, height (0 = 256)
            0,  # color count (0 = no palette)
            0,  # reserved
            1,  # color planes
            32,  # bits per pixel
            len(png),  # size of image data
            data_offset + len(image_data),  # offset to image data
        )
        dir_entries += entry
        image_data += png
    return header + dir_entries + image_data


def main() -> None:
    # QApplication is needed to render QPixmap.
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)

    # PNG — 256×256 for AppImage / .desktop
    px256 = _render_pixmap(256)
    png_256 = _pixmap_to_png_bytes(px256)
    (ASSETS / "icon.png").write_bytes(png_256)
    print(f"Written {ASSETS / 'icon.png'}  ({len(png_256)} bytes)")

    # ICO — multi-size for Windows
    sizes = {
        16: _pixmap_to_png_bytes(_render_pixmap(16)),
        32: _pixmap_to_png_bytes(_render_pixmap(32)),
        48: _pixmap_to_png_bytes(_render_pixmap(48)),
        64: _pixmap_to_png_bytes(_render_pixmap(64)),
        128: _pixmap_to_png_bytes(_render_pixmap(128)),
        256: png_256,
    }
    ico = _build_ico(sizes)
    (ASSETS / "icon.ico").write_bytes(ico)
    print(f"Written {ASSETS / 'icon.ico'}  ({len(ico)} bytes)")

    _ = app  # keep alive


if __name__ == "__main__":
    main()

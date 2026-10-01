"""Reproduce the Windows icon from the repository's vector brand mark."""
import io
from pathlib import Path

from PySide6.QtCore import QByteArray, QBuffer, QIODevice
from PySide6.QtGui import QImage, QPainter
from PySide6.QtSvg import QSvgRenderer
from PIL import Image


def main():
    root = Path(__file__).resolve().parents[1]
    renderer = QSvgRenderer(str(root / "assets" / "relay-studio.svg"))
    if not renderer.isValid():
        raise ValueError("Invalid brand SVG")
    image = QImage(256, 256, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    array = QByteArray()
    buffer = QBuffer(array)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise OSError("Could not render brand image")
    Image.open(io.BytesIO(bytes(array))).save(root / "assets" / "relay-studio.ico",
        sizes=[(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48),
               (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    main()

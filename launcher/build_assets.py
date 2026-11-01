"""Create edition-specific assets without including private artwork in releases."""
import json
import shutil
from pathlib import Path


def prepare_assets(source: Path, output: Path, edition: str) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    if edition == "personal":
        shutil.copytree(source, output, dirs_exist_ok=True)
    else:
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QColor, QImage, QPainter, QPen
        from PIL import Image
        from ui import tokens as K
        for size in (256, 512, 1024):
            image = QImage(size, size, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.scale(size/256, size/256)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(str(K.LIGHT["INK"])))
            painter.drawRoundedRect(QRectF(4, 4, 248, 248), 54, 54)
            painter.setPen(QPen(QColor(str(K.LIGHT["POSTER_BG"])), 16, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(52, 81, 99, 60), 28, 28)
            painter.drawRoundedRect(QRectF(105, 115, 99, 60), 28, 28)
            painter.end()
            image.save(str(output / f"app_{size}.png"))
        with Image.open(output / "app_256.png") as icon:
            icon.save(output / "app.ico", sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
        shutil.copy2(output / "app.ico", output / "icon.ico")
        shutil.copy2(output / "app_256.png", output / "icon.png")
    (output / "edition.json").write_text(json.dumps({"edition": edition}), encoding="utf-8")
    return output

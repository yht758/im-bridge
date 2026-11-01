"""Draw supplied portrait art without enlarging or stretching the original."""
from pathlib import Path
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QWidget
from . import tokens as K

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "illustrations"


class PosterArt(QWidget):
    def __init__(self, filename, parent=None, crop=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._pixmap = QPixmap(str(ASSETS / filename))
        self._crop = crop

    def set_image(self, filename):
        self._pixmap = QPixmap(str(ASSETS / filename))
        self.update()

    def paintEvent(self, event):
        if self._pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(bounds, K.RADIUS_LG, K.RADIUS_LG)
        painter.setClipPath(clip)
        painter.fillRect(bounds, K.qcolor(K.t("POSTER_BG")))
        source = QRectF(self._pixmap.rect())
        if self._crop:
            x, y, w, h = self._crop
            source = QRectF(x * source.width(), y * source.height(), w * source.width(), h * source.height())
        scale = min(self.width()/source.width(), self.height()/source.height())
        # Keep native resolution at the current display's device pixel ratio.
        scale = min(scale, 1 / max(1, self.devicePixelRatioF()))
        w, h = source.width() * scale, source.height() * scale
        target = QRectF((self.width()-w)/2, (self.height()-h)/2, w, h)
        painter.drawPixmap(target, self._pixmap, source)

"""The look of the watch: the day and night faces, the alert card and the ripples (Features 2, 6 and 8).

Everything is painted here, in code, from the approved pictures (tall watch screens, 3 wide by 4 high).
The watch box is always exactly 188 x 250 pixels on screen (drawn in 150 x 200 units, 25% bigger), whatever it shows. The same on every system; the helper and the picture command both paint with this.
"""

import math
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QLinearGradient, QPainter, QPainterPath, QPen,
    QRadialGradient, QTextLayout, QTextOption,
)

from . import alerts

DESIGN_W, DESIGN_H = 150, 200  # everything below is drawn in these units
DESIGN_RADIUS = 22.0
SCALE = 1.25  # the watch on screen is 25% bigger than the drawing units
WIDTH, HEIGHT = round(DESIGN_W * SCALE), round(DESIGN_H * SCALE)
RADIUS = DESIGN_RADIUS * SCALE
RIPPLE_REACH = 40
RIPPLE_SECONDS = 1.5
FONT_FILE = Path(__file__).resolve().parent.parent / "ui" / "fonts" / "InstrumentSans-Variable.ttf"

NAVY = QColor("#010821")
DEEP_BLUE = QColor("#02235c")
EDGE = QColor("#3fd8ff")
EDGE_GLOW = QColor("#1670ff")
LINE = QColor("#0f6fcf")
WHITE = QColor("#ffffff")
BODY_TEXT = QColor("#f2f6fa")
NIGHT_AMPM = QColor("#7cc4ff")
AMBER = QColor("#ff9d14")

STRIP_HEIGHT = 32
TEXT_PAD_X = 10
TEXT_AREA = QRectF(TEXT_PAD_X, STRIP_HEIGHT + 14, DESIGN_W - 2 * TEXT_PAD_X, DESIGN_H - STRIP_HEIGHT - 14 - 12)
BODY_PX = 17
LINE_PX = 20
DIGITS_PX, AMPM_PX, AMPM_GAP = 36, 18, 5
DIGITS_SPACING = 1.0

_family = None


def family():
    """Register the bundled Instrument Sans once and return its family name."""
    global _family
    if _family is None:
        ident = QFontDatabase.addApplicationFont(str(FONT_FILE))
        _family = QFontDatabase.applicationFontFamilies(ident)[0]
    return _family


def font(pixels, weight, spacing=0.0):
    result = QFont(family())
    result.setPixelSize(pixels)
    result.setWeight(weight)
    if spacing:
        result.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return result


def digits_font():
    return font(DIGITS_PX, QFont.Weight.Bold, DIGITS_SPACING)


@dataclass(frozen=True)
class Face:
    digits: str
    ampm: str
    is_day: bool


@dataclass(frozen=True)
class Card:
    time_text: str
    text: str
    age: float  # seconds since the card appeared


@dataclass(frozen=True)
class CardLayout:
    layout: QTextLayout
    height: float

    @property
    def hidden(self):
        """Height of the text that does not fit in the card's text area."""
        return max(0.0, self.height - TEXT_AREA.height())


def card_layout(text):
    """Wrap the alert text to the card's text area. Used for both painting and the card's showing time."""
    layout = QTextLayout(text, font(BODY_PX, QFont.Weight.Bold))
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    layout.beginLayout()
    y = 0.0
    while True:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(TEXT_AREA.width())
        line.setPosition(QPointF(0, y))
        y += LINE_PX
    layout.endLayout()
    return CardLayout(layout, y)


def _box_path(inset=0.0):
    path = QPainterPath()
    path.addRoundedRect(QRectF(inset, inset, DESIGN_W - 2 * inset, DESIGN_H - 2 * inset), DESIGN_RADIUS - inset, DESIGN_RADIUS - inset)
    return path


def _wave(painter, wave, color):
    """One wave: (left y, first control point, second control point, right y, fill strength, fill depth)."""
    left, (x1, y1), (x2, y2), right, alpha, depth = wave
    path = QPainterPath()
    path.moveTo(-2, left)
    path.cubicTo(x1, y1, x2, y2, DESIGN_W + 2, right)
    edge = QPainterPath(path)
    path.lineTo(DESIGN_W + 2, DESIGN_H + 2)
    path.lineTo(-2, DESIGN_H + 2)
    path.closeSubpath()
    top = min(left, right, y1, y2)
    fill = QLinearGradient(0, top, 0, top + depth)
    fill.setColorAt(0, QColor(color.red(), color.green(), color.blue(), alpha))
    fill.setColorAt(1, QColor(color.red(), color.green(), color.blue(), 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(fill))
    painter.drawPath(path)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for width, line_alpha in ((5.0, 30), (1.2, 120)):
        painter.setPen(QPen(QColor(EDGE_GLOW.red(), EDGE_GLOW.green(), EDGE_GLOW.blue(), line_alpha), width))
        painter.drawPath(edge)


def _frame(painter, waves):
    """The dark screen with its soft glowing waves, clipped to the rounded box."""
    painter.setClipPath(_box_path())
    background = QLinearGradient(0, 0, 0, DESIGN_H)
    background.setColorAt(0, NAVY)
    background.setColorAt(0.55, DEEP_BLUE)
    background.setColorAt(1, NAVY)
    painter.fillRect(QRectF(0, 0, DESIGN_W, DESIGN_H), background)
    for wave in waves:
        _wave(painter, wave, EDGE_GLOW)


def _edge(painter):
    """The thin glowing cyan-blue edge."""
    painter.setClipPath(_box_path())
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for width, alpha in ((16.0, 18), (11.0, 28), (7.0, 50), (4.0, 110)):
        painter.setPen(QPen(QColor(EDGE_GLOW.red(), EDGE_GLOW.green(), EDGE_GLOW.blue(), alpha), width))
        painter.drawPath(_box_path(0.5))
    painter.setPen(QPen(EDGE, 2.2))
    painter.drawPath(_box_path(1.2))


def _sun(painter, center, radius):
    painter.setPen(QPen(QColor("#ffb21f"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for i in range(8):
        angle = i * math.pi / 4
        inner, outer = radius * 1.45, radius * 2.0
        painter.drawLine(
            QPointF(center.x() + inner * math.cos(angle), center.y() + inner * math.sin(angle)),
            QPointF(center.x() + outer * math.cos(angle), center.y() + outer * math.sin(angle)),
        )
    disc = QRadialGradient(center, radius)
    disc.setColorAt(0, QColor("#ffe45c"))
    disc.setColorAt(1, QColor("#ffae00"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(disc))
    painter.drawEllipse(center, radius, radius)


def _moon(painter, center, radius):
    halo = QRadialGradient(center, radius * 2.2)
    halo.setColorAt(0.4, QColor(110, 170, 255, 90))
    halo.setColorAt(1, QColor(110, 170, 255, 0))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(halo))
    painter.drawEllipse(center, radius * 2.2, radius * 2.2)
    disc = QRadialGradient(QPointF(center.x() - radius * 0.3, center.y() - radius * 0.3), radius * 1.4)
    disc.setColorAt(0, QColor("#f2f8ff"))
    disc.setColorAt(1, QColor("#7da6e6"))
    painter.setBrush(QBrush(disc))
    painter.drawEllipse(center, radius, radius)
    painter.setBrush(QColor(80, 120, 190, 110))
    for dx, dy, r in ((-0.25, -0.1, 0.28), (0.3, 0.3, 0.22), (0.15, -0.4, 0.15)):
        painter.drawEllipse(QPointF(center.x() + dx * radius, center.y() + dy * radius), r * radius, r * radius)


def _stars(painter):
    painter.setPen(Qt.PenStyle.NoPen)
    for x, y, size in ((110, 22, 1.1), (130, 30, 0.9), (16, 74, 1.0), (73, 76, 0.8), (134, 76, 1.1), (88, 81, 0.8),
                       (122, 84, 0.9), (60, 60, 0.7), (18, 126, 0.8), (74, 122, 1.0), (88, 132, 0.8),
                       (134, 108, 1.0), (60, 18, 0.8), (40, 100, 0.7)):
        painter.setBrush(QColor(210, 230, 255, 200))
        painter.drawEllipse(QPointF(x, y), size, size)


def text_width(digits, ampm):
    """Width of the time with its AM or PM, as the face paints it."""
    return QFontMetricsF(digits_font()).horizontalAdvance(digits) + AMPM_GAP \
        + QFontMetricsF(font(AMPM_PX, QFont.Weight.DemiBold)).horizontalAdvance(ampm)


DAY_WAVES = (
    (70, (45, 45), (95, 5), 6, 70, 45),
    (100, (45, 80), (100, 125), 105, 85, 50),
    (150, (50, 120), (105, 180), 138, 95, 55),
    (190, (55, 165), (110, 205), 168, 80, 30),
)
NIGHT_WAVES = (
    (142, (45, 136), (100, 124), 128, 90, 50),
    (162, (50, 150), (100, 172), 154, 120, 40),
    (180, (60, 170), (110, 186), 166, 110, 30),
)


def _paint_face(painter, face):
    _frame(painter, DAY_WAVES if face.is_day else NIGHT_WAVES)
    if not face.is_day:
        _stars(painter)
    digits_font_, ampm_font = digits_font(), font(AMPM_PX, QFont.Weight.DemiBold)
    digits_width = QFontMetricsF(digits_font_).horizontalAdvance(face.digits)
    left = (DESIGN_W - text_width(face.digits, face.ampm)) / 2
    baseline = 78 if face.is_day else 60
    painter.setPen(WHITE)
    painter.setFont(digits_font_)
    painter.drawText(QPointF(left, baseline), face.digits)
    painter.setPen(WHITE if face.is_day else NIGHT_AMPM)
    painter.setFont(ampm_font)
    painter.drawText(QPointF(left + digits_width + AMPM_GAP, baseline), face.ampm)
    word = "Day" if face.is_day else "Night"
    word_font = font(22 if face.is_day else 19, QFont.Weight.Medium)
    if face.is_day:
        _sun(painter, QPointF(52, 122), 9.5)
        word_at = QPointF(79, 131)
    else:
        _moon(painter, QPointF(48, 101), 19)
        word_at = QPointF(79, 107)
    painter.setPen(WHITE)
    painter.setFont(word_font)
    painter.drawText(word_at, word)
    _edge(painter)


def _alert_sign(painter, left, top, size):
    sign = QPainterPath()
    sign.moveTo(left + size / 2, top)
    sign.lineTo(left + size, top + size * 0.9)
    sign.lineTo(left, top + size * 0.9)
    sign.closeSubpath()
    painter.setPen(QPen(AMBER, 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(AMBER)
    painter.drawPath(sign)
    painter.setPen(QPen(NAVY, 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(QPointF(left + size / 2, top + size * 0.32), QPointF(left + size / 2, top + size * 0.58))
    painter.drawPoint(QPointF(left + size / 2, top + size * 0.74))


CARD_WAVES = (
    (55, (45, 40), (100, 30), 38, 45, 50),
    (150, (50, 110), (105, 150), 120, 70, 50),
    (185, (55, 160), (110, 195), 165, 80, 30),
)


def _paint_card(painter, card):
    _frame(painter, CARD_WAVES)
    painter.setPen(QPen(LINE, 1.0))
    painter.drawLine(QPointF(0, STRIP_HEIGHT + 0.5), QPointF(DESIGN_W, STRIP_HEIGHT + 0.5))
    _alert_sign(painter, 13, 11, 15)
    strip_font = font(11, QFont.Weight.DemiBold)
    painter.setFont(strip_font)
    painter.setPen(WHITE)
    painter.drawText(QPointF(33, 23), "City Alert")
    time_width = QFontMetricsF(strip_font).horizontalAdvance(card.time_text)
    painter.drawText(QPointF(DESIGN_W - 13 - time_width, 23), card.time_text)
    layout = card_layout(card.text)
    painter.save()
    painter.setClipRect(TEXT_AREA)
    painter.setPen(BODY_TEXT)
    offset = alerts.scroll_offset(card.age, layout.hidden)
    layout.layout.draw(painter, QPointF(TEXT_AREA.x(), TEXT_AREA.y() - offset))
    painter.restore()
    _edge(painter)


def paint(painter, view):
    """Paint a Face or a Card into the watch box whose top-left corner is at (0, 0)."""
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.save()
    painter.scale(SCALE, SCALE)
    (_paint_card if isinstance(view, Card) else _paint_face)(painter, view)
    painter.restore()


def render(view):
    """The watch box as a picture, WIDTH x HEIGHT."""
    image = QImage(WIDTH, HEIGHT, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint(painter, view)
    painter.end()
    return image


def ripple_size():
    return WIDTH + 2 * RIPPLE_REACH, HEIGHT + 2 * RIPPLE_REACH


def paint_ripples(painter, seconds):
    """Three thin rings in the watch's edge colour spreading up to 40 px beyond the box and fading.

    The box sits at (40, 40) inside the layer. Nothing is painted over the box itself.
    """
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for ring in range(3):
        progress = (seconds / RIPPLE_SECONDS - ring * 0.18) / 0.64
        if not 0.0 < progress < 1.0:
            continue
        reach = RIPPLE_REACH * (1 - (1 - progress) ** 2)
        color = QColor(EDGE)
        color.setAlpha(int(230 * (1 - progress)))
        painter.setPen(QPen(color, 1.5))
        rect = QRectF(RIPPLE_REACH - reach, RIPPLE_REACH - reach, WIDTH + 2 * reach, HEIGHT + 2 * reach)
        painter.drawRoundedRect(rect, RADIUS + reach, RADIUS + reach)

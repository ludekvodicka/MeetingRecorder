"""Design tokens, the application style sheet and the dark or light theme in use.

Dark is the default. The theme follows the operating system's colour scheme; a scheme Qt
cannot read (some Linux desktops) stays dark. Widgets that paint themselves or tint icons
read the same tokens through color() and repaint on notifier.changed.
"""

import re
import tempfile
from pathlib import Path

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontDatabase, QPalette

from audiorecorder.ui import icons

FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
MONO = "JetBrains Mono"

TOKENS = {
    "dark": {
        "bg.window": "#0F1012",
        "bg.surface": "#16171A",
        "bg.raised": "#1D1F23",
        "bg.raised2": "#24262B",
        "bg.hover": "#2A2D33",
        "bg.selected": "#1A2740",
        "bg.input": "#121316",
        "bg.meter": "#131417",
        "border.subtle": "#23252A",
        "border.default": "#33363C",
        "border.strong": "#474B53",
        "text.primary": "#EDEEF0",
        "text.secondary": "#A6AAB2",
        "text.tertiary": "#858A93",
        "text.disabled": "#5A5E66",
        "text.onFill": "#FFFFFF",
        "accent": "#5C9BFF",
        "accent.fill": "#2F6FE4",
        "accent.fillHover": "#2763D4",
        "accent.fillPressed": "#2158BF",
        "accent.soft": "rgba(92,155,255,0.14)",
        "accent.border": "rgba(92,155,255,0.45)",
        "rec": "#FF5C61",
        "rec.fill": "#C8323A",
        "rec.fillHover": "#B42A32",
        "rec.fillPressed": "#A0242B",
        "rec.soft": "rgba(255,92,97,0.10)",
        "rec.border": "rgba(255,92,97,0.45)",
        "live": "#B49CFF",
        "live.fill": "#6E4BD8",
        "live.fillHover": "#6342C6",
        "live.soft": "rgba(160,130,255,0.13)",
        "live.border": "rgba(180,156,255,0.35)",
        "success": "#46CF86",
        "success.soft": "rgba(70,207,134,0.13)",
        "warning": "#F2B550",
        "warning.soft": "rgba(242,181,80,0.13)",
        "error": "#FF6B70",
        "error.soft": "rgba(255,107,112,0.10)",
        "error.border": "rgba(255,107,112,0.45)",
        "speaker.me": "#6EA8FF",
        "speaker.r1": "#3CC8B9",
        "speaker.r2": "#F5A25A",
        "speaker.r3": "#E77EBE",
        "meter.sysLow": "#4C8DFF",
        "meter.sysHigh": "#E879B9",
        "meter.micLow": "#3DCB7F",
        "meter.micHigh": "#F59E52",
        "meter.flat": "#2C2F35",
        "tooltip.bg": "#2E3138",
        "tooltip.text": "#F2F3F5",
        "pill.bg": "#17181B",
    },
    "light": {
        "bg.window": "#F3F4F6",
        "bg.surface": "#FFFFFF",
        "bg.raised": "#FFFFFF",
        "bg.raised2": "#FFFFFF",
        "bg.hover": "#ECEEF1",
        "bg.selected": "#E5EDFC",
        "bg.input": "#FFFFFF",
        "bg.meter": "#F3F4F6",
        "border.subtle": "#E8EAEE",
        "border.default": "#D6D9DF",
        "border.strong": "#B5BAC3",
        "text.primary": "#16181D",
        "text.secondary": "#4C515A",
        "text.tertiary": "#656B75",
        "text.disabled": "#A6ABB3",
        "text.onFill": "#FFFFFF",
        "accent": "#2160E0",
        "accent.fill": "#2563EB",
        "accent.fillHover": "#1D4FD8",
        "accent.fillPressed": "#1E40AF",
        "accent.soft": "rgba(37,99,235,0.09)",
        "accent.border": "rgba(37,99,235,0.40)",
        "rec": "#C42A31",
        "rec.fill": "#C8323A",
        "rec.fillHover": "#B42A32",
        "rec.fillPressed": "#A0242B",
        "rec.soft": "rgba(217,45,53,0.07)",
        "rec.border": "rgba(200,50,58,0.40)",
        "live": "#6236D0",
        "live.fill": "#6E4BD8",
        "live.fillHover": "#6342C6",
        "live.soft": "rgba(110,75,216,0.09)",
        "live.border": "rgba(110,75,216,0.30)",
        "success": "#167044",
        "success.soft": "rgba(22,138,80,0.10)",
        "warning": "#965400",
        "warning.soft": "rgba(214,138,0,0.12)",
        "error": "#BF2930",
        "error.soft": "rgba(217,45,53,0.07)",
        "error.border": "rgba(200,50,58,0.40)",
        "speaker.me": "#2160E0",
        "speaker.r1": "#0B7F73",
        "speaker.r2": "#B9530A",
        "speaker.r3": "#AE2F78",
        "meter.sysLow": "#4C8DFF",
        "meter.sysHigh": "#D9529F",
        "meter.micLow": "#22A863",
        "meter.micHigh": "#E8822F",
        "meter.flat": "#D6D9DF",
        "tooltip.bg": "#22252B",
        "tooltip.text": "#F2F3F5",
        "pill.bg": "#17181B",
    },
}

QSS = """
QWidget { font-family: "Inter"; font-size: 13px; color: @text.primary; }
QMainWindow, QDialog { background-color: @bg.window; }
QLabel { background: transparent; }

QFrame#header { background-color: @bg.surface; border: none;
    border-bottom: 1px solid @border.subtle; }
QLabel#appTitle { font-size: 15px; font-weight: 600; }
QFrame#vsep { background-color: @border.default; border: none; }
QStatusBar { background-color: @bg.surface; border-top: 1px solid @border.subtle;
    color: @text.tertiary;
    font-size: 12px; min-height: 28px; }
QStatusBar::item { border: none; }
QStatusBar QLabel { color: @text.tertiary; font-size: 12px; }

QPushButton { min-height: 30px; max-height: 30px; padding: 0px 14px; border-radius: 6px;
    border: 1px solid @border.default; background-color: @bg.raised2; color: @text.primary;
    font-weight: 500; }
QPushButton:hover { background-color: @bg.hover; border-color: @border.strong; }
QPushButton:pressed { background-color: @bg.input; }
QPushButton:focus { border-color: @accent; }
QPushButton:disabled { background-color: transparent; border-color: @border.subtle;
    color: @text.disabled; }
QPushButton[size="sm"] { min-height: 26px; max-height: 26px; padding: 0px 10px; }
QPushButton[variant="primary"] { background-color: @accent.fill; border: 1px solid @accent.fill;
    color: @text.onFill;
    font-weight: 600; padding: 0px 16px; }
QPushButton[variant="primary"]:hover { background-color: @accent.fillHover;
    border-color: @accent.fillHover; }
QPushButton[variant="primary"]:pressed { background-color: @accent.fillPressed; }
QPushButton[variant="primary"]:disabled { background-color: @bg.raised2;
    border-color: @bg.raised2; color: @text.disabled; }
QPushButton[variant="accentSoft"] { background-color: @accent.soft;
    border: 1px solid @accent.border; color: @accent; }
QPushButton[variant="accentSoft"]:hover { border-color: @accent; }
QPushButton[variant="danger"] { background-color: @rec.fill; border: 1px solid @rec.fill;
    color: @text.onFill; font-weight: 600; }
QPushButton[variant="danger"]:hover { background-color: @rec.fillHover;
    border-color: @rec.fillHover; }
QPushButton[variant="danger"]:pressed { background-color: @rec.fillPressed; }
QPushButton[pill="true"] { min-height: 30px; max-height: 30px; border-radius: 16px;
    padding: 0px 16px 0px 12px; }

QToolButton[variant="quiet"] { background-color: transparent; border: none; color: @text.secondary;
    padding: 0px; min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px;
    border-radius: 6px; }
QToolButton[variant="quiet"]:hover { background-color: @bg.hover; }
QToolButton[variant="quiet"]:pressed { background-color: @border.default; }
QToolButton[variant="quiet"][size="sm"] { min-width: 28px; max-width: 28px; min-height: 28px;
    max-height: 28px; }
QToolButton::menu-indicator { image: none; width: 0px; }

QPushButton[toggle="live"] { padding: 0px 10px; background-color: @bg.raised;
    color: @text.secondary; }
QPushButton[toggle="live"]:hover { background-color: @bg.hover; color: @text.primary; }
QPushButton[toggle="live"]:checked { background-color: @live.fill; border-color: @live.fill;
    color: @text.onFill; font-weight: 600; }
QPushButton[toggle="live"]:checked:hover { background-color: @live.fillHover; }

QPushButton#recordButton { min-height: 44px; max-height: 44px; border-radius: 22px;
    padding: 0px 22px 0px 18px;
    border: none; background-color: @rec.fill; color: @text.onFill; font-size: 15px;
    font-weight: 600; }
QPushButton#recordButton:hover { background-color: @rec.fillHover; }
QPushButton#recordButton:pressed { background-color: @rec.fillPressed; }
QPushButton#recordButton:disabled { background-color: transparent;
    border: 1px solid @border.default; color: @text.disabled; }
QPushButton#muteButton { min-height: 44px; max-height: 44px; border-radius: 22px;
    padding: 0px 16px; font-size: 14px; font-weight: 600; }
QPushButton#muteButton:checked { background-color: @rec.fill; border-color: @rec.fill;
    color: @text.onFill; }
QPushButton#detailsButton { min-height: 36px; max-height: 36px; }

QFrame#stateCard { background-color: @bg.raised; border: 1px solid @border.default;
    border-radius: 12px; }
QFrame#stateCard[state="recording"] { background-color: @rec.soft; border-color: @rec.border; }
QFrame#stateCard[state="failed"] { background-color: @error.soft; border-color: @error.border; }
QFrame#stateCard[state="dictation"] { background-color: @live.soft; border-color: @live.border; }
QLabel#timer { font-family: "JetBrains Mono"; font-size: 30px; font-weight: 500;
    color: @text.tertiary; }
QLabel#timer[state="recording"] { color: @text.primary; }
QLabel#timer[state="failed"] { color: @text.secondary; }
QLabel#stateLine { font-size: 12px; font-weight: 500; color: @text.tertiary; }
QLabel#stateLine[tone="error"] { color: @error; font-weight: 600; }
QLabel#stateLine[tone="live"] { color: @live; font-weight: 600; }
QLabel#recLine { font-size: 12px; font-weight: 500; color: @text.secondary; }
QLabel#failTitle { font-size: 14px; font-weight: 500; }
QLabel#failDetail, QLabel#hint, QLabel#keyHint { font-size: 12px; color: @text.secondary; }
QLabel#failDetail { font-size: 13px; }
QLabel#plus { font-size: 12px; color: @text.tertiary; }
QLabel[keycap="true"] { min-height: 20px; max-height: 20px; padding: 0px 7px; border-radius: 5px;
    background-color: @bg.raised2;
    border: 1px solid @border.default; border-bottom-width: 2px; font-size: 11px;
    font-weight: 600; }
QFrame#dictPreview { background-color: @bg.raised; border: 1px dashed @live.border;
    border-radius: 8px; }
QFrame#dictPreview QLabel#liveTag { font-size: 11px; font-weight: 600; color: @live; }
QFrame#dictPreview QLabel#previewText { font-size: 15px; font-style: italic;
    color: @text.secondary; }
QLabel#pastedTag { font-size: 12px; color: @text.tertiary; }
QLabel#pastedText { font-size: 12px; color: @text.secondary; }
QLabel#meterLabel { font-size: 12px; font-weight: 500; color: @text.secondary; }
QLabel#meterLabel[muted="true"] { color: @rec; }
QLabel#meterDevice { font-size: 12px; color: @text.tertiary; }
QLabel#meterDevice[muted="true"] { color: @rec; }

QFrame#costStrip { background-color: @live.soft; border: none;
    border-bottom: 1px solid @live.border; }
QFrame#costStrip QLabel { font-size: 12px; color: @text.secondary; }
QFrame#costStrip QLabel#lead { color: @live; font-weight: 600; }

QFrame[chip] { min-height: 20px; max-height: 20px; border-radius: 10px; border: none; }
QFrame[chip] QLabel { font-size: 12px; font-weight: 500; background: transparent; }
QFrame[chip="neutral"] { background-color: @bg.hover; }
QFrame[chip="neutral"] QLabel { color: @text.secondary; }
QFrame[chip="success"] { background-color: @success.soft; }
QFrame[chip="success"] QLabel { color: @success; }
QFrame[chip="accent"] { background-color: @accent.soft; }
QFrame[chip="accent"] QLabel { color: @accent; }
QFrame[chip="warning"] { background-color: @warning.soft; }
QFrame[chip="warning"] QLabel { color: @warning; }
QFrame[chip="error"] { background-color: @error.soft; }
QFrame[chip="error"] QLabel { color: @error; }
QFrame[chip="live"] { background-color: @live.soft; }
QFrame[chip="live"] QLabel { color: @live; }
QFrame[chip="muted"] { background-color: @rec.soft; border: 1px solid @rec.border; }
QFrame[chip="muted"] QLabel { color: @rec; font-weight: 600; }
QFrame[chip="rec"] { background-color: @rec.fill; }
QFrame[chip="rec"] QLabel { color: @text.onFill; font-size: 11px; font-weight: 700; }

QLabel#sectionTitle { font-size: 13px; font-weight: 600; }
QLabel#sectionCount, QLabel#queueNote { font-size: 12px; color: @text.tertiary; }
QScrollArea#library { background: transparent; border: none; }
QWidget#libraryContents { background: transparent; }
QFrame#row { border-radius: 8px; background-color: transparent; border: none; }
QFrame#row:hover { background-color: @bg.hover; }
QFrame#row[playing="true"] { background-color: @bg.selected; }
QFrame#row QLabel#name { font-size: 14px; font-weight: 600; }
QFrame#row QLabel#duration { font-family: "JetBrains Mono"; font-size: 12px;
    color: @text.secondary; }
QFrame#row QLabel#date { font-size: 12px; color: @text.tertiary; }
QFrame#row QLabel#divider { font-size: 12px; color: @border.default; }
QLabel#details { font-size: 12px; }
QToolButton#playButton { min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px;
    border-radius: 16px;
    border: 1px solid @border.default; background-color: transparent; }
QToolButton#playButton:hover { background-color: @bg.hover; }
QToolButton#playButton:checked { background-color: @accent.fill; border-color: @accent.fill; }
QLabel#emptyTitle { font-size: 17px; font-weight: 600; }
QLabel#emptyText { font-size: 13px; color: @text.secondary; }
QLabel#emptyFormats { font-size: 12px; color: @text.tertiary; }
QLabel#emptyIcon { background-color: @bg.hover; border-radius: 28px; }
QLabel#playerTime { font-family: "JetBrains Mono"; font-size: 12px; }
QLabel#playerTotal { font-family: "JetBrains Mono"; font-size: 12px; color: @text.tertiary; }
QSlider { min-height: 16px; }
QGroupBox QSlider { min-height: 30px; }
QSlider::groove:horizontal { height: 4px; border-radius: 2px; background-color: @border.default; }
QSlider::sub-page:horizontal { border-radius: 2px; background-color: @accent; }
QSlider::handle:horizontal { width: 14px; height: 14px; margin: -5px 0px; border-radius: 7px;
    background-color: #FFFFFF;
    border: 1px solid @border.strong; }
QSlider::handle:horizontal:disabled { background-color: @bg.hover; }

QFrame#livePanel { background-color: @bg.surface; border: 1px solid @border.default;
    border-radius: 12px; }
QFrame#liveHeader { border: none; border-bottom: 1px solid @border.subtle; }
QLabel#liveTitle { font-size: 14px; font-weight: 600; }
QFrame#inProgress { background-color: @bg.raised; border: 1px dashed @border.default;
    border-radius: 8px; }
QFrame#inProgress QLabel#pending { font-size: 15px; font-style: italic; color: @text.secondary; }
QFrame#inProgress QLabel#pendingWho { font-size: 13px; font-weight: 600; }
QTextEdit#liveText { background: transparent; border: none; }
QSplitter::handle:vertical { height: 14px; background: transparent; }

QMenu { background-color: @bg.raised2; border: 1px solid @border.default; border-radius: 8px;
    padding: 4px; }
QMenu::item { min-height: 30px; padding: 0px 14px 0px 10px; border-radius: 6px;
    color: @text.primary; }
QMenu::item:selected { background-color: @bg.hover; }
QMenu::item:disabled { color: @text.disabled; }
QMenu::icon { padding-left: 8px; }
QMenu::separator { height: 1px; margin: 4px 6px; background-color: @border.subtle; }
QToolTip { background-color: @tooltip.bg; color: @tooltip.text; border: none; border-radius: 6px;
    padding: 6px 9px; font-size: 12px; }
QScrollBar:vertical { width: 10px; margin: 2px; background: transparent; }
QScrollBar::handle:vertical { min-height: 32px; border-radius: 3px;
    background-color: @border.default; }
QScrollBar::handle:vertical:hover { background-color: @border.strong; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QLineEdit, QSpinBox { min-height: 30px; max-height: 30px; padding: 0px 10px; border-radius: 6px;
    border: 1px solid @border.default; background-color: @bg.input; color: @text.primary;
    selection-background-color: @accent.fill; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: @accent; }
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled { color: @text.disabled;
    border-color: @border.subtle; }
QLineEdit[readOnly="true"] { font-family: "JetBrains Mono"; font-size: 12px;
    color: @text.secondary; border-color: @border.subtle; }
QSpinBox::up-button, QSpinBox::down-button { width: 20px; border: none; background: transparent; }
QSpinBox::up-arrow { image: url(@icon.chevronUp); width: 12px; height: 12px; }
QSpinBox::down-arrow { image: url(@icon.chevronDown); width: 12px; height: 12px; }
QComboBox { min-height: 30px; max-height: 30px; padding: 0px 8px 0px 10px; border-radius: 6px;
    border: 1px solid @border.default; background-color: @bg.raised2; color: @text.primary; }
QComboBox::drop-down { width: 28px; border: none; }
QComboBox::down-arrow { image: url(@icon.chevronDown); width: 16px; height: 16px; }
QComboBox QAbstractItemView { background-color: @bg.raised2; border: 1px solid @border.default;
    padding: 4px;
    selection-background-color: @bg.hover; selection-color: @text.primary; outline: none; }
QCheckBox { spacing: 10px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid @border.strong; background-color: @bg.input; }
QCheckBox::indicator:hover { border-color: @accent; }
QCheckBox::indicator:checked { background-color: @accent.fill; border-color: @accent.fill;
    image: url(@icon.check); }
QCheckBox::indicator:disabled { border-color: @border.subtle; background-color: transparent; }
QCheckBox:disabled { color: @text.disabled; }
QGroupBox { background-color: @bg.raised; border: 1px solid @border.default; border-radius: 10px;
    margin-top: 0px; padding: 40px 14px 14px 14px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: padding; subcontrol-position: top left; left: 16px;
    top: 14px; color: @text.primary; }
QGroupBox QLabel { font-weight: 400; }
QLabel#help { font-size: 12px; color: @text.tertiary; }
QTabWidget::pane { border: none; border-top: 1px solid @border.default; }
QTabBar::tab { min-height: 38px; padding: 0px 12px; border: none;
    border-bottom: 2px solid transparent;
    background: transparent; color: @text.secondary; font-weight: 500; }
QTabBar::tab:hover { color: @text.primary; }
QTabBar::tab:selected { color: @text.primary; font-weight: 600; border-bottom-color: @accent; }
QScrollArea#settingsPage { background: transparent; border: none; }
QWidget#settingsContents { background: transparent; }
QPlainTextEdit, QTextEdit { background-color: @bg.input; border: 1px solid @border.default;
    border-radius: 6px; }
"""


class _Notifier(QObject):
    changed = pyqtSignal()


notifier = _Notifier()
_state = {"name": "dark", "fonts": False}


def name():
    return _state["name"]


def token(key):
    return TOKENS[_state["name"]][key]


_RGBA = re.compile(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)")


def color(key):
    value = token(key)
    match = _RGBA.fullmatch(value)
    if match:
        r, g, b, a = match.groups()
        return QColor(int(r), int(g), int(b), round(float(a) * 255))
    return QColor(value)


def solid(key):
    """The token as #RRGGBB, for SVG tinting where alpha is not available."""
    return color(key).name()


def apply_tokens(template, tokens):
    for key in sorted(tokens, key=len, reverse=True):
        template = template.replace("@" + key, tokens[key])
    return template


def stylesheet(theme_name):
    tokens = dict(TOKENS[theme_name])
    folder = Path(tempfile.gettempdir()) / "meeting-recorder-theme"
    folder.mkdir(parents=True, exist_ok=True)
    for key, glyph, tint in (
        ("icon.check", "check", tokens["text.onFill"]),
        ("icon.chevronDown", "chevron-down", tokens["text.secondary"]),
        ("icon.chevronUp", "chevron-up", tokens["text.secondary"]),
    ):
        path = folder / f"{glyph}-{theme_name}.svg"
        path.write_text(icons.svg(glyph, tint, 2.5 if glyph == "check" else 2), encoding="utf-8")
        tokens[key] = path.as_posix()
    return apply_tokens(QSS, tokens)


def _palette():
    palette = QPalette()
    roles = QPalette.ColorRole
    for role, key in (
        (roles.Window, "bg.window"),
        (roles.WindowText, "text.primary"),
        (roles.Base, "bg.input"),
        (roles.AlternateBase, "bg.raised"),
        (roles.Text, "text.primary"),
        (roles.Button, "bg.raised2"),
        (roles.ButtonText, "text.primary"),
        (roles.Highlight, "accent.fill"),
        (roles.HighlightedText, "text.onFill"),
        (roles.ToolTipBase, "tooltip.bg"),
        (roles.ToolTipText, "tooltip.text"),
        (roles.Link, "accent"),
        (roles.PlaceholderText, "text.tertiary"),
    ):
        palette.setColor(role, color(key))
    palette.setColor(QPalette.ColorGroup.Disabled, roles.Text, color("text.disabled"))
    palette.setColor(QPalette.ColorGroup.Disabled, roles.ButtonText, color("text.disabled"))
    palette.setColor(QPalette.ColorGroup.Disabled, roles.WindowText, color("text.disabled"))
    return palette


def _load_fonts():
    if _state["fonts"]:
        return
    _state["fonts"] = True
    for font in sorted(FONT_DIR.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(font))


def _scheme_name(scheme):
    return "light" if scheme == Qt.ColorScheme.Light else "dark"


def set_theme(app, theme_name):
    _state["name"] = theme_name
    app.setPalette(_palette())
    app.setStyleSheet(stylesheet(theme_name))
    notifier.changed.emit()


def apply(app):
    _load_fonts()
    hints = app.styleHints()
    set_theme(app, _scheme_name(hints.colorScheme()))
    hints.colorSchemeChanged.connect(lambda scheme: set_theme(app, _scheme_name(scheme)))

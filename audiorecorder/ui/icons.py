"""Lucide icons, tinted per state when they are drawn.

The path data comes from Lucide (https://lucide.dev), ISC licence:
Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of Feather
(MIT). All other copyright (c) for Lucide are held by Lucide Contributors 2022.
Permission to use, copy, modify, and/or distribute this software for any purpose with or
without fee is hereby granted, provided that the above copyright notice and this
permission notice appear in all copies.
"""

from PyQt6.QtCore import QByteArray, QRectF, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication

PATHS = {
    "circle": "M22 12a10 10 0 1 1-20 0a10 10 0 1 1 20 0",
    "square": "M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z",
    "play": "M6 3l14 9-14 9Z",
    "mic": "M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z M19 10v2a7 7 0 0 1-14 0v-2 "
           "M12 19v3",
    "mic-off": "M2 2l20 20 M18.89 13.23A7 7 0 0 0 19 12v-2 M5 10v2a7 7 0 0 0 12 5 "
               "M15 9.34V5a3 3 0 0 0-5.68-1.33 M9 9v3a3 3 0 0 0 5.12 2.12 M12 19v3",
    "volume-2": "M11 4.7a.7.7 0 0 0-1.2-.5L6.4 7.6A1.4 1.4 0 0 1 5.4 8H3a1 1 0 0 0-1 1v6a1 1 0 0 "
                "0 1 1h2.4a1.4 1.4 0 0 1 1 .4l3.4 3.4a.7.7 0 0 0 1.2-.5Z M16 9a5 5 0 0 1 0 6 "
                "M19.4 18.4a9 9 0 0 0 0-12.7",
    "keyboard": "M4 4h16a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2Z M6 8h.01 "
                "M10 8h.01 M14 8h.01 M18 8h.01 M8 12h.01 M12 12h.01 M16 12h.01 M7 16h10",
    "languages": "M5 8l6 6 M4 14l6-6 2-3 M2 5h12 M7 2h1 M22 22l-5-10-5 10 M14 18h6",
    "users": "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2 M13 7a4 4 0 1 1-8 0a4 4 0 1 1 8 0 "
             "M22 21v-2a4 4 0 0 0-3-3.87 M16 3.13a4 4 0 0 1 0 7.75",
    "radio": "M4.9 19.1C1 15.2 1 8.8 4.9 4.9 M7.8 16.2c-2.3-2.3-2.3-6.1 0-8.5 "
             "M14 12a2 2 0 1 1-4 0a2 2 0 1 1 4 0 M16.2 7.8c2.3 2.3 2.3 6.1 0 8.5 "
             "M19.1 4.9C23 8.8 23 15.1 19.1 19.1",
    "file-plus": "M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z "
                 "M14 2v4a2 2 0 0 0 2 2h4 M9 15h6 M12 18v-6",
    "settings": "M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 "
                "2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 "
                "1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 "
                "2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 "
                "1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-"
                "2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l"
                "-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 "
                "2 0 0 0-2-2Z M15 12a3 3 0 1 1-6 0a3 3 0 1 1 6 0",
    "file-text": "M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z "
                 "M14 2v4a2 2 0 0 0 2 2h4 M10 9H8 M16 13H8 M16 17H8",
    "audio-lines": "M2 10v3 M6 6v11 M10 3v18 M14 8v7 M18 5v13 M22 10v3",
    "sparkles": "M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.13-1.58a.5.5 0 0 1 0-.96L8.5 9.94A2 2 0 0 0 "
                "9.94 8.5l1.58-6.13a.5.5 0 0 1 .96 0L14.06 8.5A2 2 0 0 0 15.5 9.94l6.13 1.58a.5"
                ".5 0 0 1 0 .96L15.5 14.06a2 2 0 0 0-1.44 1.44l-1.58 6.13a.5.5 0 0 1-.96 0Z "
                "M20 3v4 M22 5h-4 M4 17v2 M5 18H3",
    "captions": "M5 5h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2Z "
                "M7 15h4 M15 15h2 M7 11h2 M13 11h4",
    "ellipsis": "M13 12a1 1 0 1 1-2 0a1 1 0 1 1 2 0 M20 12a1 1 0 1 1-2 0a1 1 0 1 1 2 0 "
                "M6 12a1 1 0 1 1-2 0a1 1 0 1 1 2 0",
    "folder-input": "M2 9V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H20a2 2 0 "
                    "0 1 2 2v10a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-1 M2 13h10 M9 16l3-3-3-3",
    "trash-2": "M3 6h18 M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6 M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2 "
               "M10 11v6 M14 11v6",
    "rotate-ccw": "M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8 M3 3v5h5",
    "check": "M20 6L9 17l-5-5",
    "loader-circle": "M21 12a9 9 0 1 1-6.22-8.56",
    "clock": "M22 12a10 10 0 1 1-20 0a10 10 0 1 1 20 0 M12 6v6l4 2",
    "circle-dashed": "M10.1 2.18a9.93 9.93 0 0 1 3.8 0 M17.6 3.71a9.95 9.95 0 0 1 2.69 2.7 "
                     "M21.82 10.1a9.93 9.93 0 0 1 0 3.8 M20.29 17.6a9.95 9.95 0 0 1-2.7 2.69 "
                     "M13.9 21.82a9.94 9.94 0 0 1-3.8 0 M6.4 20.29a9.95 9.95 0 0 1-2.69-2.7 "
                     "M2.18 13.9a9.93 9.93 0 0 1 0-3.8 M3.71 6.4a9.95 9.95 0 0 1 2.7-2.69",
    "circle-pause": "M22 12a10 10 0 1 1-20 0a10 10 0 1 1 20 0 M10 15V9 M14 15V9",
    "circle-alert": "M22 12a10 10 0 1 1-20 0a10 10 0 1 1 20 0 M12 8v4 M12 16h.01",
    "triangle-alert": "M21.73 18l-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 "
                      "1.73-3 M12 9v4 M12 17h.01",
    "info": "M22 12a10 10 0 1 1-20 0a10 10 0 1 1 20 0 M12 16v-4 M12 8h.01",
    "lock": "M5 11h14a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2Z "
            "M7 11V7a5 5 0 0 1 10 0v4",
    "user-pen": "M11.5 15H7a4 4 0 0 0-4 4v2 M21.38 16.62a1 1 0 0 0-3-3l-4.01 4.02a2 2 0 0 0-.51"
                ".86l-.84 2.87a.5.5 0 0 0 .62.62l2.87-.84a2 2 0 0 0 .86-.51Z "
                "M14 7a4 4 0 1 1-8 0a4 4 0 1 1 8 0",
    "video": "M16 13l5.22 3.48a.5.5 0 0 0 .78-.42V7.87a.5.5 0 0 0-.75-.43L16 10.5 "
             "M4 6h10a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2Z",
    "x": "M18 6L6 18 M6 6l12 12",
    "download": "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4 M7 10l5 5 5-5 M12 15V3",
    "refresh-cw": "M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8 M21 3v5h-5 "
                  "M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16 M8 16H3v5",
    "copy": "M10 8h10a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H10a2 2 0 0 1-2-2V10a2 2 0 0 1 2-2Z "
            "M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2",
    "chevron-down": "M6 9l6 6 6-6",
    "chevron-up": "M18 15l-6-6-6 6",
}

FILLED = frozenset({"circle", "square", "play"})


def svg(name, color, stroke_width=2):
    fill = color if name in FILLED else "none"
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'fill="{fill}" stroke="{color}" stroke-width="{stroke_width}" '
        'stroke-linecap="round" stroke-linejoin="round">'
        f'<path d="{PATHS[name]}"/></svg>'
    )


def pixmap(name, color, size=16, stroke_width=2):
    """A crisp pixmap at the screen's device pixel ratio."""
    app = QApplication.instance()
    ratio = app.devicePixelRatio() if app is not None else 1.0
    ratio = max(ratio, 2.0)
    side = int(size * ratio)
    result = QPixmap(side, side)
    result.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg(name, color, stroke_width).encode()))
    painter = QPainter(result)
    renderer.render(painter, QRectF(0, 0, side, side))
    painter.end()
    result.setDevicePixelRatio(ratio)
    return result


def icon(name, color, size=16, stroke_width=2):
    return QIcon(pixmap(name, color, size, stroke_width))

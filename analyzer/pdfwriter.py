"""Un PDF simple, sans dépendance : pages A4, texte en Helvetica (accents compris), symboles des couleurs des cartes
(ZapfDingbats), rectangles (arrondis ou non), traits et courbes.

Les polices sont trois des quatorze polices standard que tout lecteur PDF connaît : rien à embarquer, le fichier
reste léger. Le texte s'écrit en WinAnsi (cp1252 : le français y est tout entier) ; la largeur de chaque caractère
(métriques d'Helvetica, en millièmes de la taille) sert à aligner et à couper les lignes. Les coordonnées se donnent
depuis le haut de la page, en points (1/72 de pouce).
"""
from __future__ import annotations

import zlib
from datetime import datetime
from typing import Optional, Sequence

from . import NAME

A4 = (595.28, 841.89)
Color = tuple[float, float, float]

# Largeurs des caractères 32 à 255 (cp1252), en millièmes de la taille du texte.
_REGULAR = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556,
    556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833,
    722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556,
    556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500, 334,
    260, 334, 584, 750, 556, 0, 222, 556, 333, 1000, 556, 556, 333, 1000, 667, 333, 1000, 0, 611, 0, 0, 222, 222, 333,
    333, 350, 556, 1000, 333, 1000, 500, 333, 944, 0, 500, 667, 278, 333, 556, 556, 556, 556, 260, 556, 333, 737, 370,
    556, 584, 333, 737, 333, 400, 584, 333, 333, 333, 556, 537, 278, 333, 333, 365, 556, 834, 834, 834, 611, 667, 667,
    667, 667, 667, 667, 1000, 722, 667, 667, 667, 667, 278, 278, 278, 278, 722, 722, 778, 778, 778, 778, 778, 584, 778,
    722, 722, 722, 722, 667, 667, 611, 556, 556, 556, 556, 556, 556, 889, 500, 556, 556, 556, 556, 278, 278, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 584, 611, 556, 556, 556, 556, 500, 556, 500,
)
_BOLD = (
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556, 556,
    556, 556, 556, 333, 333, 584, 584, 584, 611, 975, 722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833,
    722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 333, 278, 333, 584, 556, 333, 556, 611, 556, 611,
    556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611, 611, 389, 556, 333, 611, 556, 778, 556, 556, 500, 389,
    280, 389, 584, 750, 556, 0, 278, 556, 500, 1000, 556, 556, 333, 1000, 667, 333, 1000, 0, 611, 0, 0, 278, 278, 500,
    500, 350, 556, 1000, 333, 1000, 556, 333, 944, 0, 500, 667, 278, 333, 556, 556, 556, 556, 280, 556, 333, 737, 370,
    556, 584, 333, 737, 333, 400, 584, 333, 333, 333, 611, 556, 278, 333, 333, 365, 556, 834, 834, 834, 611, 722, 722,
    722, 722, 722, 722, 1000, 722, 667, 667, 667, 667, 278, 278, 278, 278, 722, 722, 778, 778, 778, 778, 778, 584, 778,
    722, 722, 722, 722, 667, 667, 611, 556, 556, 556, 556, 556, 556, 889, 556, 556, 556, 556, 556, 278, 278, 278, 278,
    611, 611, 611, 611, 611, 611, 611, 584, 611, 611, 611, 611, 611, 556, 611, 556,
)
FONTS = {"regular": ("F1", "Helvetica", _REGULAR), "bold": ("F2", "Helvetica-Bold", _BOLD)}
# Les couleurs des cartes dans ZapfDingbats : (code, largeur)
SUITS = {"c": (0xA8, 776), "d": (0xA9, 595), "h": (0xAA, 694), "s": (0xAB, 626)}
# Ce que WinAnsi n'a pas, remplacé par son plus proche
_REPLACE = str.maketrans({
    "\u202f": "\xa0", "\u2009": " ", "\u2007": " ", "\u00a0": "\xa0",  # espaces fines -> insécable
    "\u2212": "–", "\u2010": "-", "\u2011": "-",  # signe moins -> tiret demi-cadratin
    "\u2192": "\u203a", "\u2197": "\u203a", "\u2190": "\u2039",  # flèches -> chevrons
    "\u2265": ">=", "\u2264": "<=", "\u2248": "~", "\u2713": "v", "\u25b2": "^", "\u25bc": "v",
})


def rgb(hex_color: str) -> Color:
    """« #2a78d6 » -> (0.16, 0.47, 0.84)."""
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def encode(text: str) -> bytes:
    """Le texte en WinAnsi (un caractère sans équivalent devient « ? »)."""
    return text.translate(_REPLACE).encode("cp1252", errors="replace")


def text_width(text: str, font: str = "regular", size: float = 10.0) -> float:
    widths = FONTS[font][2]
    return sum(widths[b - 32] if b >= 32 else 0 for b in encode(text)) * size / 1000


def _cut(word: str, width: float, font: str, size: float) -> list[str]:
    """Un mot plus long que la ligne, coupé où il faut."""
    parts, current = [], ""
    for ch in word:
        if current and text_width(current + ch, font, size) > width:
            parts.append(current)
            current = ""
        current += ch
    return parts + [current]


def wrap(text: str, width: float, font: str = "regular", size: float = 10.0) -> list[str]:
    """Le texte coupé en lignes d'au plus width points (aux espaces ; un saut de ligne force une nouvelle ligne)."""
    lines: list[str] = []
    for paragraph in text.split("\n"):
        line = ""
        for word in paragraph.split(" "):
            candidate = f"{line} {word}" if line else word
            if text_width(candidate, font, size) <= width:
                line = candidate
                continue
            if line:
                lines.append(line)
            pieces = _cut(word, width, font, size)
            lines.extend(pieces[:-1])
            line = pieces[-1]
        lines.append(line)
    return lines


def _literal(data: bytes) -> bytes:
    """Une chaîne PDF entre parenthèses (échappements compris)."""
    out = bytearray(b"(")
    for b in data:
        if b in (0x28, 0x29, 0x5C):
            out += b"\\" + bytes([b])
        elif 32 <= b < 127:
            out.append(b)
        else:
            out += b"\\%03o" % b
    return bytes(out + b")")


def _num(x: float) -> bytes:
    text = f"{x:.2f}".rstrip("0").rstrip(".")
    return (text if text not in ("-0", "") else "0").encode()


def _color(c: Color) -> bytes:
    return b" ".join(_num(v) for v in c)


class Page:
    """Une page : ce qu'on y dessine, depuis le haut à gauche."""

    def __init__(self, size: tuple[float, float] = A4):
        self.width, self.height = size
        self._ops: list[bytes] = []

    def _y(self, y: float) -> bytes:
        return _num(self.height - y)

    def text(self, x: float, y: float, text: str, font: str = "regular", size: float = 10.0,
             color: Color = (0, 0, 0), align: str = "left") -> float:
        """Écrit une ligne (y : sa ligne de base) ; renvoie sa largeur."""
        width = text_width(text, font, size)
        if align == "right":
            x -= width
        elif align == "center":
            x -= width / 2
        self._ops.append(b"BT /%s %s Tf %s rg %s %s Td %s Tj ET" % (
            FONTS[font][0].encode(), _num(size), _color(color), _num(x), self._y(y), _literal(encode(text))))
        return width

    def suit(self, x: float, y: float, suit: str, size: float, color: Color) -> float:
        """Le symbole d'une couleur (« s », « h », « d », « c ») ; renvoie sa largeur."""
        code, width = SUITS[suit]
        self._ops.append(b"BT /F3 %s Tf %s rg %s %s Td %s Tj ET" % (
            _num(size), _color(color), _num(x), self._y(y), _literal(bytes([code]))))
        return width * size / 1000

    def rect(self, x: float, y: float, w: float, h: float, fill: Optional[Color] = None,
             stroke: Optional[Color] = None, line_width: float = 0.75, radius: float = 0.0) -> None:
        """Un rectangle (x, y : son coin en haut à gauche), plein, tracé ou les deux ; radius : coins arrondis."""
        if fill is None and stroke is None:
            return
        ops = []
        if fill is not None:
            ops.append(_color(fill) + b" rg")
        if stroke is not None:
            ops.append(_color(stroke) + b" RG " + _num(line_width) + b" w")
        top, bottom = self.height - y, self.height - y - h
        if radius <= 0:
            ops.append(b"%s %s %s %s re" % (_num(x), _num(bottom), _num(w), _num(h)))
        else:
            r = min(radius, w / 2, h / 2)
            k = r * 0.5523  # bras des courbes de Bézier d'un quart de cercle
            pts = [
                (b"m", (x + r, top)), (b"l", (x + w - r, top)),
                (b"c", (x + w - r + k, top, x + w, top - r + k, x + w, top - r)), (b"l", (x + w, bottom + r)),
                (b"c", (x + w, bottom + r - k, x + w - r + k, bottom, x + w - r, bottom)), (b"l", (x + r, bottom)),
                (b"c", (x + r - k, bottom, x, bottom + r - k, x, bottom + r)), (b"l", (x, top - r)),
                (b"c", (x, top - r + k, x + r - k, top, x + r, top)),
            ]
            ops.extend(b" ".join(_num(v) for v in values) + b" " + op for op, values in pts)
            ops.append(b"h")
        ops.append(b"B" if fill is not None and stroke is not None else b"f" if fill is not None else b"S")
        self._ops.append(b"q " + b" ".join(ops) + b" Q")

    def line(self, x1: float, y1: float, x2: float, y2: float, color: Color = (0, 0, 0), width: float = 0.75,
             dash: Optional[tuple[float, float]] = None) -> None:
        self.polyline([(x1, y1), (x2, y2)], color, width, dash)

    def polyline(self, points: Sequence[tuple[float, float]], color: Color = (0, 0, 0), width: float = 1.0,
                 dash: Optional[tuple[float, float]] = None) -> None:
        if len(points) < 2:
            return
        path = [b"%s %s m" % (_num(points[0][0]), self._y(points[0][1]))]
        path += [b"%s %s l" % (_num(x), self._y(y)) for x, y in points[1:]]
        style = b"[%s %s] 0 d " % (_num(dash[0]), _num(dash[1])) if dash else b""
        self._ops.append(b"q %s RG %s w 1 j 1 J %s%s S Q" % (_color(color), _num(width), style, b" ".join(path)))

    def content(self) -> bytes:
        return b"\n".join(self._ops)


def _info_text(text: str) -> bytes:
    """Une chaîne du dictionnaire d'informations (UTF-16 pour les accents)."""
    return b"<FEFF" + text.encode("utf-16-be").hex().upper().encode() + b">"


class Document:
    """Les pages d'un PDF, et le fichier."""

    def __init__(self, title: str = "", author: str = NAME, subject: str = "",
                 size: tuple[float, float] = A4):
        self.title, self.author, self.subject, self.size = title, author, subject, size
        self.pages: list[Page] = []

    def add_page(self) -> Page:
        page = Page(self.size)
        self.pages.append(page)
        return page

    def output(self, created: Optional[datetime] = None) -> bytes:
        created = created or datetime.now()
        objects: list[bytes] = []

        def add(body: bytes) -> int:
            objects.append(body)
            return len(objects)

        catalog = add(b"")  # rempli à la fin (il nomme l'arbre des pages)
        pages_id = add(b"")
        fonts = {
            "F1": add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"),
            "F2": add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"),
            "F3": add(b"<< /Type /Font /Subtype /Type1 /BaseFont /ZapfDingbats >>"),
        }
        resources = b"<< /Font << " + b" ".join(b"/%s %d 0 R" % (k.encode(), v) for k, v in fonts.items()) + b" >> >>"
        kids = []
        for page in self.pages:
            data = zlib.compress(page.content(), 9)
            stream = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(data) + data + b"\nendstream")
            kids.append(add(b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %s %s] /Resources %s /Contents %d 0 R >>" % (
                pages_id, _num(self.size[0]), _num(self.size[1]), resources, stream)))
        objects[pages_id - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
            b" ".join(b"%d 0 R" % k for k in kids), len(kids))
        objects[catalog - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id
        stamp = created.strftime("D:%Y%m%d%H%M%S").encode()
        info = add(b"<< /Title %s /Author %s /Subject %s /Producer %s /Creator %s /CreationDate (%s) >>" % (
            _info_text(self.title), _info_text(self.author), _info_text(self.subject), _info_text(NAME),
            _info_text(NAME), stamp))
        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objects, start=1):
            offsets.append(len(out))
            out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
        xref = len(out)
        out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
        out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
        out += b"trailer\n<< /Size %d /Root %d 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
            len(objects) + 1, catalog, info, xref)
        return bytes(out)

"""Une présentation PowerPoint (.pptx), sans dépendance : diapositives 16:9, zones de texte, formes (rectangles,
rectangles arrondis, cercles), traits, tracés libres (courbes), tableaux et notes du présentateur.

Le fichier est un paquet OOXML (une archive zip de fichiers XML) : thème, masque et disposition vierge, puis une
diapositive par page, chacune avec sa page de notes. Les positions et tailles se donnent en pouces (la diapositive
fait 13,333 × 7,5), les tailles de texte en points, les couleurs en hexadécimal (« 0F3D2E »). La police est Arial :
ses largeurs sont celles d'Helvetica (pdfwriter.text_width), de quoi couper les lignes avant d'écrire.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import BytesIO
from typing import Optional, Sequence
from xml.sax.saxutils import escape

from . import NAME, pdfwriter

EMU = 914400  # par pouce
SLIDE_W, SLIDE_H = 13.333, 7.5
FONT = "Arial"

NS = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
XML = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CT = "application/vnd.openxmlformats-officedocument.presentationml"


def emu(inches: float) -> int:
    return int(round(inches * EMU))


def attr(text: str) -> str:
    return escape(text, {'"': "&quot;"})


def width(text: str, size: float, bold: bool = False) -> float:
    """La largeur d'un texte en Arial, en pouces."""
    return pdfwriter.text_width(text, "bold" if bold else "regular", size) / 72


def wrap(text: str, box_width: float, size: float, bold: bool = False) -> list[str]:
    """Le texte coupé en lignes qui tiennent dans box_width pouces."""
    return pdfwriter.wrap(text, box_width * 72, "bold" if bold else "regular", size)


def line_height(size: float) -> float:
    """La hauteur d'une ligne de texte (interligne simple d'Arial, avec un peu de marge), en pouces."""
    return size * 1.2 / 72


@dataclass
class Run:
    text: str
    size: float = 16
    color: str = "1B1B1B"
    bold: bool = False
    italic: bool = False

    def xml(self) -> str:
        flags = (' b="1"' if self.bold else "") + (' i="1"' if self.italic else "")
        return (f'<a:r><a:rPr lang="fr-FR" sz="{int(round(self.size * 100))}"{flags} dirty="0">'
                f'<a:solidFill><a:srgbClr val="{self.color}"/></a:solidFill>'
                f'<a:latin typeface="{FONT}"/><a:cs typeface="{FONT}"/></a:rPr><a:t>{escape(self.text)}</a:t></a:r>')


@dataclass
class Para:
    runs: list[Run]
    align: str = "l"          # l, ctr, r
    after: float = 0          # espace après, en points
    line: Optional[float] = None  # interligne en pourcentage (100 : simple)

    def xml(self) -> str:
        spacing = f'<a:lnSpc><a:spcPct val="{int(self.line * 1000)}"/></a:lnSpc>' if self.line else ""
        after = f'<a:spcAft><a:spcPts val="{int(self.after * 100)}"/></a:spcAft>' if self.after else ""
        size = int(round((self.runs[0].size if self.runs else 12) * 100))
        body = "".join(r.xml() for r in self.runs if r.text)
        return (f'<a:p><a:pPr algn="{self.align}">{spacing}{after}<a:buNone/></a:pPr>{body}'
                f'<a:endParaRPr lang="fr-FR" sz="{size}" dirty="0"/></a:p>')


def para(text: str, size: float = 16, color: str = "1B1B1B", bold: bool = False, align: str = "l",
         after: float = 0) -> Para:
    return Para([Run(text, size, color, bold)], align, after)


def _body(paras: Sequence[Para], anchor: str = "t", inset: float = 0.0, wrap_text: bool = True) -> str:
    ins = emu(inset)
    return (f'<p:txBody><a:bodyPr wrap="{"square" if wrap_text else "none"}" lIns="{ins}" tIns="{ins}" rIns="{ins}" '
            f'bIns="{ins}" anchor="{anchor}" rtlCol="0"><a:noAutofit/></a:bodyPr><a:lstStyle/>'
            + "".join(p.xml() for p in paras) + "</p:txBody>")


def _fill(color: Optional[str]) -> str:
    return f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>' if color else "<a:noFill/>"


def _line(color: Optional[str], width_pt: float = 1.0, dash: Optional[str] = None) -> str:
    if not color:
        return "<a:ln><a:noFill/></a:ln>"
    style = f'<a:prstDash val="{dash}"/>' if dash else ""
    return (f'<a:ln w="{int(width_pt * 12700)}" cap="rnd"><a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
            f'{style}<a:round/></a:ln>')


def _xfrm(x: float, y: float, w: float, h: float) -> str:
    return f'<a:xfrm><a:off x="{emu(x)}" y="{emu(y)}"/><a:ext cx="{max(emu(w), 1)}" cy="{max(emu(h), 1)}"/></a:xfrm>'


@dataclass
class Cell:
    """Une case de tableau : ses paragraphes et son fond."""
    paras: list[Para]
    fill: Optional[str] = None
    align_right: bool = False


@dataclass
class Slide:
    background: Optional[str] = None
    notes: str = ""
    shapes: list[str] = field(default_factory=list)

    def _id(self) -> int:
        return len(self.shapes) + 2  # 1 : le groupe racine

    def text(self, x: float, y: float, w: float, h: float, paras: Sequence[Para], anchor: str = "t",
             name: str = "Texte") -> None:
        """Une zone de texte, sans marge intérieure (le texte commence exactement en x)."""
        sid = self._id()
        self.shapes.append(
            f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{attr(name)} {sid}"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr>'
            f'<p:spPr>{_xfrm(x, y, w, h)}<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr>'
            f'{_body(paras, anchor)}</p:sp>')

    def shape(self, x: float, y: float, w: float, h: float, fill: Optional[str] = None, line: Optional[str] = None,
              line_width: float = 1.0, geom: str = "rect", radius: float = 0.12, paras: Sequence[Para] = (),
              anchor: str = "ctr", inset: float = 0.08, name: str = "Forme") -> None:
        """Une forme (« rect », « roundRect », « ellipse »), pleine ou tracée, avec du texte si besoin ; radius : le
        rayon des coins arrondis, en pouces."""
        sid = self._id()
        adjust = (f'<a:gd name="adj" fmla="val {int(min(50000, radius / max(min(w, h), 0.01) * 100000))}"/>'
                  if geom == "roundRect" else "")
        self.shapes.append(
            f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{attr(name)} {sid}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            f'<p:spPr>{_xfrm(x, y, w, h)}<a:prstGeom prst="{geom}"><a:avLst>{adjust}</a:avLst></a:prstGeom>'
            f'{_fill(fill)}{_line(line, line_width)}</p:spPr>'
            + _body(paras or [Para([])], anchor, inset) + "</p:sp>")

    def line(self, x1: float, y1: float, x2: float, y2: float, color: str, width_pt: float = 1.0,
             dash: Optional[str] = None, name: str = "Trait") -> None:
        """Un trait (horizontal, vertical ou oblique)."""
        sid = self._id()
        x, y = min(x1, x2), min(y1, y2)
        flip = (' flipV="1"' if (y2 < y1) != (x2 < x1) and x1 != x2 and y1 != y2 else "")
        self.shapes.append(
            f'<p:cxnSp><p:nvCxnSpPr><p:cNvPr id="{sid}" name="{attr(name)} {sid}"/><p:cNvCxnSpPr/><p:nvPr/></p:nvCxnSpPr>'
            f'<p:spPr><a:xfrm{flip}><a:off x="{emu(x)}" y="{emu(y)}"/><a:ext cx="{emu(abs(x2 - x1))}" '
            f'cy="{emu(abs(y2 - y1))}"/></a:xfrm><a:prstGeom prst="line"><a:avLst/></a:prstGeom>'
            f'{_line(color, width_pt, dash)}</p:spPr></p:cxnSp>')

    def path(self, x: float, y: float, w: float, h: float, points: Sequence[tuple[float, float]], color: str,
             width_pt: float = 2.0, name: str = "Courbe") -> None:
        """Un tracé libre (une courbe) dans la boîte (x, y, w, h) : points en fractions de la boîte (0 à 1, depuis le
        haut à gauche)."""
        if len(points) < 2:
            return
        sid = self._id()
        scale = 100000
        pts = [(int(round(px * scale)), int(round(py * scale))) for px, py in points]
        steps = f'<a:moveTo><a:pt x="{pts[0][0]}" y="{pts[0][1]}"/></a:moveTo>' + "".join(
            f'<a:lnTo><a:pt x="{px}" y="{py}"/></a:lnTo>' for px, py in pts[1:])
        self.shapes.append(
            f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{attr(name)} {sid}"/><p:cNvSpPr/><p:nvPr/></p:nvSpPr>'
            f'<p:spPr>{_xfrm(x, y, w, h)}<a:custGeom><a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>'
            f'<a:rect l="0" t="0" r="r" b="b"/><a:pathLst><a:path w="{scale}" h="{scale}" fill="none">{steps}'
            f'</a:path></a:pathLst></a:custGeom><a:noFill/>{_line(color, width_pt)}</p:spPr>'
            f'{_body([Para([])])}</p:sp>')

    def table(self, x: float, y: float, widths: Sequence[float], rows: Sequence[Sequence[Cell]],
              heights: Sequence[float], border: str = "D9DED9", name: str = "Tableau") -> None:
        """Un tableau : widths, les largeurs des colonnes ; heights, la hauteur de chaque ligne (en pouces)."""
        sid = self._id()
        edge = lambda tag: f'<a:{tag} w="9525"><a:solidFill><a:srgbClr val="{border}"/></a:solidFill></a:{tag}>'  # noqa: E731
        none = lambda tag: f"<a:{tag}><a:noFill/></a:{tag}>"  # noqa: E731
        grid = "".join(f'<a:gridCol w="{emu(w)}"/>' for w in widths)
        body = []
        for row, h in zip(rows, heights):
            cells = []
            for cell in row:
                margins = f'marL="{emu(0.08)}" marR="{emu(0.08)}" marT="{emu(0.05)}" marB="{emu(0.05)}"'
                cells.append(
                    f'<a:tc><a:txBody><a:bodyPr/><a:lstStyle/>{"".join(p.xml() for p in cell.paras)}</a:txBody>'
                    f'<a:tcPr {margins} anchor="ctr">{none("lnL")}{none("lnR")}{none("lnT")}{edge("lnB")}'
                    f'{_fill(cell.fill)}</a:tcPr></a:tc>')
            body.append(f'<a:tr h="{emu(h)}">{"".join(cells)}</a:tr>')
        self.shapes.append(
            f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{sid}" name="{attr(name)} {sid}"/>'
            f'<p:cNvGraphicFramePr><a:graphicFrameLocks noGrp="1"/></p:cNvGraphicFramePr><p:nvPr/></p:nvGraphicFramePr>'
            f'<p:xfrm><a:off x="{emu(x)}" y="{emu(y)}"/><a:ext cx="{emu(sum(widths))}" cy="{emu(sum(heights))}"/></p:xfrm>'
            f'<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl>'
            f'<a:tblPr firstRow="1" bandRow="0"/><a:tblGrid>{grid}</a:tblGrid>{"".join(body)}</a:tbl></a:graphicData>'
            f'</a:graphic></p:graphicFrame>')

    def xml(self) -> str:
        bg = (f'<p:bg><p:bgPr><a:solidFill><a:srgbClr val="{self.background}"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>'
              if self.background else "")
        return (f'{XML}<p:sld {NS}><p:cSld>{bg}<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/>'
                f'</p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/>'
                f'<a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>{"".join(self.shapes)}</p:spTree></p:cSld>'
                f'<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>')


# --- les parties fixes du paquet ---------------------------------------------------------------------------------

def _theme(name: str, colors: dict[str, str]) -> str:
    def clr(key: str) -> str:
        if key in ("dk1", "lt1"):
            sys = "windowText" if key == "dk1" else "window"
            return f'<a:{key}><a:sysClr val="{sys}" lastClr="{colors[key]}"/></a:{key}>'
        return f'<a:{key}><a:srgbClr val="{colors[key]}"/></a:{key}>'
    order = ("dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6", "hlink",
             "folHlink")
    fonts = (f'<a:latin typeface="{FONT}"/><a:ea typeface=""/><a:cs typeface=""/>')
    solid = '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    lines = "".join(f'<a:ln w="{w}" cap="flat" cmpd="sng" algn="ctr">{solid}<a:prstDash val="solid"/><a:miter lim="800000"/></a:ln>'
                    for w in (6350, 12700, 19050))
    return (f'{XML}<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="{attr(name)}">'
            f'<a:themeElements><a:clrScheme name="{attr(name)}">{"".join(clr(k) for k in order)}</a:clrScheme>'
            f'<a:fontScheme name="{attr(name)}"><a:majorFont>{fonts}</a:majorFont><a:minorFont>{fonts}</a:minorFont>'
            f'</a:fontScheme><a:fmtScheme name="{attr(name)}"><a:fillStyleLst>{solid * 3}</a:fillStyleLst>'
            f'<a:lnStyleLst>{lines}</a:lnStyleLst><a:effectStyleLst>'
            + "<a:effectStyle><a:effectLst/></a:effectStyle>" * 3
            + f'</a:effectStyleLst><a:bgFillStyleLst>{solid * 3}</a:bgFillStyleLst></a:fmtScheme></a:themeElements>'
            '<a:objectDefaults/><a:extraClrSchemeLst/></a:theme>')


_GROUP = ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm>'
          '<a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>')
_CLRMAP = ('bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" '
           'accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"')


def _level_styles(size: int) -> str:
    return (f'<a:lvl1pPr marL="0" algn="l" defTabSz="914400" rtl="0" eaLnBrk="1" latinLnBrk="0" hangingPunct="1">'
            f'<a:defRPr sz="{size}" kern="1200"><a:solidFill><a:schemeClr val="tx1"/></a:solidFill>'
            '<a:latin typeface="+mn-lt"/><a:ea typeface="+mn-ea"/><a:cs typeface="+mn-cs"/></a:defRPr></a:lvl1pPr>')


def _master() -> str:
    return (f'{XML}<p:sldMaster {NS}><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg>'
            f'<p:spTree>{_GROUP}</p:spTree></p:cSld><p:clrMap {_CLRMAP}/>'
            '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
            f'<p:txStyles><p:titleStyle>{_level_styles(4000)}</p:titleStyle><p:bodyStyle>{_level_styles(1800)}'
            f'</p:bodyStyle><p:otherStyle>{_level_styles(1800)}</p:otherStyle></p:txStyles></p:sldMaster>')


def _layout() -> str:
    return (f'{XML}<p:sldLayout {NS} type="blank" preserve="1"><p:cSld name="Vierge"><p:spTree>{_GROUP}</p:spTree>'
            '</p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>')


def _placeholder(sid: int, name: str, kind: str, idx: int, x: float, y: float, w: float, h: float,
                 extra: str = "", body: str = "") -> str:
    return (f'<p:sp><p:nvSpPr><p:cNvPr id="{sid}" name="{name}"/><p:cNvSpPr><a:spLocks noGrp="1"{extra}/></p:cNvSpPr>'
            f'<p:nvPr><p:ph type="{kind}" idx="{idx}"/></p:nvPr></p:nvSpPr><p:spPr>{_xfrm(x, y, w, h)}'
            f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></p:spPr>{body}</p:sp>')


def _notes_master() -> str:
    image = _placeholder(2, "Image de la diapositive", "sldImg", 2, 0.5, 0.75, 6.5, 3.66,
                         ' noRot="1" noChangeAspect="1"')
    text = _placeholder(3, "Notes", "body", 3, 0.75, 4.63, 6.0, 4.39,
                        body='<p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang="fr-FR"/></a:p></p:txBody>')
    return (f'{XML}<p:notesMaster {NS}><p:cSld><p:bg><p:bgRef idx="1001"><a:schemeClr val="bg1"/></p:bgRef></p:bg>'
            f'<p:spTree>{_GROUP}{image}{text}</p:spTree></p:cSld><p:clrMap {_CLRMAP}/>'
            f'<p:notesStyle>{_level_styles(1200)}</p:notesStyle></p:notesMaster>')


def _notes(text: str) -> str:
    paras = "".join(f'<a:p><a:r><a:rPr lang="fr-FR" dirty="0"/><a:t>{escape(line)}</a:t></a:r></a:p>' if line else
                    '<a:p><a:endParaRPr lang="fr-FR" dirty="0"/></a:p>' for line in (text or "").split("\n"))
    image = _placeholder(2, "Image de la diapositive", "sldImg", 2, 0.5, 0.75, 6.5, 3.66,
                         ' noRot="1" noChangeAspect="1"')
    body = _placeholder(3, "Notes", "body", 3, 0.75, 4.63, 6.0, 4.39,
                        body=f'<p:txBody><a:bodyPr/><a:lstStyle/>{paras}</p:txBody>')
    return (f'{XML}<p:notes {NS}><p:cSld><p:spTree>{_GROUP}{image}{body}</p:spTree></p:cSld>'
            '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>')


def _rels(items: Sequence[tuple[str, str, str]]) -> str:
    body = "".join(f'<Relationship Id="{rid}" Type="{kind}" Target="{attr(target)}"/>' for rid, kind, target in items)
    return f'{XML}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{body}</Relationships>'


@dataclass
class Presentation:
    title: str = ""
    author: str = NAME
    colors: dict = field(default_factory=lambda: {
        "dk1": "1B1B1B", "lt1": "FFFFFF", "dk2": "0F3D2E", "lt2": "EEF3EF", "accent1": "0F6B4A", "accent2": "E0A526",
        "accent3": "D03B3B", "accent4": "2A78D6", "accent5": "1B8A4B", "accent6": "6B7280", "hlink": "2A78D6",
        "folHlink": "6D28D9"})
    slides: list[Slide] = field(default_factory=list)

    def add_slide(self, background: Optional[str] = None) -> Slide:
        slide = Slide(background)
        self.slides.append(slide)
        return slide

    def output(self, created: Optional[datetime] = None) -> bytes:
        created = (created or datetime.now()).astimezone(timezone.utc)
        n = len(self.slides)
        files: dict[str, str] = {}
        overrides = [("/ppt/presentation.xml", f"{CT}.presentation.main+xml"),
                     ("/ppt/slideMasters/slideMaster1.xml", f"{CT}.slideMaster+xml"),
                     ("/ppt/slideLayouts/slideLayout1.xml", f"{CT}.slideLayout+xml"),
                     ("/ppt/notesMasters/notesMaster1.xml", f"{CT}.notesMaster+xml"),
                     ("/ppt/theme/theme1.xml", "application/vnd.openxmlformats-officedocument.theme+xml"),
                     ("/ppt/theme/theme2.xml", "application/vnd.openxmlformats-officedocument.theme+xml"),
                     ("/ppt/presProps.xml", f"{CT}.presProps+xml"), ("/ppt/viewProps.xml", f"{CT}.viewProps+xml"),
                     ("/ppt/tableStyles.xml", f"{CT}.tableStyles+xml"),
                     ("/docProps/core.xml", "application/vnd.openxmlformats-package.core-properties+xml"),
                     ("/docProps/app.xml", "application/vnd.openxmlformats-officedocument.extended-properties+xml")]
        overrides += [(f"/ppt/slides/slide{i}.xml", f"{CT}.slide+xml") for i in range(1, n + 1)]
        overrides += [(f"/ppt/notesSlides/notesSlide{i}.xml", f"{CT}.notesSlide+xml") for i in range(1, n + 1)]
        files["[Content_Types].xml"] = (
            f'{XML}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            + "".join(f'<Override PartName="{p}" ContentType="{c}"/>' for p, c in overrides) + "</Types>")
        files["_rels/.rels"] = _rels([
            ("rId1", f"{REL}/officeDocument", "ppt/presentation.xml"),
            ("rId2", "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties",
             "docProps/core.xml"),
            ("rId3", f"{REL}/extended-properties", "docProps/app.xml")])
        stamp = created.strftime("%Y-%m-%dT%H:%M:%SZ")
        files["docProps/core.xml"] = (
            f'{XML}<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f'<dc:title>{escape(self.title)}</dc:title><dc:creator>{escape(self.author)}</dc:creator>'
            f'<cp:lastModifiedBy>{escape(self.author)}</cp:lastModifiedBy>'
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified></cp:coreProperties>')
        files["docProps/app.xml"] = (
            f'{XML}<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
            f'<Application>{escape(NAME)}</Application><Slides>{n}</Slides><Notes>{n}</Notes>'
            '<PresentationFormat>Grand écran</PresentationFormat></Properties>')
        rels = [("rId1", f"{REL}/slideMaster", "slideMasters/slideMaster1.xml")]
        rels += [(f"rId{i + 1}", f"{REL}/slide", f"slides/slide{i}.xml") for i in range(1, n + 1)]
        rels += [(f"rId{n + 2}", f"{REL}/notesMaster", "notesMasters/notesMaster1.xml"),
                 (f"rId{n + 3}", f"{REL}/presProps", "presProps.xml"), (f"rId{n + 4}", f"{REL}/viewProps", "viewProps.xml"),
                 (f"rId{n + 5}", f"{REL}/theme", "theme/theme1.xml"),
                 (f"rId{n + 6}", f"{REL}/tableStyles", "tableStyles.xml")]
        files["ppt/_rels/presentation.xml.rels"] = _rels(rels)
        slide_ids = "".join(f'<p:sldId id="{255 + i}" r:id="rId{i + 1}"/>' for i in range(1, n + 1))
        files["ppt/presentation.xml"] = (
            f'{XML}<p:presentation {NS} saveSubsetFonts="1"><p:sldMasterIdLst><p:sldMasterId id="2147483648" '
            f'r:id="rId1"/></p:sldMasterIdLst><p:notesMasterIdLst><p:notesMasterId r:id="rId{n + 2}"/>'
            f'</p:notesMasterIdLst><p:sldIdLst>{slide_ids}</p:sldIdLst><p:sldSz cx="{emu(SLIDE_W)}" cy="{emu(SLIDE_H)}"/>'
            '<p:notesSz cx="6858000" cy="9144000"/></p:presentation>')
        files["ppt/presProps.xml"] = f"{XML}<p:presentationPr {NS}/>"
        files["ppt/viewProps.xml"] = (f'{XML}<p:viewPr {NS}><p:normalViewPr><p:restoredLeft sz="15620"/>'
                                      '<p:restoredTop sz="94660"/></p:normalViewPr><p:gridSpacing cx="76200" cy="76200"/>'
                                      '</p:viewPr>')
        files["ppt/tableStyles.xml"] = (f'{XML}<a:tblStyleLst xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/'
                                        'main" def="{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"/>')
        files["ppt/theme/theme1.xml"] = _theme(NAME, self.colors)
        files["ppt/theme/theme2.xml"] = _theme(NAME + " notes", self.colors)
        files["ppt/slideMasters/slideMaster1.xml"] = _master()
        files["ppt/slideMasters/_rels/slideMaster1.xml.rels"] = _rels([
            ("rId1", f"{REL}/slideLayout", "../slideLayouts/slideLayout1.xml"),
            ("rId2", f"{REL}/theme", "../theme/theme1.xml")])
        files["ppt/slideLayouts/slideLayout1.xml"] = _layout()
        files["ppt/slideLayouts/_rels/slideLayout1.xml.rels"] = _rels([
            ("rId1", f"{REL}/slideMaster", "../slideMasters/slideMaster1.xml")])
        files["ppt/notesMasters/notesMaster1.xml"] = _notes_master()
        files["ppt/notesMasters/_rels/notesMaster1.xml.rels"] = _rels([("rId1", f"{REL}/theme", "../theme/theme2.xml")])
        for i, slide in enumerate(self.slides, start=1):
            files[f"ppt/slides/slide{i}.xml"] = slide.xml()
            files[f"ppt/slides/_rels/slide{i}.xml.rels"] = _rels([
                ("rId1", f"{REL}/slideLayout", "../slideLayouts/slideLayout1.xml"),
                ("rId2", f"{REL}/notesSlide", f"../notesSlides/notesSlide{i}.xml")])
            files[f"ppt/notesSlides/notesSlide{i}.xml"] = _notes(slide.notes)
            files[f"ppt/notesSlides/_rels/notesSlide{i}.xml.rels"] = _rels([
                ("rId1", f"{REL}/notesMaster", "../notesMasters/notesMaster1.xml"),
                ("rId2", f"{REL}/slide", f"../slides/slide{i}.xml")])
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("[Content_Types].xml", files.pop("[Content_Types].xml"))  # en premier
            for name, content in files.items():
                archive.writestr(name, content)
        return buffer.getvalue()

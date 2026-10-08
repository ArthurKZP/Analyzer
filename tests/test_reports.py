import http.client
import io
import posixpath
import re
import shutil
import tempfile
import threading
import unittest
import zipfile
import zlib
from datetime import datetime
from html import unescape
from pathlib import Path
from xml.etree import ElementTree

from analyzer import NAME
from analyzer import pdfwriter as pw
from analyzer import pptxwriter as px
from analyzer.app.library import Library
from analyzer.app.server import start

try:
    from .base import IsolatedHome
except ImportError:  # lancé par « unittest discover -s tests »
    from base import IsolatedHome

FIXTURE = Path(__file__).parent / "fixtures" / "betclic_sample.txt"


def _unescape(raw: bytes) -> bytes:
    out, i = bytearray(), 0
    while i < len(raw):
        if raw[i] == 0x5C:  # « \ »
            if raw[i + 1:i + 2].isdigit():
                out.append(int(raw[i + 1:i + 4], 8))
                i += 4
            else:
                out.append(raw[i + 1])
                i += 2
        else:
            out.append(raw[i])
            i += 1
    return bytes(out)


def pdf_strings(data: bytes) -> list[str]:
    """Les textes écrits dans les pages d'un PDF de pdfwriter (flux décompressés, chaînes des opérateurs Tj)."""
    found = []
    for stream in re.findall(rb"stream\n(.*?)\nendstream", data, re.S):
        content = zlib.decompress(stream)
        for raw in re.findall(rb"\(((?:\\.|[^\\)])*)\) Tj", content):
            found.append(_unescape(raw).decode("cp1252"))
    return found


def check_structure(test: unittest.TestCase, data: bytes) -> int:
    """Un PDF bien formé (en-tête, table des objets aux bons endroits, fin) ; renvoie son nombre de pages."""
    test.assertTrue(data.startswith(b"%PDF-1.4"))
    test.assertTrue(data.rstrip().endswith(b"%%EOF"))
    start = int(re.search(rb"startxref\n(\d+)", data).group(1))
    test.assertTrue(data[start:].startswith(b"xref"))
    offsets = [int(x) for x in re.findall(rb"(\d{10}) 00000 n", data[start:])]
    for number, offset in enumerate(offsets, start=1):
        test.assertTrue(data[offset:].startswith(b"%d 0 obj" % number), number)
    return int(re.search(rb"/Type /Pages /Kids \[[^\]]*\] /Count (\d+)", data).group(1))


class PdfWriterTest(unittest.TestCase):
    def test_text(self):
        self.assertEqual(pw.encode("é « € » − 1\u202f000 →"), b"\xe9 \xab \x80 \xbb \x96 1\xa0000 \x9b")
        self.assertEqual(pw.encode("♠"), b"?")
        self.assertAlmostEqual(pw.text_width("a", "regular", 10), 5.56)
        self.assertAlmostEqual(pw.text_width("i", "bold", 10), 2.78)
        lines = pw.wrap("Un texte assez long pour être coupé en plusieurs lignes", 100)
        self.assertGreater(len(lines), 1)
        self.assertTrue(all(pw.text_width(line) <= 100 for line in lines))
        self.assertEqual(pw.wrap("un\ndeux", 100), ["un", "deux"])
        self.assertTrue(all(pw.text_width(x) <= 20 for x in pw.wrap("anticonstitutionnellement", 20)))

    def test_document(self):
        doc = pw.Document(title="Essai — été")
        page = doc.add_page()
        page.text(40, 40, "Bonjour (à tous) \\ ok", "bold", 12)
        page.suit(40, 60, "h", 10, pw.rgb("#d03b3b"))
        page.rect(40, 80, 100, 30, fill=(0.9, 0.9, 0.9), stroke=(0, 0, 0), radius=6)
        page.polyline([(40, 120), (80, 140), (120, 130)])
        doc.add_page().text(40, 40, "Deuxième page")
        data = doc.output(datetime(2026, 10, 1, 12, 0))
        self.assertEqual(check_structure(self, data), 2)
        self.assertEqual(pdf_strings(data), ["Bonjour (à tous) \\ ok", "\xaa", "Deuxième page"])
        self.assertIn(b"/BaseFont /ZapfDingbats", data)
        self.assertIn(b"/CreationDate (D:20261001120000)", data)
        self.assertIn(b"/Producer " + pw._info_text(NAME), data)  # le nom de l'application


def check_pptx(test: unittest.TestCase, data: bytes) -> dict[str, str]:
    """Un paquet PowerPoint bien formé : chaque XML se lit, chaque relation mène à une partie qui existe, chaque partie
    a son type. Renvoie ses parties (nom -> texte)."""
    archive = zipfile.ZipFile(io.BytesIO(data))
    names = archive.namelist()
    test.assertEqual(names[0], "[Content_Types].xml")
    parts = {name: archive.read(name).decode("utf-8") for name in names}
    for name, text in parts.items():
        ElementTree.fromstring(text.encode())  # XML bien formé
    types = parts["[Content_Types].xml"]
    for name in names:
        if not name.endswith(".rels") and name != "[Content_Types].xml":
            test.assertIn(f'PartName="/{name}"', types, name)
        if name.endswith(".rels"):
            base = posixpath.dirname(posixpath.dirname(name))
            for target in re.findall(r'Target="([^"]+)"', parts[name]):
                test.assertIn(posixpath.normpath(posixpath.join(base, target)), names, (name, target))
    return parts


def slide_texts(parts: dict[str, str], prefix: str = "ppt/slides/slide") -> list[list[str]]:
    """Les textes de chaque diapositive (ou de chaque page de notes), dans l'ordre."""
    count = len([n for n in parts if n.startswith(prefix) and n.endswith(".xml")])
    return [[unescape(t) for t in re.findall(r"<a:t>(.*?)</a:t>", parts[f"{prefix}{i}.xml"])]
            for i in range(1, count + 1)]


class PptxWriterTest(unittest.TestCase):
    def test_package(self):
        deck = px.Presentation(title="Essai & co")
        slide = deck.add_slide("0F3D2E")
        slide.text(1, 1, 5, 1, [px.para("Bonjour <à tous>", 24, "FFFFFF", bold=True)])
        slide.shape(1, 2.5, 2, 1, fill="E0A526", geom="roundRect", paras=[px.para("Forme", 14)])
        slide.line(1, 4, 3, 3.5, "D03B3B", 2)
        slide.path(4, 3, 4, 2, [(0, 1), (0.5, 0.2), (1, 0.6)], "2A78D6")
        slide.notes = "Première ligne\nDeuxième ligne"
        other = deck.add_slide()
        other.table(1, 1, [2, 1], [[px.Cell([px.para("Situation", 14)], fill="0F3D2E"), px.Cell([px.para("Toi", 14)])],
                                   [px.Cell([px.para("Open", 14)]), px.Cell([px.para("23 %", 14)], fill="FBE1D6")]],
                    [0.5, 0.6])
        parts = check_pptx(self, deck.output(datetime(2026, 10, 1, 12, 0)))
        self.assertEqual(len(re.findall(r"<p:sldId ", parts["ppt/presentation.xml"])), 2)
        self.assertEqual(slide_texts(parts), [["Bonjour <à tous>", "Forme"], ["Situation", "Toi", "Open", "23 %"]])
        self.assertEqual(slide_texts(parts, "ppt/notesSlides/notesSlide")[0], ["Première ligne", "Deuxième ligne"])
        self.assertIn("<dc:title>Essai &amp; co</dc:title>", parts["docProps/core.xml"])
        self.assertIn('flipV="1"', parts["ppt/slides/slide1.xml"])  # le trait monte vers la droite

    def test_fit(self):
        from analyzer.app.report_pptx import first_sentence, fit
        text, size, lines = fit("Un titre beaucoup trop long pour tenir sur une seule ligne de la boîte", 3.0, 24,
                                True, 1, 14)
        self.assertEqual((lines, size), (1, 14))
        self.assertTrue(text.endswith("…"))
        self.assertLessEqual(px.width(text, size, True), 3.0)
        self.assertEqual(fit("Court", 3.0, 24)[0:2], ("Court", 24))
        self.assertEqual(first_sentence("A 1,5 bb. La suite."), "A 1,5 bb.")


class ReportPdfTest(IsolatedHome):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.folder = Path(tmp.name)
        shutil.copy(FIXTURE, self.folder / "sample.txt")

    def test_report(self):
        lib = Library(self.folder)
        data = lib.report_pdf()
        self.assertGreaterEqual(check_structure(self, data), 1)
        text = pdf_strings(data)
        for expected in ("Hero", "Les leaks à travailler", "Les écarts les plus importants", "Face au solveur",
                         "Mains à revoir", "Heads-up · 4 mains · le 10/01/2026"):
            self.assertIn(expected, text)
        self.assertFalse(any(t.startswith("Période") for t in text))
        lib.set_period({"kind": "last", "n": 2})
        self.assertIn("Période : 2 dernières mains", pdf_strings(lib.report_pdf()))

    def test_student(self):
        lib = Library(self.folder)
        student = lib.create_student("Paul")
        shutil.copy(FIXTURE, Path(lib.student(student["id"]).folder) / "sample.txt")
        lib.student(student["id"]).reload()
        self.assertIn("Paul", pdf_strings(lib.student(student["id"]).report_pdf()))
        parts = check_pptx(self, lib.student(student["id"]).report_pptx())
        texts = slide_texts(parts)
        self.assertEqual(texts[0][:6], ["\u2660", "\u2665", "\u2666", "\u2663", "SÉANCE DE COACHING · LEAKFINDING", "Paul"])
        flat = [t for slide in texts for t in slide]
        for expected in ("Où tu en es", "Les leaks à travailler", "Les mains à revoir", "D'ici la prochaine séance"):
            self.assertIn(expected, flat)
        self.assertTrue(any(t.startswith(f"Préparé avec {NAME} le ") for t in flat))
        notes = slide_texts(parts, "ppt/notesSlides/notesSlide")
        self.assertEqual(len(notes), len(texts))
        self.assertTrue(all(n for n in notes))  # chaque diapositive a ses notes pour le coach
        self.assertIn("presentation.pptx", lib.student(student["id"]).leaks_page())
        self.assertNotIn("presentation.pptx", lib.leaks_page())  # pour un élève seulement


class ReportRoutesTest(IsolatedHome):
    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        shutil.copy(FIXTURE, Path(tmp.name) / "sample.txt")
        self.server = start(Library(tmp.name), port=0)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def get(self, path):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=60)
        conn.request("GET", path)
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp, data

    def test_pdf_route(self):
        resp, data = self.get("/moi/rapport.pdf")
        self.assertEqual((resp.status, resp.getheader("Content-Type")), (200, "application/pdf"))
        self.assertIn('filename="leakfinding.pdf"', resp.getheader("Content-Disposition"))
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertEqual(self.get("/moi/rapport.pdf?format=ring")[0].status, 404)  # pas de table à plusieurs
        self.assertIn(b'/moi/rapport.pdf" download>Synth', self.get("/moi/leaks")[1])

    def test_pptx_route(self):
        resp, data = self.get("/moi/presentation.pptx")
        self.assertEqual(resp.status, 200)
        self.assertEqual(resp.getheader("Content-Type"),
                         "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        self.assertIn('filename="leakfinding.pptx"', resp.getheader("Content-Disposition"))
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(data)))


if __name__ == "__main__":
    unittest.main()

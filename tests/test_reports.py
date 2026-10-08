import http.client
import re
import shutil
import tempfile
import threading
import unittest
import zlib
from datetime import datetime
from pathlib import Path

from analyzer import pdfwriter as pw
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


if __name__ == "__main__":
    unittest.main()

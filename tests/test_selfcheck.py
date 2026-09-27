import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from card_logger import limitless, pricing, selfcheck
from tests.test_limitless import fake_fetch
from tests.test_pricing import POKEMON_CARDS, SCRYFALL_PRINTS, YUGIOH_CARDS, rb_fetch


def fake_services(url):
    if "tcgcsv.com" in url:
        return rb_fetch()(url)
    if "limitlesstcg.com" in url:
        return fake_fetch([])(url)
    if "scryfall" in url:
        return {"data": SCRYFALL_PRINTS}
    if "pokemontcg" in url:
        return {"data": POKEMON_CARDS}
    if "ygoprodeck" in url:
        return {"data": YUGIOH_CARDS}
    raise AssertionError(url)


@mock.patch.object(limitless, "PAUSE", 0)
class SelfCheckTest(unittest.TestCase):
    def run_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.txt"
            out = io.StringIO()
            with redirect_stdout(out):
                ok = selfcheck.run(path)
            return ok, out.getvalue(), path.read_text()

    def test_all_pass(self):
        with mock.patch.object(pricing, "_get_json", fake_services):
            ok, out, report = self.run_check()
        self.assertTrue(ok, out)
        self.assertEqual(out.count("[PASS]"), 5)
        self.assertIn("Riftbound game id 'RB'", out)
        self.assertIn("record 5-1", out)
        self.assertIn("https://tcgcsv.com/tcgplayer/89/groups", report)  # response samples saved

    def test_failures_reported(self):
        def offline(url):
            raise pricing.PriceLookupError("Could not reach the price service. Check your internet connection.")
        with mock.patch.object(pricing, "_get_json", offline):
            ok, out, report = self.run_check()
        self.assertFalse(ok)
        self.assertEqual(out.count("[FAIL]"), 5)
        self.assertIn("Traceback", report)


if __name__ == "__main__":
    unittest.main()

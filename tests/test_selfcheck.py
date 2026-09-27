import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from card_logger import pricing, riftboundgg, selfcheck
from tests.test_riftboundgg import fake_api
from tests.test_pricing import POKEMON_CARDS, SCRYFALL_PRINTS, YUGIOH_CARDS, rb_fetch


def fake_services(url):
    if "tcgcsv.com" in url:
        return rb_fetch()(url)
    if "api.dotgg.gg" in url:
        return fake_api([])(url)
    if "scryfall" in url:
        return {"data": SCRYFALL_PRINTS}
    if "pokemontcg" in url:
        return {"data": POKEMON_CARDS}
    if "ygoprodeck" in url:
        return {"data": YUGIOH_CARDS}
    if "frankfurter" in url:
        return {"date": "2026-09-25", "rates": {"GBP": 0.75458, "EUR": 0.87696}}
    raise AssertionError(url)


@mock.patch.object(riftboundgg, "PAUSE", 0)
class SelfCheckTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(selfcheck, "_topdeck_key", lambda: "")
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.txt"
            out = io.StringIO()
            with redirect_stdout(out):
                ok = selfcheck.run(path)
            return ok, out.getvalue(), path.read_text()

    def test_all_pass(self):
        with mock.patch.object(pricing, "_get_json", fake_services), \
                mock.patch.object(riftboundgg, "http_json", fake_services):
            ok, out, report = self.run_check()
        self.assertTrue(ok, out)
        self.assertEqual(out.count("[PASS]"), 6)
        self.assertEqual(out.count("[SKIP]"), 1)  # no TopDeck.gg key
        self.assertIn("newest event 'Vendetta Case Tournament' (37 players)", out)
        self.assertIn("legend 'Sett - The Boss'", out)
        self.assertIn("$1 = £0.7546", out)
        self.assertIn("https://tcgcsv.com/tcgplayer/89/groups", report)  # response samples saved

    def test_failures_reported(self):
        def offline(url):
            raise pricing.PriceLookupError("Could not reach the price service. Check your internet connection.")
        with mock.patch.object(pricing, "_get_json", offline), mock.patch.object(riftboundgg, "http_json", offline):
            ok, out, report = self.run_check()
        self.assertFalse(ok)
        self.assertEqual(out.count("[FAIL]"), 6)
        self.assertIn("Traceback", report)


if __name__ == "__main__":
    unittest.main()

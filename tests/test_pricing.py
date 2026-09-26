import unittest

from card_logger.db import Card
from card_logger.pricing import (
    PriceLookupError,
    detect_source,
    lookup_price,
    parse_pokemon,
    parse_scryfall,
    parse_yugioh,
)

# Trimmed-down responses in the shape each service returns.
SCRYFALL_PRINTS = [
    {"name": "Lightning Bolt", "set": "2x2", "set_name": "Double Masters 2022",
     "collector_number": "117", "prices": {"usd": "1.10", "usd_foil": "3.50"}},
    {"name": "Lightning Bolt", "set": "m10", "set_name": "Magic 2010",
     "collector_number": "146", "prices": {"usd": "2.25", "usd_foil": None}},
    {"name": "Lightning Bolt", "set": "lea", "set_name": "Limited Edition Alpha",
     "collector_number": "161", "prices": {"usd": "450.00", "usd_foil": None}},
    {"name": "Lightning Bolt", "set": "pdrc", "set_name": "Promo",
     "collector_number": "1", "prices": {"usd": None, "usd_foil": None}},
]

POKEMON_CARDS = [
    {"name": "Charizard", "number": "4", "set": {"id": "base1", "name": "Base"},
     "tcgplayer": {"prices": {"holofoil": {"market": 420.0, "mid": 450.0}}}},
    {"name": "Charizard", "number": "11", "set": {"id": "sm35", "name": "Shining Legends"},
     "tcgplayer": {"prices": {"normal": {"market": 3.2}, "reverseHolofoil": {"market": 6.4}}}},
    {"name": "Charizard", "number": "99", "set": {"id": "x", "name": "No Prices"}},
]

YUGIOH_CARDS = [
    {"name": "Dark Magician", "card_prices": [{"tcgplayer_price": "0.35"}],
     "card_sets": [
         {"set_name": "Legend of Blue Eyes White Dragon", "set_code": "LOB-005", "set_price": "25.10"},
         {"set_name": "Starter Deck: Yugi", "set_code": "SDY-006", "set_price": "4.00"},
     ]},
    {"name": "Dark Magician Girl", "card_prices": [{"tcgplayer_price": "1.50"}], "card_sets": []},
]


class DetectSourceTest(unittest.TestCase):
    def test_games(self):
        for game, source in [
            ("Magic", "scryfall"), ("MTG", "scryfall"), ("Magic: The Gathering", "scryfall"),
            ("Pokemon", "pokemon"), ("Pokémon", "pokemon"), ("pokemon tcg", "pokemon"),
            ("Yu-Gi-Oh!", "yugioh"), ("yugioh", "yugioh"),
            ("Baseball", None), ("", None),
        ]:
            self.assertEqual(detect_source(game), source, game)


class ScryfallTest(unittest.TestCase):
    def test_matches_set_name(self):
        r = parse_scryfall(SCRYFALL_PRINTS, Card(name="Lightning Bolt", set_name="Limited Edition Alpha"))
        self.assertEqual(r.price, 450.0)
        self.assertIn("Alpha", r.matched)

    def test_matches_set_code_and_number(self):
        r = parse_scryfall(SCRYFALL_PRINTS, Card(name="Lightning Bolt", set_name="M10", number="146"))
        self.assertEqual(r.price, 2.25)

    def test_no_set_uses_first_priced_print(self):
        self.assertEqual(parse_scryfall(SCRYFALL_PRINTS, Card(name="Lightning Bolt")).price, 1.10)

    def test_foil_hint(self):
        r = parse_scryfall(SCRYFALL_PRINTS, Card(name="Lightning Bolt", notes="Foil"))
        self.assertEqual(r.price, 3.50)

    def test_no_prices(self):
        self.assertIsNone(parse_scryfall(SCRYFALL_PRINTS[3:], Card(name="Lightning Bolt")))


class PokemonTest(unittest.TestCase):
    def test_matches_set_and_number(self):
        r = parse_pokemon(POKEMON_CARDS, Card(name="Charizard", set_name="Base", number="4"))
        self.assertEqual(r.price, 420.0)

    def test_reverse_holo_hint(self):
        r = parse_pokemon(POKEMON_CARDS, Card(name="Charizard", set_name="Shining Legends", rarity="Reverse Holo"))
        self.assertEqual(r.price, 6.4)
        self.assertIn("reverseHolofoil", r.matched)

    def test_default_variant(self):
        r = parse_pokemon(POKEMON_CARDS, Card(name="Charizard", number="11"))
        self.assertEqual(r.price, 3.2)


class YugiohTest(unittest.TestCase):
    def test_exact_name_and_set(self):
        r = parse_yugioh(YUGIOH_CARDS, Card(name="Dark Magician", set_name="Legend of Blue Eyes White Dragon"))
        self.assertEqual(r.price, 25.10)

    def test_set_code_in_number(self):
        r = parse_yugioh(YUGIOH_CARDS, Card(name="Dark Magician", number="SDY-006"))
        self.assertEqual(r.price, 4.0)

    def test_general_price(self):
        self.assertEqual(parse_yugioh(YUGIOH_CARDS, Card(name="Dark Magician")).price, 0.35)
        self.assertEqual(parse_yugioh(YUGIOH_CARDS, Card(name="Dark Magician Girl")).price, 1.50)


class LookupPriceTest(unittest.TestCase):
    def test_uses_right_service(self):
        urls = []

        def fetch(url):
            urls.append(url)
            return {"data": POKEMON_CARDS}

        r = lookup_price(Card(name="Charizard", game="Pokémon", set_name="Base"), fetch=fetch)
        self.assertEqual(r.price, 420.0)
        self.assertTrue(urls[0].startswith("https://api.pokemontcg.io/"))

    def test_unsupported_game(self):
        with self.assertRaisesRegex(PriceLookupError, "isn't available"):
            lookup_price(Card(name="Babe Ruth", game="Baseball"), fetch=lambda url: {})

    def test_not_found(self):
        with self.assertRaisesRegex(PriceLookupError, "No price found"):
            lookup_price(Card(name="Nonexistent", game="Magic"), fetch=lambda url: {})


if __name__ == "__main__":
    unittest.main()

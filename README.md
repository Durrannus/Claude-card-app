# Card Collection Logger

A small desktop program for keeping track of your trading card collection,
built mainly for **Riftbound** (the League of Legends TCG) but works for any
card game. It runs on your own computer and has no extra packages to
install. Only price lookups need the internet.

![Collection tab](docs/screenshot-collection.png)

![Meta tracker tab](docs/screenshot-meta.png)

## What it does

- Add, edit and delete cards: name, game, set, card number, rarity,
  condition, quantity, value and notes
- Search as you type, and filter by game
- Click a column heading to sort by it (click again to reverse)
- Shows how many cards you have and what they're worth, for the current
  view and for the whole collection
- **Card photos:** attach a photo to each card and see it beside the list
- **Wishlist:** keep a separate list of cards you want, with the total cost
  to complete it; tick "Mark as owned" when you get one
- **Price lookup:** fetch current market prices for Riftbound, Magic,
  Pokémon and Yu-Gi-Oh! cards (see below)
- **Meta tracker:** save tournament decklists and see which cards and
  legends are played most, how many copies decks run, and which of those
  cards you're missing (see below)
- Export your collection and wishlist to a CSV file (opens in Excel / Google
  Sheets), or import cards from a CSV

## Requirements

Python 3.10 or newer.

- **Windows / macOS:** install Python from <https://www.python.org/downloads/>.
  It includes everything the program needs.
- **Linux:** install Python plus Tk, e.g. `sudo apt install python3 python3-tk`.

## Running it

- **Windows:** double-click `start_card_logger.pyw`.
- **Any system:** open a terminal in this folder and run

  ```
  python -m card_logger
  ```

  (use `python3` instead of `python` on macOS/Linux).

## Photos

Select a card and click **Add photo…** (or choose one in the Add/Edit form).
The photo is copied into the program's own folder, so moving or deleting the
original afterwards is fine.

PNG and GIF photos preview out of the box. To preview JPEG (most phone
photos) and other formats too, install Pillow once:

```
python -m pip install pillow
```

Without Pillow, **Open photo** still opens any photo in your normal viewer.

## Wishlist

Switch between **My collection** and **Wishlist** at the top left. Cards on
the wishlist don't count towards your collection's totals. Use
**Move to wishlist** / **Mark as owned** to move selected cards between the
two, or tick "On my wishlist" in the Add/Edit form.

## Price lookup

Prices come from free public databases, in US dollars, based on TCGplayer
market prices. An internet connection is needed. The card's **Game** must be
one of these:

| Game | Source |
| --- | --- |
| Riftbound | TCGplayer prices, via [TCGCSV](https://tcgcsv.com) |
| Magic (or "MTG", "Magic: The Gathering") | [Scryfall](https://scryfall.com) |
| Pokémon | [Pokémon TCG API](https://pokemontcg.io) |
| Yu-Gi-Oh! | [YGOPRODeck](https://ygoprodeck.com) |

Sports and other cards don't have a free price service, so enter those
values yourself.

- In the Add/Edit form, **Look up price** fills in the value and shows which
  printing it matched. Check it before saving.
- **Update prices** refreshes the value of the selected cards, or every card
  shown if none are selected.

The more you fill in, the better the match. **Set** and **Card number**
pick the exact printing. Putting "foil", "holo", "reverse holo" or "1st
edition" in Rarity or Notes picks that version's price. For Yu-Gi-Oh!, you
can put the set code (e.g. `LOB-EN005`) in Card number.

For Riftbound:

- Enter the card number as printed, e.g. `OGN-148`. The set code at the
  front picks the set, so you can leave Set blank.
- For showcase or alternate-art cards, set Rarity to "Showcase", or write
  "alt art" in Notes.
- Write "foil" in Notes to get the foil price.
- The first lookup in a session downloads the set's price list, which takes
  a few seconds. Lookups after that are quick.

## Meta tracker

The **Meta tracker** tab shows what the competitive decks are playing.
You feed it decklists from tournament results, and it adds them up.

1. Find decklists on a meta site such as [riftDecks](https://riftdecks.com),
   [riftbound.gg](https://riftbound.gg) or
   [Piltover Archive](https://piltoverarchive.com). Copy the decklist text,
   or use the site's "Export as text".
2. Click **Add decklist…**, paste it in, and fill in the event, player,
   placement and date. The line under the box shows what was read (legend,
   number of main deck cards, runes, battlefields) so you can check it.
   To add many at once, save each deck as a `.txt` file and use
   **Import .txt files…**.
3. The **Most played cards** list shows each card's number of decks, % of
   decks, average copies, and how many you own. Green means you own enough
   copies and red means you're short. Runes and legends are left out unless
   you tick "Include runes".
4. The **Legends** list shows each legend's share of the meta and best
   finish. Double-click one to see just that legend's cards.
5. Narrow things down with **Legend**, **Finish** (e.g. only Top 8 decks)
   and **Since** (only decks from a date onwards).
6. Select cards (Ctrl+A for all) and click **Add missing cards to wishlist**.
   The missing copies go on your wishlist, where **Update prices** tells you
   what they'll cost.

Decklists can be in any of the common text formats: headings such as
`Legend:`, `Champion:`, `Main Deck:`, `Runes:`, `Battlefields:` and
`Sideboard:`, with quantities written `3 Card`, `3x Card`, `Card x3` or
`Card (x3)`. Lines with no heading count as the main deck.

The meta tracker doesn't download decklists automatically. None of the meta
sites offers a free public feed of their data, so the decklists come from
you.

### Tips

- Double-click a row to edit it; press **Delete** to remove selected rows.
- Hold Ctrl/Shift to select several rows at once.
- To import a CSV, the first row must be a header. Only a `name` column is
  required; other recognised columns are `game`, `set_name`, `number`,
  `rarity`, `condition`, `quantity`, `value`, `notes`, `date_added`,
  `wishlist` (`yes` for wishlist cards), `image_path`. The
  easiest way to get the format right is to export first and copy it.

## Where your data is stored

Your collection is saved automatically to `cards.db` in a `.card_logger`
folder inside your home folder (e.g. `C:\Users\you\.card_logger\cards.db`),
and card photos go in the `images` folder next to it. Meta tracker
decklists are saved in the same `cards.db` file. Back up the whole
`.card_logger` folder to keep your collection safe.

To use a different file, run `python -m card_logger --db path/to/file.db`
or set the `CARD_LOGGER_DB` environment variable.

## Running the tests

```
python -m unittest discover -s tests
```

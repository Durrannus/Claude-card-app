# Card Collection Logger

A small desktop program for keeping track of your trading card collection
(Pokémon, Magic, Yu-Gi-Oh!, sports cards — anything). It runs on your own
computer, needs no internet connection, and has no extra packages to install.

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
- **Price lookup:** fetch current market prices for Magic, Pokémon and
  Yu-Gi-Oh! cards (see below)
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
and card photos go in the `images` folder next to it. Back up the whole
`.card_logger` folder to keep your collection safe.

To use a different file, run `python -m card_logger --db path/to/file.db`
or set the `CARD_LOGGER_DB` environment variable.

## Running the tests

```
python -m unittest discover -s tests
```

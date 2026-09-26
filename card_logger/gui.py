"""Desktop window for browsing and editing the card collection."""

import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import pricing
from .db import CONDITIONS, IMAGE_TYPES, Card, CardDatabase

try:  # Pillow is optional; without it only PNG and GIF photos can be previewed.
    from PIL import Image, ImageTk
except ImportError:
    Image = ImageTk = None

ALL_GAMES = "All games"
COLLECTION, WISHLIST = "collection", "wishlist"
IMAGE_FILETYPES = [("Images", " ".join(f"*{ext}" for ext in IMAGE_TYPES)), ("All files", "*.*")]
PREVIEW_SIZE = (260, 360)

# (field, heading, width, anchor)
COLUMNS = [
    ("name", "Name", 190, "w"),
    ("game", "Game", 100, "w"),
    ("set_name", "Set", 130, "w"),
    ("number", "#", 60, "center"),
    ("rarity", "Rarity", 80, "w"),
    ("condition", "Condition", 90, "w"),
    ("quantity", "Qty", 45, "center"),
    ("value", "Value", 75, "e"),
    ("date_added", "Added", 85, "center"),
]


def run_in_background(widget: tk.Misc, work, on_done, on_progress=None) -> None:
    """Run `work(report)` on a worker thread, then call `on_done(result, error)`
    on the Tk thread. `report(msg)` inside `work` forwards to `on_progress`."""
    messages: queue.Queue = queue.Queue()

    def target():
        try:
            result = work(lambda msg: messages.put(("progress", msg)))
            messages.put(("done", (result, None)))
        except Exception as e:  # reported back to the UI thread
            messages.put(("done", (None, e)))

    def poll():
        try:
            while True:
                kind, payload = messages.get_nowait()
                if kind == "progress" and on_progress:
                    on_progress(payload)
                elif kind == "done":
                    on_done(*payload)
                    return
        except queue.Empty:
            pass
        widget.after(100, poll)

    threading.Thread(target=target, daemon=True).start()
    widget.after(100, poll)


def load_photo(path: str, max_size: tuple[int, int]):
    """Return a Tk image scaled to fit `max_size`, or None if it can't be shown."""
    if not path or not Path(path).exists():
        return None
    try:
        if Image is not None:
            img = Image.open(path)
            img.thumbnail(max_size)
            return ImageTk.PhotoImage(img)
        if Path(path).suffix.lower() not in (".png", ".gif"):
            return None
        img = tk.PhotoImage(file=path)
        factor = max(1, -(-img.width() // max_size[0]), -(-img.height() // max_size[1]))
        return img.subsample(factor) if factor > 1 else img
    except (tk.TclError, OSError, ValueError):
        return None


def open_file(path: str) -> None:
    """Open a file with the computer's default program."""
    if sys.platform.startswith("win"):
        os.startfile(path)  # noqa: S606 - opening the user's own file
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class CardDialog(tk.Toplevel):
    """Form for adding a new card or editing an existing one.

    After it closes, `result` holds the edited card (or None if cancelled) and
    `photo` is the chosen photo file: None = unchanged, "" = remove it.
    """

    def __init__(self, parent, title: str, games: list[str], card: Card | None = None):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.result: Card | None = None
        self.photo: str | None = None
        self._card = card or Card(name="")

        form = ttk.Frame(self, padding=12)
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)

        self.vars = {
            "name": tk.StringVar(value=self._card.name),
            "game": tk.StringVar(value=self._card.game),
            "set_name": tk.StringVar(value=self._card.set_name),
            "number": tk.StringVar(value=self._card.number),
            "rarity": tk.StringVar(value=self._card.rarity),
            "condition": tk.StringVar(value=self._card.condition),
            "quantity": tk.StringVar(value=str(self._card.quantity)),
            "value": tk.StringVar(value=f"{self._card.value:.2f}"),
        }
        self.wishlist_var = tk.BooleanVar(value=self._card.wishlist)
        self.photo_var = tk.StringVar(value=Path(self._card.image_path).name if self._card.image_path else "(none)")

        value_row = ttk.Frame(form)
        ttk.Entry(value_row, textvariable=self.vars["value"], width=14).pack(side="left")
        self.lookup_button = ttk.Button(value_row, text="Look up price", command=self._lookup_price)
        self.lookup_button.pack(side="left", padx=(6, 0))

        photo_row = ttk.Frame(form)
        ttk.Label(photo_row, textvariable=self.photo_var, width=22).pack(side="left")
        ttk.Button(photo_row, text="Choose…", command=self._choose_photo).pack(side="left", padx=(6, 0))
        ttk.Button(photo_row, text="Remove", command=self._remove_photo).pack(side="left", padx=(4, 0))

        rows = [
            ("Name *", ttk.Entry(form, textvariable=self.vars["name"], width=40)),
            ("Game", ttk.Combobox(form, textvariable=self.vars["game"], width=38,
                                  values=sorted(set(games) | {"Magic", "Pokémon", "Yu-Gi-Oh!"}))),
            ("Set", ttk.Entry(form, textvariable=self.vars["set_name"], width=40)),
            ("Card number", ttk.Entry(form, textvariable=self.vars["number"], width=40)),
            ("Rarity", ttk.Entry(form, textvariable=self.vars["rarity"], width=40)),
            ("Condition", ttk.Combobox(form, textvariable=self.vars["condition"], values=CONDITIONS, width=38)),
            ("Quantity", ttk.Spinbox(form, textvariable=self.vars["quantity"], from_=0, to=9999, width=38)),
            ("Value (each)", value_row),
            ("Photo", photo_row),
            ("", ttk.Checkbutton(form, text="On my wishlist (I don't own it yet)", variable=self.wishlist_var)),
        ]
        for i, (label, widget) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky="w", pady=3, padx=(0, 8))
            widget.grid(row=i, column=1, sticky="ew", pady=3)

        self.lookup_status = tk.StringVar()
        ttk.Label(form, textvariable=self.lookup_status, foreground="#555", wraplength=330).grid(
            row=len(rows), column=1, sticky="w"
        )

        notes_row = len(rows) + 1
        ttk.Label(form, text="Notes").grid(row=notes_row, column=0, sticky="nw", pady=3)
        self.notes = tk.Text(form, width=40, height=4, wrap="word")
        self.notes.insert("1.0", self._card.notes)
        self.notes.grid(row=notes_row, column=1, sticky="ew", pady=3)

        buttons = ttk.Frame(form)
        buttons.grid(row=notes_row + 1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", command=self._save).pack(side="right", padx=(0, 6))

        self.bind("<Return>", lambda e: self._save() if e.widget is not self.notes else None)
        self.bind("<Escape>", lambda e: self.destroy())
        rows[0][1].focus_set()
        self.grab_set()
        self.wait_window()

    def _choose_photo(self) -> None:
        path = filedialog.askopenfilename(parent=self, title="Choose card photo", filetypes=IMAGE_FILETYPES)
        if path:
            self.photo = path
            self.photo_var.set(Path(path).name)

    def _remove_photo(self) -> None:
        self.photo = ""
        self.photo_var.set("(none)")

    def _lookup_price(self) -> None:
        card = Card(
            name=self.vars["name"].get().strip(),
            game=self.vars["game"].get().strip(),
            set_name=self.vars["set_name"].get().strip(),
            number=self.vars["number"].get().strip(),
            rarity=self.vars["rarity"].get().strip(),
            notes=self.notes.get("1.0", "end").strip(),
        )
        if not card.name:
            self.lookup_status.set("Enter the card's name first.")
            return
        if not pricing.supported(card):
            self.lookup_status.set("Set Game to Magic, Pokémon or Yu-Gi-Oh! to look up prices.")
            return
        self.lookup_button.state(["disabled"])
        self.lookup_status.set("Looking up price…")

        def done(result, error):
            if not self.winfo_exists():
                return
            self.lookup_button.state(["!disabled"])
            if error:
                self.lookup_status.set(str(error))
            else:
                self.vars["value"].set(f"{result.price:.2f}")
                self.lookup_status.set(f"${result.price:,.2f} from {result.source}: {result.matched}")

        run_in_background(self, lambda _report: pricing.lookup_price(card), done)

    def _save(self) -> None:
        name = self.vars["name"].get().strip()
        if not name:
            messagebox.showerror("Missing name", "Please enter the card's name.", parent=self)
            return
        try:
            quantity = int(self.vars["quantity"].get() or 0)
            value = float(self.vars["value"].get().replace("$", "").replace(",", "") or 0)
        except ValueError:
            messagebox.showerror(
                "Invalid number", "Quantity must be a whole number and value a number.", parent=self
            )
            return
        if quantity < 0 or value < 0:
            messagebox.showerror("Invalid number", "Quantity and value cannot be negative.", parent=self)
            return

        self.result = replace(
            self._card,
            name=name,
            game=self.vars["game"].get().strip(),
            set_name=self.vars["set_name"].get().strip(),
            number=self.vars["number"].get().strip(),
            rarity=self.vars["rarity"].get().strip(),
            condition=self.vars["condition"].get().strip(),
            quantity=quantity,
            value=value,
            notes=self.notes.get("1.0", "end").strip(),
            wishlist=self.wishlist_var.get(),
        )
        self.destroy()


class CardLoggerApp(ttk.Frame):
    def __init__(self, root: tk.Tk, db: CardDatabase):
        super().__init__(root, padding=8)
        self.root = root
        self.db = db
        self.busy = False
        root.title("Card Collection Logger")
        root.geometry("1200x650")
        root.minsize(850, 450)
        self.pack(fill="both", expand=True)

        self._build_toolbar()
        self._build_body()
        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, anchor="w").pack(fill="x", pady=(6, 0))
        self.refresh()

    # --- layout ------------------------------------------------------------

    def _build_toolbar(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 4))

        self.view_var = tk.StringVar(value=COLLECTION)
        for text, value in [("My collection", COLLECTION), ("Wishlist", WISHLIST)]:
            ttk.Radiobutton(top, text=text, value=value, variable=self.view_var,
                            command=self._change_view).pack(side="left", padx=(0, 8))
        ttk.Separator(top, orient="vertical").pack(side="left", fill="y", padx=6)

        ttk.Label(top, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh())
        ttk.Entry(top, textvariable=self.search_var, width=28).pack(side="left", padx=(4, 10))

        self.game_var = tk.StringVar(value=ALL_GAMES)
        self.game_box = ttk.Combobox(top, textvariable=self.game_var, state="readonly", width=18)
        self.game_box.pack(side="left")
        self.game_box.bind("<<ComboboxSelected>>", lambda _: self.refresh())

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))
        ttk.Button(bar, text="Add card", command=self.add_card).pack(side="left", padx=(0, 2))
        ttk.Button(bar, text="Edit", command=self.edit_selected).pack(side="left", padx=2)
        ttk.Button(bar, text="Delete", command=self.delete_selected).pack(side="left", padx=2)
        self.move_button = ttk.Button(bar, command=self.move_selected)
        self.move_button.pack(side="left", padx=2)
        self.price_button = ttk.Button(bar, text="Update prices", command=self.update_prices)
        self.price_button.pack(side="left", padx=2)
        ttk.Button(bar, text="Export CSV", command=self.export_csv).pack(side="right", padx=2)
        ttk.Button(bar, text="Import CSV", command=self.import_csv).pack(side="right", padx=2)

    def _build_body(self) -> None:
        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        # Packed first so it keeps its width and the table shrinks instead.
        details = ttk.Frame(body, padding=(10, 0, 0, 0), width=PREVIEW_SIZE[0] + 20)
        details.pack(side="right", fill="y")
        details.pack_propagate(False)

        table = ttk.Frame(body)
        self.tree = ttk.Treeview(
            table, columns=[c[0] for c in COLUMNS], show="headings", selectmode="extended"
        )
        for field, heading, width, anchor in COLUMNS:
            self.tree.heading(field, text=heading, command=lambda f=field: self.sort_by(f))
            self.tree.column(field, width=width, anchor=anchor)
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        table.pack(side="left", fill="both", expand=True)

        self.tree.bind("<Double-1>", lambda _: self.edit_selected())
        self.tree.bind("<Delete>", lambda _: self.delete_selected())
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.show_details())
        self._sort_field = None
        self._sort_reverse = False

        self.photo_label = ttk.Label(details, anchor="center", justify="center")
        self.photo_label.pack(fill="x")
        self._photo_image = None  # keep a reference so Tk doesn't discard it
        photo_buttons = ttk.Frame(details)
        photo_buttons.pack(pady=6)
        self.add_photo_button = ttk.Button(photo_buttons, text="Add photo…", command=self.choose_photo)
        self.add_photo_button.pack(side="left", padx=2)
        self.open_photo_button = ttk.Button(photo_buttons, text="Open photo", command=self.open_photo)
        self.open_photo_button.pack(side="left", padx=2)
        self.detail_text = tk.StringVar()
        ttk.Label(details, textvariable=self.detail_text, wraplength=PREVIEW_SIZE[0], justify="left").pack(
            fill="x", anchor="w"
        )

    # --- display -----------------------------------------------------------

    @property
    def showing_wishlist(self) -> bool:
        return self.view_var.get() == WISHLIST

    def _change_view(self) -> None:
        self.tree.selection_set(())
        self.refresh()

    def refresh(self) -> None:
        wishlist = self.showing_wishlist
        self.move_button.configure(text="Mark as owned" if wishlist else "Move to wishlist")

        games = self.db.games()
        self.game_box["values"] = [ALL_GAMES] + games
        game = self.game_var.get()
        if game != ALL_GAMES and game not in games:
            self.game_var.set(ALL_GAMES)
            game = ALL_GAMES

        cards = self.db.search(
            self.search_var.get().strip(), "" if game == ALL_GAMES else game, wishlist=wishlist
        )
        if self._sort_field:
            cards.sort(key=lambda c: _sort_key(getattr(c, self._sort_field)), reverse=self._sort_reverse)

        selected = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for card in cards:
            self.tree.insert(
                "",
                "end",
                iid=str(card.id),
                values=[
                    f"${card.value:,.2f}" if field == "value" else getattr(card, field)
                    for field, *_ in COLUMNS
                ],
            )
        self.tree.selection_set([iid for iid in selected if self.tree.exists(iid)])

        shown_qty = sum(c.quantity for c in cards)
        shown_value = sum(c.quantity * c.value for c in cards)
        owned = self.db.stats()
        wanted = self.db.stats(wishlist=True)
        self.status.set(
            f"Showing {len(cards)} entries ({shown_qty} cards, ${shown_value:,.2f})    |    "
            f"Collection: {owned['entries']} entries, {owned['total_cards']} cards, "
            f"worth ${owned['total_value']:,.2f}    |    "
            f"Wishlist: {wanted['entries']} entries, ${wanted['total_value']:,.2f} to complete"
        )
        self.show_details()

    def show_details(self) -> None:
        ids = self._selected_ids()
        card = self.db.get(ids[0]) if len(ids) == 1 else None
        self._photo_image = None
        if card is None:
            self.photo_label.configure(image="", text="Select a card to see its photo" if not ids
                                       else f"{len(ids)} cards selected")
            self.detail_text.set("")
            self.add_photo_button.state(["disabled"])
            self.open_photo_button.state(["disabled"])
            return

        self.add_photo_button.state(["!disabled"])
        self.add_photo_button.configure(text="Change photo…" if card.image_path else "Add photo…")
        has_file = bool(card.image_path) and Path(card.image_path).exists()
        self.open_photo_button.state(["!disabled"] if has_file else ["disabled"])
        if card.image_path and not has_file:
            self.photo_label.configure(image="", text="Photo file is missing")
        elif card.image_path:
            self._photo_image = load_photo(card.image_path, PREVIEW_SIZE)
            if self._photo_image:
                self.photo_label.configure(image=self._photo_image, text="")
            else:
                self.photo_label.configure(
                    image="", text="Preview not available for this file type.\n"
                                   "Click \"Open photo\", or install Pillow\n(pip install pillow)."
                )
        else:
            self.photo_label.configure(image="", text="No photo yet")

        lines = [card.name]
        lines += [x for x in (card.game, " ".join(filter(None, (card.set_name, f"#{card.number}" if card.number else ""))))
                  if x]
        lines.append(f"{card.quantity} × ${card.value:,.2f} = ${card.quantity * card.value:,.2f}")
        if card.notes:
            lines += ["", card.notes]
        self.detail_text.set("\n".join(lines))

    def sort_by(self, field: str) -> None:
        if self._sort_field == field:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_field, self._sort_reverse = field, False
        self.refresh()

    def _selected_ids(self) -> list[int]:
        return [int(iid) for iid in self.tree.selection()]

    # --- actions -----------------------------------------------------------

    def _save_photo(self, card: Card, photo: str | None) -> None:
        if photo is None:
            return
        try:
            self.db.set_image(card, photo or None)
        except (ValueError, OSError) as e:
            messagebox.showerror("Photo not saved", f"The card was saved, but its photo wasn't:\n{e}")

    def add_card(self) -> None:
        dialog = CardDialog(self.root, "Add card", self.db.games(), Card(name="", wishlist=self.showing_wishlist))
        if dialog.result:
            self.db.add(dialog.result)
            self._save_photo(dialog.result, dialog.photo)
            self.view_var.set(WISHLIST if dialog.result.wishlist else COLLECTION)
            self.refresh()
            self.tree.selection_set(str(dialog.result.id))
            self.tree.see(str(dialog.result.id))

    def edit_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo("Edit card", "Select a card to edit first.")
            return
        card = self.db.get(ids[0])
        dialog = CardDialog(self.root, "Edit card", self.db.games(), card)
        if dialog.result:
            self.db.update(dialog.result)
            self._save_photo(dialog.result, dialog.photo)
            self.refresh()

    def delete_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            return
        if not messagebox.askyesno("Delete", f"Delete {len(ids)} selected card entr{'y' if len(ids) == 1 else 'ies'}?"):
            return
        for card_id in ids:
            self.db.delete(card_id)
        self.refresh()

    def move_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo("Move cards", "Select one or more cards first.")
            return
        for card_id in ids:
            card = self.db.get(card_id)
            card.wishlist = not self.showing_wishlist
            self.db.update(card)
        self.refresh()

    def choose_photo(self) -> None:
        ids = self._selected_ids()
        if len(ids) != 1:
            return
        path = filedialog.askopenfilename(title="Choose card photo", filetypes=IMAGE_FILETYPES)
        if path:
            self._save_photo(self.db.get(ids[0]), path)
            self.show_details()

    def open_photo(self) -> None:
        ids = self._selected_ids()
        card = self.db.get(ids[0]) if len(ids) == 1 else None
        if card and card.image_path:
            try:
                open_file(card.image_path)
            except OSError as e:
                messagebox.showerror("Open photo", str(e))

    def update_prices(self) -> None:
        if self.busy:
            return
        ids = self._selected_ids()
        if ids:
            cards = [self.db.get(i) for i in ids]
        else:
            cards = [self.db.get(int(iid)) for iid in self.tree.get_children()]
            if not cards:
                return
            if not messagebox.askyesno(
                "Update prices", f"No cards selected. Look up prices for all {len(cards)} cards shown?"
            ):
                return
        lookups = [c for c in cards if pricing.supported(c)]
        unsupported = len(cards) - len(lookups)
        if not lookups:
            messagebox.showinfo(
                "Update prices",
                "Price lookup works for cards whose Game is Magic, Pokémon or Yu-Gi-Oh!.",
            )
            return

        def work(report):
            found, failed = [], []
            for i, card in enumerate(lookups, 1):
                report(f"Looking up prices… {i}/{len(lookups)}: {card.name}")
                try:
                    found.append((card, pricing.lookup_price(card)))
                except pricing.PriceLookupError as e:
                    failed.append((card, str(e)))
                time.sleep(0.15)  # stay well within the services' rate limits
            return found, failed

        def done(result, error):
            self.busy = False
            self.price_button.state(["!disabled"])
            if error:
                self.refresh()
                messagebox.showerror("Update prices", f"Price lookup stopped: {error}")
                return
            found, failed = result
            for card, price in found:
                current = self.db.get(card.id)
                if current:  # it may have been deleted while we were looking
                    current.value = price.price
                    self.db.update(current)
            self.refresh()
            lines = [f"Updated {len(found)} of {len(cards)} cards."]
            if unsupported:
                lines.append(f"Skipped {unsupported} from games without price lookup.")
            if failed:
                lines.append(f"\nCouldn't price {len(failed)}:")
                lines += [f"  • {c.name}: {msg}" for c, msg in failed[:10]]
                if len(failed) > 10:
                    lines.append(f"  …and {len(failed) - 10} more")
            messagebox.showinfo("Update prices", "\n".join(lines))

        self.busy = True
        self.price_button.state(["disabled"])
        run_in_background(self, work, done, on_progress=self.status.set)

    def export_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV files", "*.csv")], initialfile="card_collection.csv"
        )
        if path:
            count = self.db.export_csv(path)
            messagebox.showinfo("Export complete", f"Exported {count} entries (collection and wishlist) to\n{path}")

    def import_csv(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("CSV files", "*.csv"), ("All files", "*.*")])
        if not path:
            return
        try:
            count = self.db.import_csv(path)
        except (ValueError, OSError) as e:
            messagebox.showerror("Import failed", str(e))
        else:
            messagebox.showinfo("Import complete", f"Imported {count} cards.")
        self.refresh()


def _sort_key(value):
    return value.lower() if isinstance(value, str) else value


def main(db_path=None) -> None:
    db = CardDatabase(db_path) if db_path else CardDatabase()
    root = tk.Tk()
    CardLoggerApp(root, db)
    try:
        root.mainloop()
    finally:
        db.close()

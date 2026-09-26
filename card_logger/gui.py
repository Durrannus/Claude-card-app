"""Desktop window for browsing and editing the card collection."""

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .db import CONDITIONS, Card, CardDatabase

ALL_GAMES = "All games"

# (field, heading, width, anchor)
COLUMNS = [
    ("name", "Name", 200, "w"),
    ("game", "Game", 110, "w"),
    ("set_name", "Set", 140, "w"),
    ("number", "#", 60, "center"),
    ("rarity", "Rarity", 90, "w"),
    ("condition", "Condition", 100, "w"),
    ("quantity", "Qty", 50, "center"),
    ("value", "Value", 80, "e"),
    ("date_added", "Added", 90, "center"),
]


class CardDialog(tk.Toplevel):
    """Form for adding a new card or editing an existing one."""

    def __init__(self, parent, title: str, games: list[str], card: Card | None = None):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self.resizable(False, False)
        self.result: Card | None = None
        self._card = card or Card(name="")

        form = ttk.Frame(self, padding=12)
        form.pack(fill="both", expand=True)

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

        rows = [
            ("Name *", ttk.Entry(form, textvariable=self.vars["name"], width=36)),
            ("Game", ttk.Combobox(form, textvariable=self.vars["game"], values=games, width=34)),
            ("Set", ttk.Entry(form, textvariable=self.vars["set_name"], width=36)),
            ("Card number", ttk.Entry(form, textvariable=self.vars["number"], width=36)),
            ("Rarity", ttk.Entry(form, textvariable=self.vars["rarity"], width=36)),
            ("Condition", ttk.Combobox(form, textvariable=self.vars["condition"], values=CONDITIONS, width=34)),
            ("Quantity", ttk.Spinbox(form, textvariable=self.vars["quantity"], from_=0, to=9999, width=34)),
            ("Value (each)", ttk.Entry(form, textvariable=self.vars["value"], width=36)),
        ]
        for i, (label, widget) in enumerate(rows):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky="w", pady=3, padx=(0, 8))
            widget.grid(row=i, column=1, sticky="ew", pady=3)

        ttk.Label(form, text="Notes").grid(row=len(rows), column=0, sticky="nw", pady=3)
        self.notes = tk.Text(form, width=36, height=4, wrap="word")
        self.notes.insert("1.0", self._card.notes)
        self.notes.grid(row=len(rows), column=1, sticky="ew", pady=3)

        buttons = ttk.Frame(form)
        buttons.grid(row=len(rows) + 1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Save", command=self._save).pack(side="right", padx=(0, 6))

        self.bind("<Return>", lambda e: self._save() if e.widget is not self.notes else None)
        self.bind("<Escape>", lambda e: self.destroy())
        rows[0][1].focus_set()
        self.grab_set()
        self.wait_window()

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

        self.result = Card(
            id=self._card.id,
            name=name,
            game=self.vars["game"].get().strip(),
            set_name=self.vars["set_name"].get().strip(),
            number=self.vars["number"].get().strip(),
            rarity=self.vars["rarity"].get().strip(),
            condition=self.vars["condition"].get().strip(),
            quantity=quantity,
            value=value,
            notes=self.notes.get("1.0", "end").strip(),
            date_added=self._card.date_added,
        )
        self.destroy()


class CardLoggerApp(ttk.Frame):
    def __init__(self, root: tk.Tk, db: CardDatabase):
        super().__init__(root, padding=8)
        self.root = root
        self.db = db
        root.title("Card Collection Logger")
        root.geometry("1000x600")
        root.minsize(700, 400)
        self.pack(fill="both", expand=True)

        self._build_toolbar()
        self._build_table()
        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, anchor="w").pack(fill="x", pady=(6, 0))
        self.refresh()

    def _build_toolbar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 6))

        ttk.Label(bar, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self.refresh())
        search = ttk.Entry(bar, textvariable=self.search_var, width=28)
        search.pack(side="left", padx=(4, 10))

        self.game_var = tk.StringVar(value=ALL_GAMES)
        self.game_box = ttk.Combobox(bar, textvariable=self.game_var, state="readonly", width=18)
        self.game_box.pack(side="left")
        self.game_box.bind("<<ComboboxSelected>>", lambda _: self.refresh())

        for text, cmd in [
            ("Export CSV", self.export_csv),
            ("Import CSV", self.import_csv),
            ("Delete", self.delete_selected),
            ("Edit", self.edit_selected),
            ("Add card", self.add_card),
        ]:
            ttk.Button(bar, text=text, command=cmd).pack(side="right", padx=2)

    def _build_table(self) -> None:
        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            frame, columns=[c[0] for c in COLUMNS], show="headings", selectmode="extended"
        )
        for field, heading, width, anchor in COLUMNS:
            self.tree.heading(field, text=heading, command=lambda f=field: self.sort_by(f))
            self.tree.column(field, width=width, anchor=anchor)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", lambda _: self.edit_selected())
        self.tree.bind("<Delete>", lambda _: self.delete_selected())
        self._sort_field = None
        self._sort_reverse = False

    def refresh(self) -> None:
        games = self.db.games()
        self.game_box["values"] = [ALL_GAMES] + games
        game = self.game_var.get()
        if game != ALL_GAMES and game not in games:
            self.game_var.set(ALL_GAMES)
            game = ALL_GAMES

        cards = self.db.search(self.search_var.get().strip(), "" if game == ALL_GAMES else game)
        if self._sort_field:
            cards.sort(key=lambda c: _sort_key(getattr(c, self._sort_field)), reverse=self._sort_reverse)

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

        shown_qty = sum(c.quantity for c in cards)
        shown_value = sum(c.quantity * c.value for c in cards)
        total = self.db.stats()
        self.status.set(
            f"Showing {len(cards)} entries ({shown_qty} cards, ${shown_value:,.2f})    |    "
            f"Whole collection: {total['entries']} entries, {total['total_cards']} cards, "
            f"${total['total_value']:,.2f}"
        )

    def sort_by(self, field: str) -> None:
        if self._sort_field == field:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_field, self._sort_reverse = field, False
        self.refresh()

    def _selected_ids(self) -> list[int]:
        return [int(iid) for iid in self.tree.selection()]

    def add_card(self) -> None:
        dialog = CardDialog(self.root, "Add card", self.db.games())
        if dialog.result:
            self.db.add(dialog.result)
            self.refresh()

    def edit_selected(self) -> None:
        ids = self._selected_ids()
        if not ids:
            messagebox.showinfo("Edit card", "Select a card to edit first.")
            return
        card = self.db.get(ids[0])
        dialog = CardDialog(self.root, "Edit card", self.db.games(), card)
        if dialog.result:
            self.db.update(dialog.result)
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

    def export_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV files", "*.csv")], initialfile="card_collection.csv"
        )
        if path:
            count = self.db.export_csv(path)
            messagebox.showinfo("Export complete", f"Exported {count} entries to\n{path}")

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

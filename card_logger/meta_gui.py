"""The "Meta tracker" tab: record tournament decklists and see what's played."""

import re
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

from .db import Card, CardDatabase
from .meta import (
    BATTLEFIELDS, CHAMPION, LEGEND, MAIN, RUNES, SECTIONS, Deck, MetaTracker, card_key, parse_decklist,
    typical_copies,
)

ALL_LEGENDS = "All legends"
TOP_CHOICES = {"All placements": None, "Winners only": 1, "Top 4": 4, "Top 8": 8, "Top 16": 16, "Top 32": 32}

DECK_COLUMNS = [
    ("date", "Date", 95, "center"),
    ("event", "Event", 150, "w"),
    ("player", "Player", 100, "w"),
    ("legend", "Legend", 170, "w"),
    ("placement", "Place", 50, "center"),
]
USAGE_COLUMNS = [
    ("name", "Card", 200, "w"),
    ("section", "Section", 90, "w"),
    ("decks", "Decks", 55, "center"),
    ("share", "% of decks", 90, "center"),
    ("avg", "Avg copies", 90, "center"),
    ("owned", "You own", 75, "center"),
]
LEGEND_COLUMNS = [
    ("legend", "Legend", 260, "w"),
    ("decks", "Decks", 60, "center"),
    ("share", "Meta share", 90, "center"),
    ("best", "Best finish", 90, "center"),
]


def _parse_placement(text: str) -> int | None:
    """'1', '1st', 'Top 8' -> 1, 1, 8. Blank -> None."""
    digits = re.findall(r"\d+", text)
    if not text.strip():
        return None
    if not digits:
        raise ValueError("Placement should be a number, like 1 or 8")
    return int(digits[-1])


def _valid_date(text: str) -> bool:
    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False


def _summary(deck: Deck) -> str:
    counts = {s: sum(c.quantity for c in deck.cards if c.section == s) for s in SECTIONS}
    parts = [f"Legend: {deck.legend or '(none found)'}",
             f"{counts[CHAMPION] + counts[MAIN]} main deck cards"]
    if counts[RUNES]:
        parts.append(f"{counts[RUNES]} runes")
    if counts[BATTLEFIELDS]:
        parts.append(f"{counts[BATTLEFIELDS]} battlefields")
    return " · ".join(parts)


class DeckDialog(tk.Toplevel):
    """Paste a decklist and enter where it came from."""

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Add decklist")
        self.transient(parent)
        self.result: Deck | None = None

        form = ttk.Frame(self, padding=12)
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)
        form.rowconfigure(6, weight=1)

        self.vars = {k: tk.StringVar() for k in ("event", "player", "placement", "date", "legend", "name")}
        self.vars["date"].set(date.today().isoformat())
        fields = [
            ("Event", "event", "e.g. Regional Qualifier Barcelona"),
            ("Player", "player", ""),
            ("Placement", "placement", "1 = winner, 8 = top 8. Leave blank if unknown"),
            ("Date", "date", "YYYY-MM-DD"),
            ("Legend", "legend", "Filled in from the decklist if left blank"),
            ("Deck name", "name", "optional"),
        ]
        for i, (label, key, hint) in enumerate(fields):
            ttk.Label(form, text=label).grid(row=i, column=0, sticky="w", padx=(0, 8), pady=2)
            ttk.Entry(form, textvariable=self.vars[key], width=40).grid(row=i, column=1, sticky="ew", pady=2)
            ttk.Label(form, text=hint, foreground="#666").grid(row=i, column=2, sticky="w", padx=(8, 0))

        ttk.Label(form, text="Decklist\n(paste the\nexported text)").grid(row=6, column=0, sticky="nw", pady=(8, 0))
        text_frame = ttk.Frame(form)
        text_frame.grid(row=6, column=1, columnspan=2, sticky="nsew", pady=(8, 0))
        self.text = tk.Text(text_frame, width=70, height=20, wrap="none", undo=True)
        scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.text.bind("<<Modified>>", self._update_preview)

        self.preview = tk.StringVar(value="Paste a decklist above.")
        ttk.Label(form, textvariable=self.preview, foreground="#555").grid(
            row=7, column=1, columnspan=2, sticky="w", pady=(4, 0)
        )

        buttons = ttk.Frame(form)
        buttons.grid(row=8, column=0, columnspan=3, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Save deck", command=self._save).pack(side="right", padx=(0, 6))
        self.bind("<Escape>", lambda e: self.destroy())

        self.text.focus_set()
        self.grab_set()
        self.wait_window()

    def _update_preview(self, _event=None) -> None:
        self.text.edit_modified(False)
        deck = parse_decklist(self.text.get("1.0", "end"))
        self.preview.set(_summary(deck) if deck.cards else "Paste a decklist above.")

    def _save(self) -> None:
        deck = parse_decklist(self.text.get("1.0", "end"))
        if not deck.cards:
            messagebox.showerror("No cards", "Paste the decklist text first.", parent=self)
            return
        try:
            deck.placement = _parse_placement(self.vars["placement"].get())
        except ValueError as e:
            messagebox.showerror("Placement", str(e), parent=self)
            return
        deck.date = self.vars["date"].get().strip() or date.today().isoformat()
        if not _valid_date(deck.date):
            messagebox.showerror("Date", "Enter the date as YYYY-MM-DD, e.g. 2026-09-26.", parent=self)
            return
        deck.event = self.vars["event"].get().strip()
        deck.player = self.vars["player"].get().strip()
        deck.name = self.vars["name"].get().strip()
        deck.legend = self.vars["legend"].get().strip() or deck.legend
        self.result = deck
        self.destroy()


class MetaTrackerTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, db: CardDatabase, on_collection_changed=None):
        super().__init__(parent, padding=8)
        self.db = db
        self.meta = MetaTracker(db)
        self.on_collection_changed = on_collection_changed or (lambda: None)
        self._usage = []

        self._build_filters()
        self._build_body()
        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, anchor="w").pack(fill="x", pady=(6, 0))
        self.refresh()

    # --- layout ------------------------------------------------------------

    def _build_filters(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 4))
        ttk.Label(bar, text="Legend:").pack(side="left")
        self.legend_var = tk.StringVar(value=ALL_LEGENDS)
        self.legend_box = ttk.Combobox(bar, textvariable=self.legend_var, state="readonly", width=30)
        self.legend_box.pack(side="left", padx=(4, 12))
        self.legend_box.bind("<<ComboboxSelected>>", lambda _: self.refresh())

        ttk.Label(bar, text="Finish:").pack(side="left")
        self.top_var = tk.StringVar(value="All placements")
        top_box = ttk.Combobox(bar, textvariable=self.top_var, values=list(TOP_CHOICES), state="readonly", width=14)
        top_box.pack(side="left", padx=(4, 12))
        top_box.bind("<<ComboboxSelected>>", lambda _: self.refresh())

        ttk.Label(bar, text="Since (YYYY-MM-DD):").pack(side="left")
        self.since_var = tk.StringVar()
        since = ttk.Entry(bar, textvariable=self.since_var, width=12)
        since.pack(side="left", padx=(4, 12))
        since.bind("<Return>", lambda _: self.refresh())
        since.bind("<FocusOut>", lambda _: self.refresh())

        self.runes_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Include runes", variable=self.runes_var, command=self.refresh).pack(side="left")

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(0, 6))
        ttk.Button(actions, text="Add decklist…", command=self.add_deck).pack(side="left", padx=(0, 2))
        ttk.Button(actions, text="Import .txt files…", command=self.import_files).pack(side="left", padx=2)
        ttk.Button(actions, text="View deck", command=self.view_deck).pack(side="left", padx=2)
        ttk.Button(actions, text="Delete deck", command=self.delete_decks).pack(side="left", padx=2)
        ttk.Button(actions, text="Add missing cards to wishlist", command=self.add_missing_to_wishlist).pack(
            side="right", padx=2
        )

    def _make_tree(self, parent, columns, height=None) -> ttk.Treeview:
        frame = ttk.Frame(parent)
        tree = ttk.Treeview(frame, columns=[c[0] for c in columns], show="headings",
                            selectmode="extended", **({"height": height} if height else {}))
        for key, heading, width, anchor in columns:
            tree.heading(key, text=heading)
            tree.column(key, width=width, anchor=anchor)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.bind("<Control-a>", lambda _: tree.selection_set(tree.get_children()))
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        frame.pack(fill="both", expand=True)
        return tree

    def _build_body(self) -> None:
        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)

        decks = ttk.Labelframe(body, text="Decklists", padding=4)
        self.deck_tree = self._make_tree(decks, DECK_COLUMNS)
        self.deck_tree.bind("<Double-1>", lambda _: self.view_deck())
        self.deck_tree.bind("<Delete>", lambda _: self.delete_decks())
        body.add(decks, weight=2)

        stats = ttk.Notebook(body)
        usage_tab = ttk.Frame(stats, padding=4)
        self.usage_tree = self._make_tree(usage_tab, USAGE_COLUMNS)
        self.usage_tree.tag_configure("have", foreground="#1b7a2e")
        self.usage_tree.tag_configure("short", foreground="#b3261e")
        ttk.Label(usage_tab, foreground="#555", text=(
            "Green: you own enough copies.  Red: you own fewer than the average "
            "number played.  Select cards and click \"Add missing cards to wishlist\"."
        ), wraplength=520).pack(fill="x", pady=(4, 0))
        stats.add(usage_tab, text="Most played cards")

        legend_tab = ttk.Frame(stats, padding=4)
        self.legend_tree = self._make_tree(legend_tab, LEGEND_COLUMNS)
        self.legend_tree.bind("<Double-1>", self._filter_to_legend)
        ttk.Label(legend_tab, foreground="#555",
                  text="Double-click a legend to see the cards its decks play.").pack(fill="x", pady=(4, 0))
        stats.add(legend_tab, text="Legends")
        self.stats_tabs = stats
        self.usage_tab = usage_tab
        body.add(stats, weight=3)

    # --- display -------------------------------------------------------------

    def _filters(self) -> tuple[str, str, int | None]:
        legend = self.legend_var.get()
        since = self.since_var.get().strip()
        if since and not _valid_date(since):
            since = ""
        return ("" if legend == ALL_LEGENDS else legend), since, TOP_CHOICES.get(self.top_var.get())

    def refresh(self) -> None:
        legends = self.meta.legends()
        self.legend_box["values"] = [ALL_LEGENDS] + legends
        if self.legend_var.get() not in self.legend_box["values"]:
            self.legend_var.set(ALL_LEGENDS)
        legend, since, top = self._filters()

        decks = self.meta.decks(legend, since, top)
        self.deck_tree.delete(*self.deck_tree.get_children())
        for d in decks:
            self.deck_tree.insert("", "end", iid=str(d.id), values=[
                d.date, d.event, d.player, d.legend, d.placement or ""
            ])

        self._usage = self.meta.card_usage(legend, since, top, include_runes=self.runes_var.get())
        self.usage_tree.delete(*self.usage_tree.get_children())
        for i, u in enumerate(self._usage):
            tag = "have" if u.owned >= typical_copies(u.avg_copies) else "short"
            self.usage_tree.insert("", "end", iid=str(i), tags=(tag,), values=[
                u.name, u.section, u.decks, f"{u.share:.0%}", f"{u.avg_copies:.1f}", u.owned
            ])

        self.legend_tree.delete(*self.legend_tree.get_children())
        for s in self.meta.legend_shares(since, top):
            self.legend_tree.insert("", "end", values=[
                s.legend, s.decks, f"{s.share:.0%}", s.best_placement or ""
            ])

        events = {d.event for d in decks if d.event}
        shown = f"{len(decks)} decklists from {len(events)} events"
        total = len(self.meta.decks())
        self.status.set(shown if len(decks) == total else f"{shown} (filtered from {total})")

    def _filter_to_legend(self, _event=None) -> None:
        sel = self.legend_tree.selection()
        if sel:
            self.legend_var.set(self.legend_tree.item(sel[0], "values")[0])
            self.refresh()
            self.stats_tabs.select(self.usage_tab)

    # --- actions -------------------------------------------------------------

    def add_deck(self) -> None:
        dialog = DeckDialog(self.winfo_toplevel())
        if dialog.result:
            self.meta.add_deck(dialog.result)
            self.refresh()

    def import_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Choose decklist files (one deck per file)",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not paths:
            return
        event = simpledialog.askstring(
            "Import decklists", "Event name for these decks (optional):", parent=self.winfo_toplevel()
        )
        if event is None:
            return
        added, skipped = 0, []
        for path in paths:
            try:
                deck = parse_decklist(Path(path).read_text(encoding="utf-8-sig", errors="replace"))
            except OSError as e:
                skipped.append(f"{Path(path).name}: {e}")
                continue
            if not deck.cards:
                skipped.append(f"{Path(path).name}: no cards found")
                continue
            deck.name = Path(path).stem
            deck.event = event.strip()
            self.meta.add_deck(deck)
            added += 1
        self.refresh()
        message = f"Imported {added} decklist{'s' if added != 1 else ''}."
        if skipped:
            message += "\n\nSkipped:\n" + "\n".join(skipped[:10])
        messagebox.showinfo("Import decklists", message)

    def _selected_deck_ids(self) -> list[int]:
        return [int(i) for i in self.deck_tree.selection()]

    def view_deck(self) -> None:
        ids = self._selected_deck_ids()
        if not ids:
            messagebox.showinfo("View deck", "Select a decklist first.")
            return
        deck = self.meta.get_deck(ids[0])
        owned = self.meta.owned_counts()
        win = tk.Toplevel(self)
        win.title(" – ".join(filter(None, [deck.legend, deck.player, deck.event])) or "Deck")
        text = tk.Text(win, width=60, height=34, wrap="word")
        text.pack(fill="both", expand=True)
        text.tag_configure("heading", font=("TkDefaultFont", 10, "bold"))
        text.tag_configure("short", foreground="#b3261e")
        header = [deck.date, deck.event, deck.player,
                  f"Placed {deck.placement}" if deck.placement else "", deck.name]
        text.insert("end", " · ".join(filter(None, header)) + "\n")
        text.insert("end", "Red = you own fewer copies than this list plays.\n")
        for section in SECTIONS:
            cards = [c for c in deck.cards if c.section == section]
            if not cards:
                continue
            text.insert("end", f"\n{section} ({sum(c.quantity for c in cards)})\n", "heading")
            for c in cards:
                have = owned.get(card_key(c.name), 0)
                line = f"{c.quantity}  {c.name}    (you own {have})\n"
                text.insert("end", line, "short" if have < c.quantity and section != LEGEND else ())
        text.configure(state="disabled")

    def delete_decks(self) -> None:
        ids = self._selected_deck_ids()
        if not ids:
            return
        if not messagebox.askyesno("Delete", f"Delete {len(ids)} decklist{'s' if len(ids) != 1 else ''}?"):
            return
        for deck_id in ids:
            self.meta.delete_deck(deck_id)
        self.refresh()

    def add_missing_to_wishlist(self) -> None:
        selected = [self._usage[int(i)] for i in self.usage_tree.selection()]
        if not selected:
            messagebox.showinfo(
                "Add to wishlist", "Select cards in the \"Most played cards\" list first (Ctrl+A selects all)."
            )
            return
        wishlist = {card_key(c.name): c for c in self.db.search(wishlist=True)
                    if not c.game or "riftbound" in card_key(c.game)}
        added = updated = 0
        for u in selected:
            missing = typical_copies(u.avg_copies) - u.owned
            if missing <= 0:
                continue
            existing = wishlist.get(card_key(u.name))
            if existing:
                if existing.quantity != missing:
                    existing.quantity = missing
                    self.db.update(existing)
                    updated += 1
            else:
                self.db.add(Card(name=u.name, game="Riftbound", quantity=missing, wishlist=True,
                                 notes="Added from meta tracker"))
                added += 1
        self.on_collection_changed()
        messagebox.showinfo(
            "Add to wishlist",
            f"Added {added} card{'s' if added != 1 else ''} to your wishlist"
            + (f" and updated {updated}" if updated else "") + "."
            + ("" if added or updated else "\nYou already own enough of the selected cards."),
        )

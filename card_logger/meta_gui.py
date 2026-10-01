"""The "Meta tracker" tab: record tournament decklists and see what's played."""

import re
import tkinter as tk
from datetime import date
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk

import webbrowser

from . import riftboundgg, riotnews, theme, topdeck
from .db import Card, CardDatabase
from .meta import (
    BATTLEFIELDS, CHAMPION, LEGEND, MAIN, RUNES, SECTIONS, Deck, MetaTracker, card_key, parse_decklist,
    typical_copies,
)

ALL_LEGENDS = "All legends"
TOP_CHOICES = {"All placements": None, "Winners only": 1, "Top 4": 4, "Top 8": 8, "Top 16": 16, "Top 32": 32}

DECK_COLUMNS = [
    ("date", "Date", 95, "center"),
    ("event", "Event", 140, "w"),
    ("player", "Player", 90, "w"),
    ("legend", "Legend", 170, "w"),
    ("placement", "Place", 62, "center"),
]
USAGE_COLUMNS = [
    ("name", "Card", 200, "w"),
    ("section", "Section", 90, "w"),
    ("decks", "Decks", 68, "center"),
    ("share", "Play rate", 90, "center"),
    ("avg", "Avg copies", 100, "center"),
    ("owned", "Owned", 72, "center"),
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

        form = ttk.Frame(self, padding=20)
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)
        form.rowconfigure(7, weight=1)
        ttk.Label(form, text="Add decklist", style="Section.TLabel", font=theme.font(14, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10)
        )

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
        for i, (label, key, hint) in enumerate(fields, start=1):
            ttk.Label(form, text=label, style="Muted.TLabel").grid(row=i, column=0, sticky="w", padx=(0, 12), pady=4)
            ttk.Entry(form, textvariable=self.vars[key], width=40).grid(row=i, column=1, sticky="ew", pady=4)
            ttk.Label(form, text=hint, style="Muted.TLabel", font=theme.font(9)).grid(
                row=i, column=2, sticky="w", padx=(10, 0)
            )

        ttk.Label(form, text="Decklist\n(paste the\nexported text)", style="Muted.TLabel").grid(
            row=7, column=0, sticky="nw", pady=(10, 0)
        )
        text_frame = ttk.Frame(form)
        text_frame.grid(row=7, column=1, columnspan=2, sticky="nsew", pady=(10, 0))
        self.text = tk.Text(text_frame, width=70, height=18, wrap="none", undo=True)
        theme.style_text(self.text)
        scroll = ttk.Scrollbar(text_frame, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        self.text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.text.bind("<<Modified>>", self._update_preview)

        self.preview = tk.StringVar(value="Paste a decklist above.")
        ttk.Label(form, textvariable=self.preview, style="Good.TLabel").grid(
            row=8, column=1, columnspan=2, sticky="w", pady=(6, 0)
        )

        buttons = ttk.Frame(form)
        buttons.grid(row=9, column=0, columnspan=3, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Save deck", style="Accent.TButton", command=self._save).pack(side="right", padx=(0, 8))
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


class ImportDialog(tk.Toplevel):
    """Choose where to import decklists from."""

    def __init__(self, parent, db: CardDatabase):
        super().__init__(parent)
        self.title("Import decklists")
        self.transient(parent)
        self.resizable(False, False)
        self.db = db
        self.accepted = False

        form = ttk.Frame(self, padding=20)
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="Import decklists", style="Section.TLabel", font=theme.font(14, "bold")).grid(
            row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(form, style="Muted.TLabel", wraplength=470, justify="left", text=(
            "Decklists you've already imported are skipped, so importing again only adds new ones."
        )).grid(row=1, column=0, columnspan=3, sticky="w", pady=(4, 10))

        self.days = tk.StringVar(value=db.get_setting("import_days", "30"))
        ttk.Label(form, text="Look back (days)", style="Muted.TLabel").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Spinbox(form, textvariable=self.days, from_=1, to=90, width=8).grid(row=2, column=1, sticky="w")

        # TopDeck.gg: recent tournaments with placings, records and decklists.
        self.topdeck = tk.BooleanVar(value=db.get_setting("import_topdeck", "1") == "1")
        self.key = tk.StringVar(value=db.get_setting("topdeck_key"))
        self.min_players = tk.StringVar(value=db.get_setting("import_min_players", "8"))
        ttk.Checkbutton(form, text="Tournaments from TopDeck.gg: placings, win/loss records and decklists",
                        variable=self.topdeck).grid(row=3, column=0, columnspan=3, sticky="w", pady=(12, 2))
        ttk.Label(form, text="API key", style="Muted.TLabel").grid(row=4, column=0, sticky="w", padx=(22, 8))
        ttk.Entry(form, textvariable=self.key, width=34, show="•").grid(row=4, column=1, sticky="ew")
        ttk.Button(form, text="Get a free key ↗", style="Small.TButton",
                   command=lambda: webbrowser.open(topdeck.KEY_PAGE)).grid(row=4, column=2, padx=(8, 0))
        ttk.Label(form, text="Minimum players", style="Muted.TLabel").grid(row=5, column=0, sticky="w",
                                                                         padx=(22, 8), pady=4)
        ttk.Spinbox(form, textvariable=self.min_players, from_=2, to=4096, width=8).grid(row=5, column=1, sticky="w")
        ttk.Label(form, style="Muted.TLabel", font=theme.font(9), wraplength=450, justify="left", text=(
            "The key is free: sign in at TopDeck.gg's developer page and create one. It's saved only on this "
            "computer. Data provided by TopDeck.gg."
        )).grid(row=6, column=0, columnspan=3, sticky="w", padx=(22, 0))

        # riftbound.gg: community decks, plus its (older) tournament decks.
        self.community = tk.BooleanVar(value=db.get_setting("import_community", "1") == "1")
        self.tournaments = tk.BooleanVar(value=db.get_setting("import_tournaments", "1") == "1")
        ttk.Checkbutton(form, text="Community decks from riftbound.gg: what players are building right now",
                        variable=self.community).grid(row=7, column=0, columnspan=3, sticky="w", pady=(12, 2))
        ttk.Checkbutton(form, text="Tournament decks from riftbound.gg", variable=self.tournaments).grid(
            row=8, column=0, columnspan=3, sticky="w")
        ttk.Label(form, style="Muted.TLabel", font=theme.font(9), wraplength=450, justify="left", text=(
            "riftbound.gg keeps only its newest few days of community decks and its tournament uploads lag, "
            "so importing daily builds up a longer history. It asks apps not to rush it, so this part takes "
            "a minute or two."
        )).grid(row=9, column=0, columnspan=3, sticky="w", padx=(22, 0))

        # Riot's own "Top Decks" articles: the only published Regional Qualifier decklists.
        self.riot = tk.BooleanVar(value=db.get_setting("import_riot", "1") == "1")
        ttk.Checkbutton(form, text="Official Regional Qualifier decklists from Riot (playriftbound.com)",
                        variable=self.riot).grid(row=10, column=0, columnspan=3, sticky="w", pady=(12, 2))
        ttk.Label(form, style="Muted.TLabel", font=theme.font(9), wraplength=450, justify="left", text=(
            "A few days after each Regional Qualifier, Riot posts its Top 8 and the best-placed deck of every "
            "other legend, with final rankings."
        )).grid(row=11, column=0, columnspan=3, sticky="w", padx=(22, 0))

        self.auto = tk.BooleanVar(value=db.get_setting("auto_import") == "1")
        ttk.Checkbutton(form, text="Import new decklists automatically each day", variable=self.auto).grid(
            row=12, column=0, columnspan=3, sticky="w", pady=(14, 0))

        buttons = ttk.Frame(form)
        buttons.grid(row=13, column=0, columnspan=3, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="Import", style="Accent.TButton", command=self._ok).pack(side="right", padx=(0, 8))
        self.bind("<Escape>", lambda e: self.destroy())
        self.bind("<Return>", lambda e: self._ok())
        self.grab_set()
        self.wait_window()

    def _ok(self) -> None:
        try:
            days, players = int(self.days.get()), int(self.min_players.get())
            if days < 1 or players < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Import decklists", "Enter whole numbers above zero.", parent=self)
            return
        if not (self.topdeck.get() or self.community.get() or self.tournaments.get() or self.riot.get()):
            messagebox.showerror("Import decklists", "Tick at least one source.", parent=self)
            return
        if self.topdeck.get() and not self.key.get().strip():
            messagebox.showerror("Import decklists", "TopDeck.gg needs an API key. Click \"Get a free key\", "
                                 "or untick TopDeck.gg.", parent=self)
            return
        for key, value in [("import_days", str(days)), ("import_min_players", str(players)),
                           ("import_topdeck", "1" if self.topdeck.get() else "0"),
                           ("topdeck_key", self.key.get().strip()),
                           ("import_community", "1" if self.community.get() else "0"),
                           ("import_tournaments", "1" if self.tournaments.get() else "0"),
                           ("import_riot", "1" if self.riot.get() else "0"),
                           ("auto_import", "1" if self.auto.get() else "0")]:
            self.db.set_setting(key, value)
        self.accepted = True
        self.destroy()


class MetaTrackerTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, db: CardDatabase, on_collection_changed=None):
        super().__init__(parent, padding=(16, 14, 16, 0))
        self.db = db
        self.meta = MetaTracker(db)
        self.on_collection_changed = on_collection_changed or (lambda: None)
        self._usage = []

        tiles = ttk.Frame(self)
        tiles.pack(fill="x", pady=(0, 14))
        self.tiles = {
            "decks": theme.StatTile(tiles, "Decklists"),
            "events": theme.StatTile(tiles, "Events"),
            "legend": theme.StatTile(tiles, "Most played legend", gold=True),
            "short": theme.StatTile(tiles, "Meta cards you're short on"),
        }
        for i, tile in enumerate(self.tiles.values()):
            tiles.columnconfigure(i, weight=1, uniform="tile")
            tile.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 10, 0))

        self._build_filters()
        self.status = tk.StringVar()
        status_bar = ttk.Frame(self, style="Header.TFrame")
        status_bar.pack(side="bottom", fill="x", pady=(10, 0))
        ttk.Label(status_bar, textvariable=self.status, style="Status.TLabel", anchor="w").pack(
            side="left", fill="x", expand=True)
        # TopDeck.gg's terms ask for a visible credit with a link.
        self.attribution = ttk.Label(status_bar, text=topdeck.ATTRIBUTION + " ↗", style="Status.TLabel",
                                     foreground=theme.GOLD, cursor="hand2")
        self.attribution.bind("<Button-1>", lambda _: webbrowser.open("https://topdeck.gg"))
        self.importing = False
        self._build_body()
        self.refresh()

        if db.get_setting("auto_import") == "1" and db.get_setting("last_auto_import") != date.today().isoformat():
            self.after(2500, lambda: self.import_tournaments(silent=True))

    # --- layout ------------------------------------------------------------

    def _build_filters(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="Legend", style="Muted.TLabel").pack(side="left")
        self.legend_var = tk.StringVar(value=ALL_LEGENDS)
        self.legend_box = ttk.Combobox(bar, textvariable=self.legend_var, state="readonly", width=30)
        self.legend_box.pack(side="left", padx=(6, 16))
        self.legend_box.bind("<<ComboboxSelected>>", lambda _: self.refresh())

        ttk.Label(bar, text="Finish", style="Muted.TLabel").pack(side="left")
        self.top_var = tk.StringVar(value="All placements")
        top_box = ttk.Combobox(bar, textvariable=self.top_var, values=list(TOP_CHOICES), state="readonly", width=14)
        top_box.pack(side="left", padx=(6, 16))
        top_box.bind("<<ComboboxSelected>>", lambda _: self.refresh())

        ttk.Label(bar, text="Since", style="Muted.TLabel").pack(side="left")
        self.since_var = tk.StringVar()
        since = ttk.Entry(bar, textvariable=self.since_var, width=12)
        since.pack(side="left", padx=(6, 4))
        ttk.Label(bar, text="YYYY-MM-DD", style="Muted.TLabel", font=theme.font(9)).pack(side="left", padx=(0, 16))
        since.bind("<Return>", lambda _: self.refresh())
        since.bind("<FocusOut>", lambda _: self.refresh())

        self.runes_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Include runes", variable=self.runes_var, command=self.refresh).pack(side="left")

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(0, 10))
        self.import_button = ttk.Button(actions, text="⬇  Import decklists", style="Accent.TButton",
                                        command=self.ask_import)
        self.import_button.pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="+  Add decklist", command=self.add_deck).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Import .txt files…", command=self.import_files).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="View deck", command=self.view_deck).pack(side="left", padx=(0, 6))
        ttk.Button(actions, text="Delete deck", style="Danger.TButton", command=self.delete_decks).pack(side="left")
        ttk.Button(actions, text="★  Add missing cards to wishlist", command=self.add_missing_to_wishlist).pack(
            side="right"
        )

    def _make_tree(self, parent, columns, height=None) -> ttk.Treeview:
        frame = ttk.Frame(parent)
        tree = theme.make_table(frame, columns, flexible=("name", "event", "legend", "player"),
                                selectmode="extended", **({"height": height} if height else {}))
        tree.bind("<Control-a>", lambda _: tree.selection_set(tree.get_children()))
        frame.pack(fill="both", expand=True)
        return tree

    def _build_body(self) -> None:
        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True)

        decks = ttk.Labelframe(body, text="Decklists")
        self.deck_tree = self._make_tree(decks, DECK_COLUMNS)
        self.deck_tree.bind("<Double-1>", lambda _: self.view_deck())
        self.deck_tree.bind("<Delete>", lambda _: self.delete_decks())
        body.add(decks, weight=2)

        stats = ttk.Notebook(body, style="Inner.TNotebook")
        usage_tab = ttk.Frame(stats, style="Header.TFrame", padding=8)
        self.usage_tree = self._make_tree(usage_tab, USAGE_COLUMNS)
        self.usage_tree.tag_configure("have", foreground=theme.GOOD)
        self.usage_tree.tag_configure("short", foreground=theme.BAD)
        ttk.Label(usage_tab, style="Legend.TLabel", text=(
            "Play rate: share of decks that run the card.  Green: you own enough copies.  "
            "Red: you own fewer than the average number played.  Select cards and click \"Add missing cards to wishlist\"."
        ), wraplength=560).pack(fill="x", pady=(8, 0))
        stats.add(usage_tab, text="Most played cards")

        legend_tab = ttk.Frame(stats, style="Header.TFrame", padding=8)
        self.legend_tree = self._make_tree(legend_tab, LEGEND_COLUMNS)
        self.legend_tree.bind("<Double-1>", self._filter_to_legend)
        ttk.Label(legend_tab, style="Legend.TLabel",
                  text="Double-click a legend to see the cards its decks play.").pack(fill="x", pady=(8, 0))
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
        for i, d in enumerate(decks):
            self.deck_tree.insert("", "end", iid=str(d.id), tags=(theme.row_tag(i),), values=[
                d.date, d.event, d.player, d.legend, d.placement or ""
            ])

        self._usage = self.meta.card_usage(legend, since, top, include_runes=self.runes_var.get())
        self.usage_tree.delete(*self.usage_tree.get_children())
        for i, u in enumerate(self._usage):
            tag = "have" if u.owned >= typical_copies(u.avg_copies) else "short"
            self.usage_tree.insert("", "end", iid=str(i), tags=(tag, theme.row_tag(i)), values=[
                u.name, u.section, u.decks, f"{u.share:.0%}", f"{u.avg_copies:.1f}", u.owned
            ])

        self.legend_tree.delete(*self.legend_tree.get_children())
        shares = self.meta.legend_shares(since, top)
        for i, s in enumerate(shares):
            self.legend_tree.insert("", "end", tags=(theme.row_tag(i),), values=[
                s.legend, s.decks, f"{s.share:.0%}", s.best_placement or ""
            ])

        events = {d.event for d in decks if d.event}
        shown = (f"{len(decks)} decklist{'s' if len(decks) != 1 else ''} from "
                 f"{len(events)} event{'s' if len(events) != 1 else ''}")
        total = len(self.meta.decks())
        if not self.importing:
            self.status.set(shown if len(decks) == total else f"{shown} (filtered from {total})")
        if self.meta.known_sources(f"{topdeck.SOURCE}:"):
            self.attribution.pack(side="right")
        else:
            self.attribution.pack_forget()
        self.tiles["decks"].value.set(f"{len(decks):,}")
        self.tiles["events"].value.set(f"{len(events):,}")
        top_legend = shares[0] if shares and not legend else None
        self.tiles["legend"].value.set(
            f"{top_legend.legend.split(',')[0].split(' - ')[0]}  {top_legend.share:.0%}" if top_legend
            else (legend.split(",")[0].split(" - ")[0] if legend else "—")
        )
        self.tiles["short"].value.set(
            f"{sum(1 for u in self._usage if u.owned < typical_copies(u.avg_copies))} of {len(self._usage)}"
        )

    def _filter_to_legend(self, _event=None) -> None:
        sel = self.legend_tree.selection()
        if sel:
            self.legend_var.set(self.legend_tree.item(sel[0], "values")[0])
            self.refresh()
            self.stats_tabs.select(self.usage_tab)

    # --- actions -------------------------------------------------------------

    def ask_import(self) -> None:
        dialog = ImportDialog(self.winfo_toplevel(), self.db)
        if dialog.accepted:
            self.import_tournaments()

    def import_tournaments(self, silent: bool = False) -> None:
        """Download new decklists from the chosen sources in the background."""
        from .gui import run_in_background

        if self.importing:
            return
        get = self.db.get_setting
        days = int(get("import_days", "30"))
        use_topdeck = get("import_topdeck", "1") == "1" and bool(get("topdeck_key"))
        key, min_players = get("topdeck_key"), int(get("import_min_players", "8"))
        tournaments = get("import_tournaments", "1") == "1"
        community = get("import_community", "1") == "1"
        riot = get("import_riot", "1") == "1"
        known = self.meta.known_sources("")

        def work(report):
            results, errors = {}, []
            if use_topdeck:
                try:
                    results["topdeck"] = topdeck.fetch(key, days, min_players, known, report=report)
                except topdeck.TopDeckError as e:
                    errors.append(str(e))
            if riot:
                try:
                    results["riot"] = riotnews.fetch(days, known, report=report)
                except riotnews.FetchError as e:
                    errors.append(str(e))
            if tournaments or community:
                try:
                    results["riftboundgg"] = riftboundgg.fetch(days, known, tournaments=tournaments,
                                                               community=community, report=report)
                except riftboundgg.FetchError as e:
                    errors.append(str(e))
            return results, errors

        def done(result, error):
            self.importing = False
            self.import_button.state(["!disabled"])
            if error:
                self.refresh()
                message = f"Import stopped: {error}"
                self.status.set(message) if silent else messagebox.showerror("Import decklists", message)
                return
            results, errors = result
            lines, notes = [], []
            if "topdeck" in results:
                r = results["topdeck"]
                added = topdeck.save(self.meta, r)
                lines.append(f"TopDeck.gg: {added} decklist{'s' if added != 1 else ''} from "
                             f"{r.tournaments} tournament{'s' if r.tournaments != 1 else ''}")
                if r.without_lists:
                    notes.append(f"{r.without_lists} TopDeck.gg players had no public decklist.")
            if "riot" in results:
                r = results["riot"]
                added = riotnews.save(self.meta, r)
                events = sorted({d.event.replace("Regional Qualifier ", "") for d in r.decks})
                lines.append(f"Riot: {added} official decklist{'s' if added != 1 else ''}"
                             + (f" ({', '.join(events)})" if events else ""))
            if "riftboundgg" in results:
                r = results["riftboundgg"]
                riftboundgg.save(self.meta, r)
                lines.append(f"riftbound.gg: {r.tournament_decks} tournament and {r.community_decks} community "
                             f"deck{'s' if r.community_decks != 1 else ''}")
                if r.skipped_undated:
                    notes.append(f"{r.skipped_undated} riftbound.gg tournament decks were skipped because their "
                                 f"event is older than {days} days (or undated), so they'd distort recent trends.")
            if silent:
                self.db.set_setting("last_auto_import", date.today().isoformat())
            self.refresh()
            summary = "Imported " + "; ".join(lines) + "." if lines else "Nothing was imported."
            if silent:
                self.status.set("Automatic import: " + summary + (" Problem: " + errors[0] if errors else ""))
                return
            text = summary + "".join(f"\n\n{n}" for n in notes) + "".join(f"\n\nProblem: {e}" for e in errors)
            (messagebox.showwarning if errors else messagebox.showinfo)("Import decklists", text)

        self.importing = True
        self.import_button.state(["disabled"])
        run_in_background(self, work, done, on_progress=self.status.set)

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
        theme.style_text(text)
        text.configure(background=theme.SURFACE, padx=16, pady=12, spacing1=2)
        text.pack(fill="both", expand=True)
        text.tag_configure("heading", font=theme.font(11, "bold"), foreground=theme.GOLD, spacing1=10)
        text.tag_configure("short", foreground=theme.BAD)
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

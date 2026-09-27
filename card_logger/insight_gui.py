"""The "Future insight" tab: cards showing early signs of becoming popular."""

import tkinter as tk
import webbrowser
from tkinter import messagebox, ttk

from . import insight, market, theme
from .db import Card, CardDatabase, name_key
from .meta import MetaTracker

LOOK_BACK = {"Last 4 weeks": 4, "Last 6 weeks": 6, "Last 8 weeks": 8, "Last 12 weeks": 12}
ALL, OWNED, NOT_OWNED = "all", "owned", "not_owned"

COLUMNS = [
    ("score", "Watch score", 118, "w"),
    ("name", "Card", 150, "w"),
    ("signs", "Signs", 170, "w"),
    ("play", "Play rate", 96, "center"),
    ("top", "Top finishers", 122, "center"),
    ("win", "Win rate", 92, "center"),
    ("owned", "Own", 58, "center"),
]
LEGEND_COLUMNS = [
    ("legend", "Legend", 260, "w"),
    ("share", "Meta share", 118, "center"),
    ("change", "Change", 90, "center"),
    ("trend", "Trend", 110, "center"),
]


def _bar(score: float) -> str:
    filled = round(score / 20)
    return f"{score:>3.0f}  " + "▮" * filled + "▯" * (5 - filled)


class InsightTab(ttk.Frame):
    def __init__(self, parent: tk.Misc, db: CardDatabase, get_latest=None, on_data_changed=None):
        super().__init__(parent, padding=(16, 14, 16, 0))
        self.db = db
        self.meta = MetaTracker(db)
        self.get_latest = get_latest
        self.on_data_changed = on_data_changed or (lambda: None)
        self.report = insight.Report()
        self.shown: list[insight.Candidate] = []

        tiles = ttk.Frame(self)
        tiles.pack(fill="x", pady=(0, 14))
        self.tiles = {
            "watch": theme.StatTile(tiles, "Cards to watch (score 30+)", gold=True),
            "legends": theme.StatTile(tiles, "Rising legends"),
            "decks": theme.StatTile(tiles, "Decklists analysed"),
            "data": theme.StatTile(tiles, "With placings / match records"),
        }
        for i, tile in enumerate(self.tiles.values()):
            tiles.columnconfigure(i, weight=1, uniform="tile")
            tile.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else 10, 0))

        bar = ttk.Frame(self)
        bar.pack(fill="x", pady=(0, 10))
        self.view_var = tk.StringVar(value=ALL)
        for text, value in [("All cards", ALL), ("Cards I own", OWNED), ("Cards I don't own", NOT_OWNED)]:
            ttk.Radiobutton(bar, text=text, value=value, variable=self.view_var, style="Segment.TRadiobutton",
                            command=self._fill).pack(side="left")
        ttk.Label(bar, text="Look at", style="Muted.TLabel").pack(side="left", padx=(20, 6))
        self.weeks_var = tk.StringVar(value="Last 6 weeks")
        box = ttk.Combobox(bar, textvariable=self.weeks_var, values=list(LOOK_BACK), state="readonly", width=13)
        box.pack(side="left")
        box.bind("<<ComboboxSelected>>", lambda _: self.refresh())
        if get_latest:
            ttk.Button(bar, text="⬇  Get latest data", style="Accent.TButton", command=get_latest).pack(side="right")

        self.status = tk.StringVar()
        ttk.Label(self, textvariable=self.status, style="Status.TLabel", anchor="w").pack(
            side="bottom", fill="x", pady=(10, 0))

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)
        details = ttk.Frame(body, style="Card.TFrame", padding=14, width=theme.px(336))
        details.pack(side="right", fill="y", padx=(12, 0))
        details.pack_propagate(False)
        self._build_details(details)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        legends = ttk.Frame(left, style="Card.TFrame", padding=(12, 8))
        legends.pack(side="bottom", fill="x", pady=(12, 0))
        heading = ttk.Frame(legends, style="Header.TFrame")
        heading.pack(anchor="w")
        ttk.Label(heading, text="Legends on the move", style="CardSection.TLabel").pack(side="left")
        ttk.Label(heading, text="  meta share, second half of the period vs first half",
                  style="CardMuted.TLabel").pack(side="left")
        self.legend_tree = self._tree(legends, LEGEND_COLUMNS, height=3)

        table = ttk.Frame(left, style="Card.TFrame", padding=1)
        table.pack(side="top", fill="both", expand=True)
        self.tree = self._tree(table, COLUMNS)
        self.tree.tag_configure("hot", foreground=theme.GOLD)
        self.tree.tag_configure("warm", foreground=theme.TEXT)
        self.tree.tag_configure("mild", foreground=theme.MUTED)
        self.tree.bind("<<TreeviewSelect>>", lambda _: self.show_details())
        self.refresh()

    def _tree(self, parent, columns, height=None) -> ttk.Treeview:
        frame = ttk.Frame(parent, style="Header.TFrame")
        frame.pack(fill="both", expand=True)
        return theme.make_table(frame, columns, flexible=("name", "signs", "legend"),
                                selectmode="extended", **({"height": height} if height else {}))

    def _build_details(self, panel: ttk.Frame) -> None:
        self.d_name = tk.StringVar()
        self.d_score = tk.StringVar()
        ttk.Label(panel, textvariable=self.d_name, style="CardName.TLabel", wraplength=theme.px(306)).pack(anchor="w")
        ttk.Label(panel, textvariable=self.d_score, style="CardSection.TLabel").pack(anchor="w", pady=(2, 8))
        self.signs_frame = ttk.Frame(panel, style="Header.TFrame")
        self.signs_frame.pack(fill="both", expand=True, anchor="n")
        buttons = ttk.Frame(panel, style="Header.TFrame")
        buttons.pack(side="bottom", anchor="w", pady=(8, 0))
        self.wish_button = ttk.Button(buttons, text="★ Add to wishlist", style="Small.TButton",
                                      command=self.add_to_wishlist)
        self.wish_button.pack(side="left")
        self.ebay_button = ttk.Button(buttons, text="eBay ↗", style="Small.TButton", command=self.open_ebay)
        self.ebay_button.pack(side="left", padx=(6, 0))

    # --- display -------------------------------------------------------------

    def refresh(self) -> None:
        self.report = insight.analyse(self.db, self.meta, weeks=LOOK_BACK[self.weeks_var.get()])
        r = self.report
        self.tiles["watch"].value.set(str(sum(1 for c in r.candidates if c.score >= 30)))
        self.tiles["legends"].value.set(str(sum(1 for t in r.legends if t.rising)))
        self.tiles["decks"].value.set(f"{r.decks:,}")
        self.tiles["data"].value.set(f"{r.with_placing:,} / {r.with_record:,}")

        self.legend_tree.delete(*self.legend_tree.get_children())
        for i, t in enumerate(r.legends):
            trend = "Rising" if t.rising else ("Falling" if t.change_points <= -5 and t.z <= -insight.Z_NEEDED
                                               else "Steady")
            self.legend_tree.insert("", "end", tags=(theme.row_tag(i),), values=[
                t.legend, f"{t.previous_share:.0%} → {t.recent_share:.0%}", f"{t.change_points:+.0f} pts", trend])

        self.status.set(r.message or (
            f"Based on {r.decks} decklists from {r.start} to {r.end}. These are early warning signs "
            "to research, not predictions, and can be wrong."))
        self._fill()

    def _fill(self) -> None:
        view = self.view_var.get()
        self.shown = [c for c in self.report.candidates
                      if view == ALL or (view == OWNED) == (c.owned > 0)]
        self.tree.delete(*self.tree.get_children())
        for i, c in enumerate(self.shown):
            tier = "hot" if c.score >= 50 else ("warm" if c.score >= 30 else "mild")
            self.tree.insert("", "end", iid=str(i), tags=(tier, theme.row_tag(i)), values=[
                _bar(c.score),
                c.name,
                c.tags,
                f"{c.share:.0%}" if c.share else "not played",
                f"{c.top_share:.0%}" if c.top_share is not None else "",
                f"{c.win_rate:.0%}" if c.win_rate is not None else "",
                c.owned or "",
            ])
        if self.shown:
            self.tree.selection_set("0")
        self.show_details()

    def show_details(self) -> None:
        for child in self.signs_frame.winfo_children():
            child.destroy()
        sel = self.tree.selection()
        if not sel:
            self.d_name.set("No card selected" if self.shown else "Nothing to watch yet")
            self.d_score.set("")
            ttk.Label(self.signs_frame, style="Card.TLabel", wraplength=theme.px(306), justify="left", text=(
                self.report.message or "No card shows early signs right now. Check back after importing "
                "more tournaments and updating prices.")).pack(anchor="w")
            self.wish_button.state(["disabled"])
            self.ebay_button.state(["disabled"])
            return
        c = self.shown[int(sel[0])]
        self.d_name.set(c.name)
        self.d_score.set(f"Watch score {c.score:.0f} / 100")
        for sign in sorted(c.signs, key=lambda s: -s.points):
            ttk.Label(self.signs_frame, text=f"▸ {sign.kind}", style="Card.TLabel",
                      font=theme.font(10, "bold")).pack(anchor="w", pady=(4, 0))
            ttk.Label(self.signs_frame, text=sign.detail, style="CardMuted.TLabel", wraplength=theme.px(286),
                      justify="left").pack(anchor="w", padx=(14, 0))
        if c.owned:
            ttk.Label(self.signs_frame, text=f"You own {c.owned}.", style="Card.TLabel").pack(anchor="w", pady=(8, 0))
        self.wish_button.state(["!disabled"])
        self.ebay_button.state(["!disabled"])

    # --- actions -------------------------------------------------------------

    def _selected(self) -> list[insight.Candidate]:
        return [self.shown[int(i)] for i in self.tree.selection()]

    def add_to_wishlist(self) -> None:
        wishlist = {name_key(c.name) for c in self.db.search(wishlist=True)}
        added = 0
        for c in self._selected():
            if name_key(c.name) in wishlist:
                continue
            self.db.add(Card(name=c.name, game="Riftbound", wishlist=True,
                             notes=f"Added from Future insight (watch score {c.score:.0f}: {c.tags})"))
            added += 1
        self.on_data_changed()
        messagebox.showinfo("Add to wishlist", f"Added {added} card{'s' if added != 1 else ''} to your wishlist."
                            if added else "Those cards are already on your wishlist.")

    def open_ebay(self) -> None:
        for c in self._selected()[:1]:
            webbrowser.open(market.ebay_sold_url(c.name, "Riftbound", self.db.get_setting("ebay_site", "ebay.com")))

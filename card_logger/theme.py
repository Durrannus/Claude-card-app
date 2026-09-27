"""Dark navy-and-gold look for the whole app, built on ttk's "clam" theme."""

import sys
import tkinter as tk
from tkinter import font as tkfont
from tkinter import ttk

# Palette
BG = "#0b1520"          # window background
SURFACE = "#132230"     # panels, tables
SURFACE_ALT = "#172a3a"  # striped rows
RAISED = "#1c3144"      # buttons, inputs
BORDER = "#2a4257"
TEXT = "#e8ecf1"
MUTED = "#8fa3b8"
GOLD = "#c8aa6e"
GOLD_HOVER = "#dcc08a"
GOLD_TEXT = "#1a1407"
TEAL = "#0ac8b9"
SELECT = "#1f4f6b"
GOOD = "#5fd38d"
BAD = "#ff7a7a"
DANGER = "#c9524f"

if sys.platform.startswith("win"):
    FAMILY = "Segoe UI"
elif sys.platform == "darwin":
    FAMILY = "Helvetica Neue"
else:
    FAMILY = None  # keep the system's default font


def font(size: int = 10, weight: str = "normal") -> tuple:
    family = FAMILY or tkfont.nametofont("TkDefaultFont").actual("family")
    return (family, size, weight)


SCALE = 1.0  # display scaling, set by apply_theme (1.25 on Windows at 125%)


def px(size: float) -> int:
    """A size in pixels for a standard screen, scaled for this one."""
    return int(size * SCALE)


def apply_theme(root: tk.Tk) -> None:
    global SCALE
    SCALE = display_scale(root)
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
        f = tkfont.nametofont(name)
        if FAMILY:
            f.configure(family=FAMILY)
        f.configure(size=10)

    root.configure(background=BG)
    # Colours for plain Tk widgets and the combobox drop-down list.
    root.option_add("*Toplevel.background", BG)
    root.option_add("*TCombobox*Listbox.background", RAISED)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", SELECT)
    root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
    root.option_add("*TCombobox*Listbox.font", font(10))

    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, fieldbackground=RAISED, bordercolor=BORDER,
                    lightcolor=BORDER, darkcolor=BORDER, troughcolor=SURFACE, focuscolor=GOLD,
                    selectbackground=SELECT, selectforeground=TEXT, insertcolor=TEXT, font=font(10))
    style.map(".", foreground=[("disabled", "#5b6f82")])

    # Frames and labels
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=SURFACE, bordercolor=BORDER, relief="solid", borderwidth=1)
    style.configure("Header.TFrame", background=SURFACE)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Card.TLabel", background=SURFACE)
    style.configure("CardMuted.TLabel", background=SURFACE, foreground=MUTED)
    style.configure("Title.TLabel", background=SURFACE, foreground=GOLD, font=font(17, "bold"))
    style.configure("Subtitle.TLabel", background=SURFACE, foreground=MUTED, font=font(10))
    style.configure("TileValue.TLabel", background=SURFACE, foreground=TEXT, font=font(16, "bold"))
    style.configure("TileGold.TLabel", background=SURFACE, foreground=GOLD, font=font(16, "bold"))
    style.configure("TileCaption.TLabel", background=SURFACE, foreground=MUTED, font=font(9))
    style.configure("Section.TLabel", foreground=GOLD, font=font(11, "bold"))
    style.configure("CardSection.TLabel", background=SURFACE, foreground=GOLD, font=font(11, "bold"))
    style.configure("CardName.TLabel", background=SURFACE, foreground=TEXT, font=font(12, "bold"))
    style.configure("Status.TLabel", background=SURFACE, foreground=MUTED, padding=(10, 5))
    style.configure("Good.TLabel", foreground=GOOD)
    style.configure("Legend.TLabel", background=SURFACE, foreground=MUTED, font=font(9))

    # Buttons
    style.configure("TButton", background=RAISED, foreground=TEXT, borderwidth=1, bordercolor=BORDER,
                    lightcolor=RAISED, darkcolor=RAISED, padding=(12, 6), focusthickness=0)
    style.map("TButton",
              background=[("disabled", SURFACE), ("pressed", SELECT), ("active", "#26405a")],
              bordercolor=[("active", GOLD), ("focus", BORDER)],
              lightcolor=[("active", "#26405a")], darkcolor=[("active", "#26405a")])
    style.configure("Accent.TButton", background=GOLD, foreground=GOLD_TEXT, bordercolor=GOLD,
                    lightcolor=GOLD, darkcolor=GOLD, font=font(10, "bold"))
    style.map("Accent.TButton",
              background=[("disabled", "#6b5d41"), ("pressed", "#b0935a"), ("active", GOLD_HOVER)],
              foreground=[("disabled", "#2d261a")],
              bordercolor=[("active", GOLD_HOVER)],
              lightcolor=[("active", GOLD_HOVER)], darkcolor=[("active", GOLD_HOVER)])
    style.configure("Small.TButton", padding=(8, 5), width=0)  # width 0 = fit the text
    style.configure("Danger.TButton", foreground="#ffb4b0")
    style.map("Danger.TButton", background=[("pressed", "#5a2220"), ("active", "#4a2322")],
              bordercolor=[("active", DANGER)])

    # Segmented view switch (radio buttons drawn as toggle buttons)
    style.configure("Segment.TRadiobutton", background=RAISED, foreground=MUTED, padding=(16, 6),
                    borderwidth=1, bordercolor=BORDER, indicatorsize=0, font=font(10, "bold"))
    style.layout("Segment.TRadiobutton", [
        ("Radiobutton.border", {"sticky": "nswe", "children": [
            ("Radiobutton.padding", {"sticky": "nswe", "children": [
                ("Radiobutton.label", {"sticky": "nswe"})]})]})])
    style.map("Segment.TRadiobutton",
              background=[("selected", GOLD), ("active", "#26405a")],
              foreground=[("selected", GOLD_TEXT), ("active", TEXT)])

    # Inputs
    field = dict(fieldbackground=RAISED, foreground=TEXT, bordercolor=BORDER, lightcolor=RAISED,
                 darkcolor=RAISED, insertcolor=TEXT, padding=(6, 4), arrowcolor=MUTED)
    for widget in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(widget, **field)
        style.map(widget, bordercolor=[("focus", GOLD)], lightcolor=[("focus", RAISED)],
                  fieldbackground=[("readonly", RAISED), ("disabled", SURFACE)],
                  foreground=[("readonly", TEXT)], selectbackground=[("readonly", RAISED)],
                  selectforeground=[("readonly", TEXT)], arrowcolor=[("active", GOLD)],
                  background=[("active", RAISED), ("readonly", RAISED)])
    for widget in ("TCheckbutton", "TRadiobutton"):
        style.configure(widget, background=BG, foreground=TEXT, indicatorbackground=RAISED,
                        indicatorforeground=GOLD_TEXT, upperbordercolor=BORDER, lowerbordercolor=BORDER)
        style.map(widget, background=[("active", BG)],
                  indicatorbackground=[("selected", GOLD), ("active", "#26405a")])
    style.configure("Card.TCheckbutton", background=SURFACE)
    style.map("Card.TCheckbutton", background=[("active", SURFACE)])

    # Tables
    style.configure("Treeview", background=SURFACE, fieldbackground=SURFACE, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=SURFACE, darkcolor=SURFACE, rowheight=30, font=font(10))
    style.map("Treeview", background=[("selected", SELECT)], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=RAISED, foreground=GOLD, bordercolor=BORDER,
                    lightcolor=RAISED, darkcolor=RAISED, relief="flat", padding=(8, 7), font=font(10, "bold"))
    style.map("Treeview.Heading", background=[("active", "#26405a")])
    style.configure("Vertical.TScrollbar", background=RAISED, troughcolor=SURFACE, bordercolor=SURFACE,
                    lightcolor=RAISED, darkcolor=RAISED, arrowcolor=MUTED, gripcount=0)
    style.map("Vertical.TScrollbar", background=[("active", "#2d4a66")])
    style.configure("Horizontal.TScrollbar", background=RAISED, troughcolor=SURFACE, bordercolor=SURFACE,
                    lightcolor=RAISED, darkcolor=RAISED, arrowcolor=MUTED, gripcount=0)
    style.map("Horizontal.TScrollbar", background=[("active", "#2d4a66")])

    # Tabs
    style.configure("TNotebook", background=BG, bordercolor=BORDER, tabmargins=(0, 0, 0, 0),
                    lightcolor=BG, darkcolor=BG)
    style.configure("TNotebook.Tab", background=BG, foreground=MUTED, bordercolor=BG,
                    lightcolor=BG, darkcolor=BG, padding=(18, 8), font=font(11, "bold"))
    style.map("TNotebook.Tab",
              background=[("selected", SURFACE), ("active", "#12202d")],
              foreground=[("selected", GOLD), ("active", TEXT)],
              bordercolor=[("selected", BORDER)],
              lightcolor=[("selected", GOLD)])
    style.configure("Inner.TNotebook", background=SURFACE, lightcolor=SURFACE, darkcolor=SURFACE)
    style.configure("Inner.TNotebook.Tab", background=SURFACE, font=font(10, "bold"), padding=(14, 6))
    style.map("Inner.TNotebook.Tab", background=[("selected", RAISED), ("active", "#1a2d3e")])

    style.configure("TLabelframe", background=SURFACE, bordercolor=BORDER, lightcolor=SURFACE,
                    darkcolor=SURFACE, padding=8)
    style.configure("TLabelframe.Label", background=SURFACE, foreground=GOLD, font=font(11, "bold"))
    style.configure("TSeparator", background=BORDER)
    style.configure("TPanedwindow", background=BG)
    style.configure("Sash", sashthickness=8, background=BG, bordercolor=BG, lightcolor=BG, darkcolor=BG)


def style_text(widget: tk.Text) -> None:
    """Give a plain Tk text box the theme's colours."""
    widget.configure(background=RAISED, foreground=TEXT, insertbackground=TEXT, selectbackground=SELECT,
                     selectforeground=TEXT, relief="flat", highlightthickness=1, highlightbackground=BORDER,
                     highlightcolor=GOLD, padx=8, pady=6, font=font(10))


def make_table(container: tk.Misc, columns, on_sort=None, flexible=("name",), **tree_options) -> ttk.Treeview:
    """Build a striped table filling `container`, with scroll bars both ways.

    `columns` are (key, heading, width, anchor, ...) tuples; widths are for a
    standard 96 DPI screen and are scaled to this one. Every column is at
    least wide enough for its heading (plus a sort arrow) in the font actually
    used here, so headings never get cut off; when the window is narrow,
    columns shrink down to that, and a horizontal scroll bar appears only if
    they still don't fit. `flexible` columns (like card names) can shrink
    further and are cut off first.
    """
    tree = ttk.Treeview(container, columns=[c[0] for c in columns], show="headings", **tree_options)
    heading_font = tkfont.Font(font=font(10, "bold"))
    scale = display_scale(tree)
    preferred, minimum = {}, {}
    for key, heading, width, anchor, *_ in columns:
        needed = heading_font.measure(heading + " ▼") + 24
        preferred[key] = max(int(width * scale), needed)
        minimum[key] = min(needed, int(90 * scale)) if key in flexible else needed
        tree.heading(key, text=heading, command=(lambda k=key: on_sort(k)) if on_sort else "")
        tree.column(key, width=preferred[key], minwidth=minimum[key], anchor=anchor, stretch=False)
    stripe(tree)

    def fit(_event=None):
        """Share the table's width between its columns: extra room goes to
        flexible columns; when short of room, flexible columns shrink first,
        then the rest, never below their heading."""
        available = tree.winfo_width() - 4
        if available < 50:
            return
        widths = dict(preferred)
        flex = [k for k in widths if k in flexible] or list(widths)
        spare = available - sum(widths.values())
        if spare >= 0:
            for k in flex:
                widths[k] += spare // len(flex)
        else:
            for group in (flex, [k for k in widths if k not in flex]):
                over = sum(widths.values()) - available
                if over <= 0:
                    break
                slack = {k: widths[k] - minimum[k] for k in group}
                total = sum(slack.values())
                if total <= 0:
                    continue
                for k in group:
                    widths[k] -= min(slack[k], round(over * slack[k] / total))
        for k, w in widths.items():
            if tree.column(k, "width") != w:
                tree.column(k, width=max(w, minimum[k]))
    tree.bind("<Configure>", fit, add="+")
    vertical = ttk.Scrollbar(container, orient="vertical", command=tree.yview)
    horizontal = ttk.Scrollbar(container, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    container.rowconfigure(0, weight=1)
    container.columnconfigure(0, weight=1)

    # Show the horizontal bar only when the columns don't fit.
    def update_horizontal(first, last):
        horizontal.set(first, last)
        if float(first) <= 0.0 and float(last) >= 1.0:
            horizontal.grid_remove()
        else:
            horizontal.grid(row=1, column=0, sticky="ew")
    tree.configure(xscrollcommand=update_horizontal)
    return tree


def display_scale(widget: tk.Misc) -> float:
    """1.0 on a standard screen, 1.25 at 125% display scaling, and so on."""
    return max(1.0, float(widget.tk.call("tk", "scaling")) / (96 / 72))


def stripe(tree: ttk.Treeview) -> None:
    """Set up alternating row colours; insert rows with tags=(row_tag(i),)."""
    tree.tag_configure("odd", background=SURFACE_ALT)
    tree.tag_configure("even", background=SURFACE)


def row_tag(index: int) -> str:
    return "odd" if index % 2 else "even"


def header(parent: tk.Misc, subtitle: str) -> ttk.Frame:
    """The title bar across the top of the window."""
    bar = ttk.Frame(parent, style="Header.TFrame", padding=(18, 12))
    accent = tk.Frame(bar, background=GOLD, width=4, height=38)
    accent.pack(side="left", padx=(0, 12))
    text = ttk.Frame(bar, style="Header.TFrame")
    text.pack(side="left")
    ttk.Label(text, text="Card Collection Logger", style="Title.TLabel").pack(anchor="w")
    ttk.Label(text, text=subtitle, style="Subtitle.TLabel").pack(anchor="w")
    return bar


class StatTile(ttk.Frame):
    """A small panel showing one big number with a caption."""

    def __init__(self, parent, caption: str, gold: bool = False):
        super().__init__(parent, style="Card.TFrame", padding=(16, 10))
        self.value = tk.StringVar(value="—")
        ttk.Label(self, textvariable=self.value, style="TileGold.TLabel" if gold else "TileValue.TLabel").pack(anchor="w")
        ttk.Label(self, text=caption.upper(), style="TileCaption.TLabel").pack(anchor="w")

# hud_tk.py
"""
Modern Tkinter HUD for SuperChess / Classic Chess (hybrid UI) with:
 - captured-piece thumbnails (PIL optional)
 - modern side-panel styling (ttk)
 - move history with SAN formatting
 - non-destructive tracing via snapshot preview window
 - heuristics to detect superpower activations (best-effort)
 - keyboard shortcuts for trace/back/forward
Usage:
    from hud_tk import launch_hud
    launch_hud(game_controller)  # call after controller.chess exists (e.g. in Game.start_variant)
"""

import threading
import tkinter as tk
from tkinter import ttk, messagebox
import time
import os
import traceback
import copy
import math

# optional pillow for images
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

# Theme colors
_BG = "#111217"
_PANEL = "#0f1720"
_ACCENT = "#f5c542"
_TEXT = "#E6EEF3"
_SUBTEXT = "#9AA7B2"
_BADGE_BG = "#2b2f3a"
_GOOD = "#2ecc71"
_WARN = "#f39c12"
_DANGER = "#e74c3c"

REFRESH_MS = 250  # HUD refresh interval in ms

# SAN piece letters mapping
_PIECE_LETTER = {
    "king": "K",
    "queen": "Q",
    "rook": "R",
    "bishop": "B",
    "knight": "N",
    "pawn": ""
}


# ---------- PieceAtlas to slice pieces.png (optional) ----------
class PieceAtlas:
    DEFAULT_ORDER = ["king", "queen", "rook", "bishop", "knight", "pawn"]

    def __init__(self, pieces_path, thumb_size=32, order=None):
        self.pieces_path = pieces_path
        self.thumb_size = int(thumb_size)
        self.order = order or self.DEFAULT_ORDER
        self.images = {}
        self.available = False
        if not PIL_AVAILABLE or not pieces_path or not os.path.exists(pieces_path):
            self.available = False
            return
        try:
            img = Image.open(pieces_path).convert("RGBA")
            w, h = img.size
            cols = len(self.order)
            rows = 2
            tile_w = w // cols
            tile_h = h // rows
            ts = self.thumb_size
            for row_idx, color in enumerate(("white", "black")):
                for col_idx, name in enumerate(self.order):
                    left = col_idx * tile_w
                    upper = row_idx * tile_h
                    crop = img.crop((left, upper, left + tile_w, upper + tile_h))
                    crop = crop.resize((ts, ts), Image.LANCZOS)
                    tkimg = ImageTk.PhotoImage(crop)
                    key = f"{color}_{name}"
                    self.images[key] = tkimg
            self.available = True
        except Exception:
            traceback.print_exc()
            self.available = False

    def get(self, piece_name):
        return self.images.get(piece_name)


# ---------- Preview board window (non-destructive) ----------
class PreviewWindow:
    """
    A simple Tkinter Toplevel that renders board snapshots stored by the HUD.
    Accepts a list of snapshots (each snapshot = piece_location dict) and captured lists.
    Provides navigation controls and keyboard shortcuts.
    """

    def __init__(self, parent, snapshots, captured_snapshots, atlas=None, square_size=64):
        """
        snapshots: list of piece_location snapshots (deepcopied dicts). Index i corresponds to position after half-move i.
        captured_snapshots: list of captured lists corresponding to snapshots.
        atlas: PieceAtlas or None
        square_size: pixels for rendering squares
        """
        self.root = tk.Toplevel(parent)
        self.root.title("Trace Preview")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.geometry(f"{square_size*8+200}x{square_size*8+20}")
        self.snapshots = snapshots
        self.captured_snapshots = captured_snapshots
        self.atlas = atlas
        self.square = square_size
        self.index = 0
        self._image_refs = []
        self._running = True

        # UI layout: canvas left, panel right
        self.canvas = tk.Canvas(self.root, width=self.square*8, height=self.square*8, bg="#ddd")
        self.canvas.pack(side="left", padx=8, pady=8)

        right = tk.Frame(self.root, width=200, bg=_PANEL)
        right.pack(side="right", fill="y")

        self.lbl = tk.Label(right, text="Move 1 / {}".format(len(self.snapshots)), bg=_PANEL, fg=_TEXT, font=("Segoe UI", 10, "bold"))
        self.lbl.pack(pady=8)

        # navigation buttons
        nav = tk.Frame(right, bg=_PANEL)
        nav.pack(pady=6, fill="x")
        tk.Button(nav, text="⏮ Prev", command=self.prev).pack(side="left", expand=True, fill="x", padx=4)
        tk.Button(nav, text="Next ⏭", command=self.next).pack(side="left", expand=True, fill="x", padx=4)

        tk.Button(right, text="Return to Live (Close)", command=self._on_close).pack(pady=8, fill="x", padx=8)

        # instructions
        inst = tk.Label(right, text="Keys: ← prev   → next   Home -> live   Esc -> close", bg=_PANEL, fg=_SUBTEXT, wraplength=180, justify="left")
        inst.pack(pady=8, padx=6)

        # captured lists
        self.cap_label = tk.Label(right, text="Captured:", bg=_PANEL, fg=_TEXT)
        self.cap_label.pack(pady=(6,0))
        self.cap_box = tk.Listbox(right, bg=_PANEL, fg=_TEXT, bd=0, highlightthickness=0)
        self.cap_box.pack(fill="both", expand=True, padx=6, pady=(2,8))

        # bind keys
        self.root.bind("<Left>", lambda e: self.prev())
        self.root.bind("<Right>", lambda e: self.next())
        self.root.bind("<Home>", lambda e: self.go_live())
        self.root.bind("<Escape>", lambda e: self._on_close())

        # initial render
        self.render_index(self.index)

    def render_index(self, idx):
        if idx < 0: idx = 0
        if idx >= len(self.snapshots): idx = len(self.snapshots)-1
        self.index = idx
        snap = self.snapshots[self.index]
        # draw board squares
        self.canvas.delete("all")
        for x in range(8):
            for y in range(8):
                px = x*self.square
                py = y*self.square
                color = "#f0d9b5" if (x+y)%2==0 else "#b58863"
                self.canvas.create_rectangle(px, py, px+self.square, py+self.square, fill=color, outline="")

        # place piece images or text from snapshot
        # snapshot layout assumed same as chess.piece_location: dict[file][row] -> [piece_name, selected, (x,y)]
        for file in "abcdefgh":
            if file not in snap:
                continue
            for row in range(1,9):
                cell = snap[file].get(row)
                if not cell:
                    continue
                pname = cell[0]
                if not pname:
                    continue
                # compute coords
                px, py = cell[2]  # x,y in 0..7
                dx = px * self.square + self.square//2
                dy = py * self.square + self.square//2
                # image if atlas available
                if self.atlas:
                    img = self.atlas.get(pname)
                    if img:
                        # keep reference
                        self._image_refs.append(img)
                        self.canvas.create_image(dx, dy, image=img)
                        continue
                # fallback: text label
                self.canvas.create_text(dx, dy, text=pname.split("_",1)[1][0].upper(), font=("Segoe UI", 14, "bold"))

        # update captured box
        self.cap_box.delete(0, tk.END)
        caps = self.captured_snapshots[self.index] if self.captured_snapshots and len(self.captured_snapshots)>self.index else []
        for c in caps:
            self.cap_box.insert(tk.END, c)

        # update label
        self.lbl.config(text=f"Move {self.index+1} / {len(self.snapshots)}")

    def prev(self):
        if self.index > 0:
            self.render_index(self.index-1)

    def next(self):
        if self.index < len(self.snapshots)-1:
            self.render_index(self.index+1)

    def go_live(self):
        # closing returns to live (the user requested non-destructive tracing)
        self._on_close()

    def _on_close(self):
        try:
            self._running = False
            self.root.destroy()
        except Exception:
            pass


# ---------- Main HUD class ----------
class ModernHUD:
    def __init__(self, root, controller):
        """
        controller: your Game instance (from game.py). HUD reads controller.chess for engine state.
        """
        self.root = root
        self.controller = controller
        self.engine = getattr(controller, "chess", None) or controller

        # piece atlas detection (res/pieces.png fallback)
        pieces_path = None
        try:
            base = getattr(controller, "resources", None)
            if base:
                cand = os.path.join(base, "pieces.png")
                if os.path.exists(cand):
                    pieces_path = cand
        except Exception:
            pass
        if not pieces_path:
            for cand in ("./res/pieces.png", "./pieces.png"):
                if os.path.exists(cand):
                    pieces_path = cand
                    break

        thumb = 32
        try:
            sq = getattr(controller, "square_length", None)
            if sq:
                thumb = max(20, min(48, sq // 2))
        except Exception:
            pass

        self.atlas = PieceAtlas(pieces_path, thumb_size=thumb)

        # image references
        self._image_refs = []

        # history store: a list of entries; each entry includes:
        #  'idx', 'san', 'src'(file,row)|None, 'dst_x','dst_y', 'piece', 'power' (heuristic), 'snapshot' (piece_location deepcopy), 'captured_snapshot' (list)
        self._history = []
        self._last_seen_move = None
        # also store snapshots separately for quick previewing
        self._snapshots = []
        self._capt_snapshots = []

        # Build UI
        root.title("SuperChess — HUD")
        root.configure(bg=_BG)
        root.geometry("380x760")
        try:
            root.resizable(False, True)
        except Exception:
            pass

        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure("TFrame", background=_BG)
        style.configure("Card.TFrame", background=_PANEL, relief="flat")
        style.configure("Title.TLabel", background=_BG, foreground=_ACCENT, font=("Segoe UI", 14, "bold"))
        style.configure("Large.TLabel", background=_PANEL, foreground=_TEXT, font=("Segoe UI", 12, "bold"))
        style.configure("Small.TLabel", background=_PANEL, foreground=_SUBTEXT, font=("Segoe UI", 10))
        style.configure("Accent.TButton", background=_ACCENT, foreground=_BG, font=("Segoe UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#f7d26a"), ("!disabled", _ACCENT)],
                  foreground=[("!disabled", _BG)])

        # Top title + variant
        title = ttk.Label(root, text="SuperChess", style="Title.TLabel")
        title.pack(padx=12, pady=(12, 6), anchor="w")
        self.variant_label = ttk.Label(root, text="Loading...", style="Small.TLabel")
        self.variant_label.pack(padx=12, pady=(0, 8), anchor="w")

        self.content = ttk.Frame(root, style="TFrame")
        self.content.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        # Player cards
        self.white_card = self._make_player_card(self.content, "White")
        self.white_card.pack(fill="x", pady=(0,8))
        self.black_card = self._make_player_card(self.content, "Black")
        self.black_card.pack(fill="x", pady=(0,8))

        # Power card
        self.power_card = ttk.Frame(self.content, style="Card.TFrame", padding=(12, 10))
        self._populate_power_card(self.power_card)
        self.power_card.pack(fill="x", pady=(6, 12))

        # History card
        self.history_card = ttk.Frame(self.content, style="Card.TFrame", padding=(12, 10))
        self._populate_history_card(self.history_card)
        self.history_card.pack(fill="both", expand=True, pady=(6, 12))

        # Captured card
        self.captured_card = ttk.Frame(self.content, style="Card.TFrame", padding=(12, 10))
        self._populate_captured_card(self.captured_card)
        self.captured_card.pack(fill="x", pady=(6, 12))

        # Controls
        self.controls_card = ttk.Frame(self.content, style="Card.TFrame", padding=(12, 10))
        self._populate_controls(self.controls_card)
        self.controls_card.pack(fill="x", pady=(6, 12))

        # toast
        self.toast_var = tk.StringVar(value="")
        self.toast_label = tk.Label(root, textvariable=self.toast_var, bg=_PANEL, fg=_TEXT,
                                    font=("Segoe UI", 10), wraplength=340, justify="center")
        self.toast_label.place(relx=0.5, rely=0.94, anchor="center")

        # shortcuts in main HUD
        root.bind_all("<Control-Left>", lambda e: self._keyboard_trace_prev())
        root.bind_all("<Control-Right>", lambda e: self._keyboard_trace_next())
        root.bind_all("<Control-Home>", lambda e: self._keyboard_trace_live())

        # start update loop
        self._running = True
        self.update_loop()

    # ---------- UI parts ----------
    def _make_player_card(self, parent, color_name):
        card = ttk.Frame(parent, style="Card.TFrame", padding=(12, 10))
        top = tk.Frame(card, bg=_PANEL)
        top.pack(fill="x")
        avatar = tk.Canvas(top, width=46, height=46, highlightthickness=0, bg=_PANEL)
        avatar.pack(side="left")
        if color_name.lower() == "white":
            fill = "#dbe9f9"; textc = "#1b2636"
        else:
            fill = "#2b2f3a"; textc = "#e6eef3"
        avatar.create_oval(2, 2, 44, 44, fill=fill, outline="#00000020")
        avatar.create_text(23, 23, text=color_name[0], font=("Segoe UI", 14, "bold"), fill=textc)
        infof = tk.Frame(top, bg=_PANEL)
        infof.pack(side="left", padx=(8,0), fill="x", expand=True)
        name_lbl = ttk.Label(infof, text=color_name, style="Large.TLabel")
        name_lbl.pack(anchor="w")
        timer_lbl = ttk.Label(infof, text="--:--", style="Small.TLabel")
        timer_lbl.pack(anchor="w", pady=(2,0))
        rightf = tk.Frame(top, bg=_PANEL)
        rightf.pack(side="right", anchor="n")
        turn_dot = tk.Canvas(rightf, width=14, height=14, highlightthickness=0, bg=_PANEL)
        turn_dot.pack(anchor="e", pady=(4,0))
        turn_dot.create_oval(2,2,12,12, fill="#00000000", outline="#00000000")
        charge_badge = tk.Label(rightf, text="", bg=_BADGE_BG, fg=_ACCENT, font=("Segoe UI",9,"bold"), padx=6, pady=3)
        charge_badge.pack(anchor="e", pady=(6,0))
        # store refs
        card._name_lbl = name_lbl
        card._timer_lbl = timer_lbl
        card._turn_dot = turn_dot
        card._charge_badge = charge_badge
        card._color_name = color_name.lower()
        return card

    def _populate_power_card(self, parent):
        header = ttk.Label(parent, text="Super Powers", style="Large.TLabel")
        header.pack(anchor="w", pady=(0,8))
        row = tk.Frame(parent, bg=_PANEL)
        row.pack(fill="x", pady=(0,8))
        self.w_charge_label = ttk.Label(row, text="White: 0", style="Small.TLabel")
        self.w_charge_label.pack(side="left", padx=(0,8))
        self.b_charge_label = ttk.Label(row, text="Black: 0", style="Small.TLabel")
        self.b_charge_label.pack(side="left", padx=(0,8))
        btns = tk.Frame(parent, bg=_PANEL)
        btns.pack(fill="x")
        self.preview_btn = ttk.Button(btns, text="Preview Power (S)", style="Accent.TButton", command=self._on_preview)
        self.preview_btn.pack(side="left", expand=True, fill="x", padx=(0,6))
        self.cancel_btn = ttk.Button(btns, text="Cancel Preview", style="TButton", command=self._on_cancel)
        self.cancel_btn.pack(side="left", expand=True, fill="x", padx=(6,0))
        hint = ttk.Label(parent, text="Tip: Click Preview then the board to activate.", style="Small.TLabel")
        hint.pack(anchor="w", pady=(8,0))
        self.preview_status = ttk.Label(parent, text="Preview: inactive", style="Small.TLabel")
        self.preview_status.pack(anchor="w", pady=(6,0))

    def _populate_history_card(self, parent):
        header = ttk.Label(parent, text="Move History", style="Large.TLabel")
        header.pack(anchor="w", pady=(0,8))
        frame = tk.Frame(parent, bg=_PANEL)
        frame.pack(fill="both", expand=True)
        self.history_list = tk.Listbox(frame, bg=_PANEL, fg=_TEXT, bd=0, highlightthickness=0, activestyle="none",
                                       selectbackground=_ACCENT, selectforeground=_BG)
        self.history_list.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.history_list.yview)
        sb.pack(side="right", fill="y")
        self.history_list.configure(yscrollcommand=sb.set)
        self.history_list.bind("<Double-Button-1>", self._on_history_double)
        ctrl_row = tk.Frame(parent, bg=_PANEL)
        ctrl_row.pack(fill="x", pady=(8,0))
        self.trace_btn = ttk.Button(ctrl_row, text="Trace to selected", command=self.trace_to_selected)
        self.trace_btn.pack(side="left", expand=True, fill="x", padx=(0,6))
        self.trace_live_btn = ttk.Button(ctrl_row, text="Trace to live", command=self.trace_to_live)
        self.trace_live_btn.pack(side="left", expand=True, fill="x", padx=(6,0))
        self.trace_info = ttk.Label(parent, text="Double-click entry to open preview at that half-move.", style="Small.TLabel")
        self.trace_info.pack(anchor="w", pady=(6,0))

    def _populate_captured_card(self, parent):
        header = ttk.Label(parent, text="Captured Pieces", style="Large.TLabel")
        header.pack(anchor="w", pady=(0,8))
        counts = tk.Frame(parent, bg=_PANEL)
        counts.pack(fill="x")
        self.captured_count_label = ttk.Label(counts, text="Total: 0", style="Small.TLabel")
        self.captured_count_label.pack(side="left")
        strips = tk.Frame(parent, bg=_PANEL)
        strips.pack(fill="x", pady=(8,0))
        w_label = ttk.Label(strips, text="White lost:", style="Small.TLabel")
        w_label.grid(row=0, column=0, sticky="w")
        self.white_strip = tk.Frame(strips, bg=_PANEL)
        self.white_strip.grid(row=1, column=0, sticky="w", pady=(4,6))
        b_label = ttk.Label(strips, text="Black lost:", style="Small.TLabel")
        b_label.grid(row=2, column=0, sticky="w")
        self.black_strip = tk.Frame(strips, bg=_PANEL)
        self.black_strip.grid(row=3, column=0, sticky="w", pady=(4,6))
        if not self.atlas.available:
            self._captured_fallback = tk.Listbox(parent, bg=_PANEL, fg=_TEXT, bd=0, highlightthickness=0)
            self._captured_fallback.pack(fill="both", expand=True, pady=(8,0))
        else:
            self._captured_fallback = None

    def _populate_controls(self, parent):
        header = ttk.Label(parent, text="Controls", style="Large.TLabel")
        header.pack(anchor="w", pady=(0,8))
        row = tk.Frame(parent, bg=_PANEL)
        row.pack(fill="x")
        self.new_btn = ttk.Button(row, text="New Game", command=self._on_new_game)
        self.new_btn.pack(side="left", expand=True, fill="x", padx=(0,6))
        self.pause_btn = ttk.Button(row, text="Pause", command=self._on_pause)
        self.pause_btn.pack(side="left", expand=True, fill="x", padx=(6,6))
        self.quit_btn = ttk.Button(row, text="Quit", command=self._on_quit)
        self.quit_btn.pack(side="left", expand=True, fill="x", padx=(6,0))
        sett = tk.Frame(parent, bg=_PANEL)
        sett.pack(fill="x", pady=(8,0))
        ttk.Label(sett, text="Theme:", style="Small.TLabel").pack(side="left")
        self.theme_var = tk.StringVar(value="Dark")
        ttk.OptionMenu(sett, self.theme_var, "Dark", "Dark", "Light", command=self._on_theme).pack(side="right")

    # ---------- Button callbacks ----------
    def _on_preview(self):
        try:
            if hasattr(self.engine, "start_power_preview_for_selected"):
                self.engine.start_power_preview_for_selected()
            elif hasattr(self.controller, "start_power_preview_for_selected"):
                self.controller.start_power_preview_for_selected()
        except Exception:
            self._set_toast("Preview error")
            traceback.print_exc()

    def _on_cancel(self):
        try:
            if hasattr(self.engine, "cancel_power_preview"):
                self.engine.cancel_power_preview()
            elif hasattr(self.controller, "cancel_power_preview"):
                self.controller.cancel_power_preview()
        except Exception:
            self._set_toast("Cancel error")
            traceback.print_exc()

    def _on_new_game(self):
        try:
            if hasattr(self.controller, "start_variant"):
                self.controller.start_variant()
            elif hasattr(self.controller, "reset"):
                self.controller.reset()
            # clear recorded history
            self._history.clear()
            self._snapshots.clear()
            self._capt_snapshots.clear()
            self.history_list.delete(0, tk.END)
            self._set_toast("New game started.")
        except Exception:
            self._set_toast("New game failed.")
            traceback.print_exc()

    def _on_pause(self):
        try:
            if hasattr(self.controller, "paused"):
                self.controller.paused = not getattr(self.controller, "paused", False)
                self._set_toast("Paused." if self.controller.paused else "Resumed.")
            else:
                self._set_toast("Pause not available.")
        except Exception:
            self._set_toast("Pause failed.")
            traceback.print_exc()

    def _on_quit(self):
        try:
            if hasattr(self.controller, "quit"):
                try:
                    self.controller.quit()
                except Exception:
                    pass
            self._running = False
            self.root.quit()
        except Exception:
            self.root.quit()

    def _on_theme(self, val):
        global _BG, _PANEL, _TEXT, _SUBTEXT
        if val == "Light":
            _BG, _PANEL, _TEXT, _SUBTEXT = "#f6f7f9", "#ffffff", "#121417", "#59636b"
        else:
            _BG, _PANEL, _TEXT, _SUBTEXT = "#111217", "#0f1720", "#E6EEF3", "#9AA7B2"
        self.root.configure(bg=_BG)
        self.variant_label.configure(background=_BG)
        self.toast_label.configure(bg=_PANEL, fg=_TEXT)

    # ---------- History interactions ----------
    def _on_history_double(self, _evt):
        sel = self.history_list.curselection()
        if not sel:
            return
        idx = sel[0]
        self.open_preview_at(idx)

    def trace_to_selected(self):
        sel = self.history_list.curselection()
        if not sel:
            self._set_toast("No move selected.")
            return
        idx = sel[0]
        # ask user: destructive or non-destructive?
        if messagebox.askyesno("Trace", "Open non-destructive preview? (Cancel to reset live game and replay there)") :
            self.open_preview_at(idx)
        else:
            self._destructive_trace_to(idx)

    def trace_to_live(self):
        if not self._history:
            self._set_toast("No moves recorded.")
            return
        if messagebox.askyesno("Trace to Live", "Reset the live game and replay all moves up to current?"):
            self._destructive_trace_to(len(self._history)-1)
        else:
            self.open_preview_at(len(self._history)-1)

    def _keyboard_trace_prev(self):
        # move selection up in history list
        try:
            cur = self.history_list.curselection()
            idx = cur[0] if cur else 0
            idx = max(0, idx-1)
            self.history_list.selection_clear(0, tk.END)
            self.history_list.selection_set(idx)
            self.history_list.see(idx)
        except Exception:
            pass

    def _keyboard_trace_next(self):
        try:
            cur = self.history_list.curselection()
            idx = cur[0] if cur else -1
            idx = min(len(self._history)-1, idx+1)
            self.history_list.selection_clear(0, tk.END)
            self.history_list.selection_set(idx)
            self.history_list.see(idx)
        except Exception:
            pass

    def _keyboard_trace_live(self):
        # select last
        if self._history:
            idx = len(self._history)-1
            self.history_list.selection_clear(0, tk.END)
            self.history_list.selection_set(idx)
            self.history_list.see(idx)

    def open_preview_at(self, idx):
        # open non-destructive preview window with snapshots
        if idx < 0 or idx >= len(self._snapshots):
            self._set_toast("Index out of range.")
            return
        try:
            PreviewWindow(self.root, self._snapshots, self._capt_snapshots, atlas=self.atlas, square_size=64)
        except Exception:
            traceback.print_exc()
            self._set_toast("Failed to open preview.")

    def _destructive_trace_to(self, idx):
        """
        Reset the live controller and replay moves up to idx (inclusive) destructively.
        This uses controller.start_variant() or controller.reset(), then replays by calling
        controller.chess.validate_move(...) with stored src/dst metadata when available.
        """
        try:
            if idx < 0 or idx >= len(self._history):
                self._set_toast("Index out of range.")
                return
            if not messagebox.askyesno("Confirm", f"This will reset the live game and replay to move #{idx+1}. Continue?"):
                return

            if hasattr(self.controller, "start_variant"):
                self.controller.start_variant()
            elif hasattr(self.controller, "reset"):
                self.controller.reset()
            else:
                self._set_toast("Controller cannot reset/restart variant.")
                return

            time.sleep(0.05)

            # replay moves from history[0..idx]
            for i in range(idx+1):
                entry = self._history[i]
                dst = (entry['dst_x'], entry['dst_y'])
                src = entry.get('src')
                ok = False
                try:
                    if src:
                        ok = self.controller.chess.validate_move(dst, simulate=False, source=(src[0], src[1]))
                    else:
                        ok = self.controller.chess.validate_move(dst, simulate=False)
                except Exception:
                    ok = False
                if not ok:
                    self._set_toast(f"Replay failed at move #{i+1}: {entry.get('san','?')}")
                    return
            self._set_toast(f"Live game replayed to move #{idx+1}.")
        except Exception:
            traceback.print_exc()
            self._set_toast("Destructive trace failed.")

    # ---------- Recording logic ----------
    def _maybe_record_last_move(self):
        """
        Detect engine.last_move changes and append to history. Also capture snapshot (deepcopy)
        of piece_location and captured list AFTER the move for non-destructive tracing.
        Uses heuristics to detect special power activations (best-effort).
        """
        engine = getattr(self.controller, "chess", None) or self.engine
        if not engine:
            return
        last = getattr(engine, "last_move", None)
        if not last:
            return
        # ignore if same as last seen
        if last == self._last_seen_move:
            return
        self._last_seen_move = last

        try:
            # expect ((sx,sy),(dx,dy), piece_name)
            if isinstance(last, (list, tuple)) and len(last) >= 3:
                src_xy, dst_xy, piece = last[0], last[1], last[2]
                sx, sy = int(src_xy[0]), int(src_xy[1])
                dx, dy = int(dst_xy[0]), int(dst_xy[1])
            else:
                # fallback: string/something
                src_xy = None; dst_xy = None; piece = str(last)
                sx = sy = dx = dy = None
        except Exception:
            # malformed; skip
            return

        # capture counts before/after by peeking at last history entry's captured snapshot if available;
        # otherwise read engine.captured as after.
        prev_captured = []
        if self._capt_snapshots:
            prev_captured = list(self._capt_snapshots[-1])
        curr_captured = []
        try:
            curr_captured = list(getattr(engine, "captured", []) or [])
        except Exception:
            curr_captured = []

        captures_happened = len(curr_captured) - len(prev_captured) > 0

        # snapshot of board (deepcopy piece_location)
        snap = None
        try:
            if hasattr(engine, "piece_location"):
                snap = copy.deepcopy(engine.piece_location)
        except Exception:
            snap = None

        # determine source file,row if engine provides xy_to_square
        src_file_row = None
        try:
            if sx is not None and hasattr(engine, "xy_to_square"):
                sf, sr = engine.xy_to_square(sx, sy)
                src_file_row = (sf, sr)
        except Exception:
            src_file_row = None

        # heuristics to guess power type (best-effort)
        power = None
        try:
            # piece kind
            kind = piece.split("_",1)[1] if "_" in piece else piece
            kind = kind.lower()
            # queen dark empress detection: knight-like jump
            if kind == "queen" and sx is not None and ( (abs(dx-sx), abs(dy-sy)) in [(2,1),(1,2)] ):
                power = "dark_empress"
            # knight short-area: shadow jump (queen/knight?) per your engine rules: knight power is within 3x3 except own square
            elif kind == "knight" and sx is not None and max(abs(dx-sx), abs(dy-sy)) <= 1:
                power = "shadow_jump"
            # rook fortress: if dx==sx and dy==sy and engine has fortress_zones increased
            elif kind == "rook":
                # detect fortress: if last action caused fortress zone added (compare prev snapshot of fortress)
                prev_zones = []
                curr_zones = []
                try:
                    prev_zones = self._history[-1].get('fortress_zones', []) if self._history else []
                except Exception:
                    prev_zones = []
                try:
                    curr_zones = copy.deepcopy(getattr(engine, "fortress_zones", []) or [])
                except Exception:
                    curr_zones = []
                if curr_zones and (len(curr_zones) > len(prev_zones)):
                    power = "fortress_field"
            # pawn sacrifice: pawn removed itself and captured nearby pieces (heuristic)
            elif kind == "pawn":
                # if pawn no longer exists at src and captures increased -> sacrifice
                pawn_present = False
                try:
                    if snap and src_file_row:
                        sf, sr = src_file_row
                        occupant = snap.get(sf, {}).get(sr, [None])[0]
                        pawn_present = occupant and occupant.startswith(piece.split("_",1)[0])
                except Exception:
                    pawn_present = False
                if not pawn_present and captures_happened:
                    power = "sacrifice"
            # bishop phase shift: bishop moved diagonally, maybe jumped over blockers or special king+shield redirect
            elif kind == "bishop":
                # If destination previously had opponent king (we'd have captured king normally), label phase_shift
                prev_board = None
                try:
                    if len(self._snapshots)>0:
                        prev_board = self._snapshots[-1]
                except Exception:
                    prev_board = None
                try:
                    if prev_board and dx is not None and hasattr(engine, "xy_to_square"):
                        df, dr = engine.xy_to_square(dx, dy)
                        prev_occ = prev_board.get(df, {}).get(dr, [None])[0]
                        if prev_occ and prev_occ.endswith("king") and not prev_occ.startswith(piece.split("_",1)[0]):
                            power = "phase_shift"
                except Exception:
                    pass
            # else, if captures occurred and piece moved unconventionally (jump), mark power unknown
            elif captures_happened:
                power = "capture"
        except Exception:
            power = None

        # build SAN-like notation
        san = self._build_san(piece, src_xy, dst_xy, captures_happened, engine)

        # store fortress zones snapshot if available
        fortress_zones = copy.deepcopy(getattr(engine, "fortress_zones", []) or [])

        entry = {
            'idx': len(self._history),
            'san': san,
            'src': src_file_row,
            'dst_x': dx,
            'dst_y': dy,
            'piece': piece,
            'power': power,
            'snapshot': snap,
            'captured_snapshot': list(curr_captured),
            'fortress_zones': fortress_zones
        }

        # append
        self._history.append(entry)
        self._snapshots.append(snap if snap is not None else {})
        self._capt_snapshots.append(entry['captured_snapshot'])
        # add to listbox
        display = f"{entry['idx']+1:3d}. {entry['san']}" + (f" [{power}]" if power else "")
        self.history_list.insert(tk.END, display)

    def _build_san(self, piece, src_xy, dst_xy, capture, engine):
        """Make a simple SAN-like string (not full algebraic disambiguation)."""
        try:
            if not dst_xy:
                return str(piece)
            dx, dy = int(dst_xy[0]), int(dst_xy[1])
            # get dest file/row if possible
            dest = None
            src_file = None
            src_rank = None
            if hasattr(engine, "xy_to_square"):
                df, dr = engine.xy_to_square(dx, dy)
                dest = f"{df}{dr}"
                if src_xy:
                    sx, sy = int(src_xy[0]), int(src_xy[1])
                    sf, sr = engine.xy_to_square(sx, sy)
                    src_file = sf
                    src_rank = sr
            else:
                dest = f"{dx},{dy}"
                if src_xy:
                    src_file = str(src_xy[0])
                    src_rank = str(src_xy[1])

            piece_kind = piece.split("_",1)[1] if "_" in piece else piece
            letter = _PIECE_LETTER.get(piece_kind.lower(), "")
            if piece_kind.lower() == "pawn":
                if capture and src_file:
                    san = f"{src_file}x{dest}"
                elif capture:
                    san = f"x{dest}"
                else:
                    san = f"{dest}"
            else:
                san = f"{letter}{'x' if capture else ''}{dest}"
            return san
        except Exception:
            return f"{piece}→{dst_xy}"

    # ---------- HUD update loop ----------
    def update_loop(self):
        if not self._running:
            return
        try:
            engine = getattr(self.controller, "chess", None) or self.engine
            variant = "Classic"
            if engine and hasattr(engine, "charges"):
                variant = "Super"
            self.variant_label.config(text=f"Mode: {variant}")

            # update player cards
            self._update_player_card(self.white_card, "white")
            self._update_player_card(self.black_card, "black")

            # charges
            w_ch = b_ch = 0
            if engine and hasattr(engine, "charges"):
                try:
                    w_ch = engine.charges.get("white", 0)
                    b_ch = engine.charges.get("black", 0)
                except Exception:
                    pass
            self.w_charge_label.config(text=f"White: {w_ch}")
            self.b_charge_label.config(text=f"Black: {b_ch}")

            # preview status toggles
            preview_active = bool(getattr(engine, "power_preview_active", False))
            self.preview_status.config(text=f"Preview: {'active' if preview_active else 'inactive'}")
            if preview_active:
                self.preview_btn.state(["disabled"]); self.cancel_btn.state(["!disabled"])
            else:
                self.preview_btn.state(["!disabled"]); self.cancel_btn.state(["disabled"])

            # maybe record last_move (and snapshot)
            try:
                self._maybe_record_last_move()
            except Exception:
                traceback.print_exc()

            # update captured displays
            captured = []
            if engine and hasattr(engine, "captured"):
                for p in getattr(engine, "captured", []):
                    if isinstance(p, (list, tuple)) and p:
                        captured.append(str(p[0]))
                    else:
                        captured.append(str(p))
            self.captured_count_label.config(text=f"Total: {len(captured)}")
            if self.atlas.available:
                white_lost = [p for p in captured if p.startswith("white")]
                black_lost = [p for p in captured if p.startswith("black")]
                self._populate_strip_with_images(self.white_strip, white_lost)
                self._populate_strip_with_images(self.black_strip, black_lost)
            else:
                if self._captured_fallback is not None:
                    self._captured_fallback.delete(0, tk.END)
                    for c in captured:
                        self._captured_fallback.insert(tk.END, c)

            # engine toast -> HUD toast
            if engine and hasattr(engine, "toast_message"):
                t = getattr(engine, "toast_message", None)
                if t:
                    self._set_toast(t, sticky=False)

        except Exception:
            traceback.print_exc()

        self.root.after(REFRESH_MS, self.update_loop)

    # ---------- helper UI updates ----------
    def _populate_strip_with_images(self, strip_frame, piece_list):
        for w in strip_frame.winfo_children():
            w.destroy()
        refs = []
        for piece in piece_list:
            img = self.atlas.get(piece) if self.atlas else None
            if img:
                lbl = tk.Label(strip_frame, image=img, bg=_PANEL)
                lbl.pack(side="left", padx=4, pady=2)
                refs.append(img)
            else:
                lbl = ttk.Label(strip_frame, text=piece, style="Small.TLabel")
                lbl.pack(side="left", padx=6, pady=6)
        self._image_refs.append(refs)
        if len(self._image_refs) > 60:
            self._image_refs = self._image_refs[-30:]

    def _update_player_card(self, card, player):
        name = getattr(self.controller, f"name_{player}", None) or player.capitalize()
        card._name_lbl.config(text=name)
        timer_text = "--:--"
        try:
            if hasattr(self.controller, "remaining"):
                rem = getattr(self.controller, "remaining", {}).get(player)
                if rem is not None:
                    timer_text = self._fmt_time(int(rem))
        except Exception:
            pass
        card._timer_lbl.config(text=timer_text)

        is_turn = False
        try:
            if hasattr(self.controller, "current_turn_color"):
                is_turn = (self.controller.current_turn_color == player)
            else:
                engine = getattr(self.controller, "chess", None) or self.engine
                if engine and hasattr(engine, "turn"):
                    is_turn = bool(engine.turn.get(player))
        except Exception:
            is_turn = False

        dot = card._turn_dot
        dot.delete("all")
        if is_turn:
            dot.create_oval(2,2,12,12, fill=_GOOD, outline=_GOOD)
        else:
            dot.create_oval(2,2,12,12, fill="#00000000", outline="#00000000")

        badge_text = ""
        try:
            engine = getattr(self.controller, "chess", None) or self.engine
            if engine and hasattr(engine, "charges"):
                val = engine.charges.get(player, 0)
                badge_text = f"{val} ⚡"
        except Exception:
            badge_text = ""
        card._charge_badge.config(text=badge_text)

    def _fmt_time(self, seconds):
        if seconds is None:
            return "∞"
        try:
            s = max(0, int(seconds))
            m, s = divmod(s, 60)
            return f"{m}:{s:02d}"
        except Exception:
            return "--:--"

    def _set_toast(self, text, sticky=False, duration=2200):
        try:
            self.toast_var.set(text)
            if not sticky:
                def _clear():
                    try:
                        if self.toast_var.get() == text:
                            self.toast_var.set("")
                    except Exception:
                        pass
                self.root.after(duration, _clear)
        except Exception:
            pass


# ---------- convenience launcher ----------
def launch_hud(controller):
    """
    Launch the HUD in a separate daemon thread.
    controller: your Game instance (from game.py) or the engine. Must be created before calling.
    """
    def _run():
        try:
            root = tk.Tk()
            app = ModernHUD(root, controller)
            root.mainloop()
        except Exception:
            traceback.print_exc()
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


# ---------- debug/demo main ----------
if __name__ == "__main__":
    # Demo controller / engine stub
    class DummyEngine:
        def __init__(self):
            # piece_location scaffold like your engine: piece_location[file][row] = [piece_name, selected_bool, (x,y)]
            self.piece_location = {f: {r: ["", False, (ord(f)-97, 8-r)] for r in range(1,9)} for f in "abcdefgh"}
            # place a few pieces
            self.piece_location['e'][2][0] = "white_pawn"; self.piece_location['e'][2][2] = (4,6)
            self.piece_location['e'][7][0] = "black_king"; self.piece_location['e'][7][2] = (4,1)
            self.captured = []
            self.charges = {"white": 1, "black": 2}
            self.power_preview_active = False
            self.last_move = None
            self.fortress_zones = []

        def xy_to_square(self, x, y):
            # x:0..7 -> file letter, y:0..7 -> rank number (1..8 inverted)
            file = "abcdefgh"[x]
            row = 8 - y
            return file, row

        def validate_move(self, dest, simulate=False, source=None):
            # naive move for demo: move selected pawn/whatever from source to dest
            if source:
                sf, sr = source
                sx, sy = self.piece_location[sf][sr][2]
                df, dr = self.xy_to_square(dest[0], dest[1])
                moved = self.piece_location[sf][sr][0]
                if moved:
                    # capture if something in dest
                    tgt = self.piece_location[df][dr][0]
                    if tgt:
                        self.captured.append(tgt)
                    # move
                    self.piece_location[df][dr][0] = moved
                    self.piece_location[sf][sr][0] = ""
                    self.last_move = ((sx,sy),(dest[0],dest[1]), moved)
                    return True
            return False

    class DummyController:
        def __init__(self):
            self.name_white = "Alice"
            self.name_black = "Bob"
            self.remaining = {"white": 300, "black": 300}
            self.current_turn_color = "white"
            self.chess = DummyEngine()
            self.paused = False
        def start_variant(self):
            print("start_variant called")
        def reset(self):
            print("reset called")
        def quit(self):
            print("quit called")

    ctrl = DummyController()
    launch_hud(ctrl)

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

# game.py
import os
import sys
import pygame
from pygame.locals import KEYDOWN, K_ESCAPE, K_SPACE, K_s
from piece import Piece
from chess import Chess

pygame.init()
pygame.mixer.init()
try:
    move_sound = pygame.mixer.Sound("sound/move.wav")
    capture_sound = pygame.mixer.Sound("sound/capture.wav")
    lightning_sound=pygame.mixer.Sound("sound/lightning.mp3")
except Exception as e:
    print("Error loading sound:", e)

# lightning sound for superpower (try mp3 first, fallback to wav)
try:
    lightning_sound = pygame.mixer.Sound("sound/lightning.mp3")
except Exception:
    try:
        lightning_sound = pygame.mixer.Sound("sound/lightning.wav")
    except Exception as e:
        lightning_sound = None
        print("Error loading lightning sound:", e)

# Optional: set volume
move_sound.set_volume(1.0)
capture_sound.set_volume(1.0)
if lightning_sound:
    lightning_sound.set_volume(0.8)


try:
    from superchess import SuperChess
    HAS_SUPER = True
except Exception:
    HAS_SUPER = False

from utils import Utils


# --- Visual HUD: top bar, move history, replay controls, overlays (visual-only) ---
import pygame, time, math, traceback, os, copy

HUD_WIDTH = 360
TOP_BAR = 96
BOARD_MARGIN = 40

BG_COLOR = (24, 24, 24)
BOARD_LIGHT = (246, 246, 238)
BOARD_DARK = (120, 120, 90)
RIGHT_PANEL_BG = (178, 203, 222)
HUD_BG = (18, 18, 18)
TEXT_LIGHT = (245, 245, 245)
PREVIEW_YELLOW = (255, 200, 20, 120)
PREVIEW_RED = (200, 24, 24, 140)
HIGHLIGHT_SRC = (255, 255, 120, 160)
HIGHLIGHT_DST = (120, 255, 160, 160)

def choose_font(name_list, size, bold=False):
    for n in name_list:
        f = pygame.font.match_font(n)
        if f:
            return pygame.font.Font(f, size)
    return pygame.font.SysFont(None, size, bold=bold)

def helvetica(size, bold=False):
    return choose_font(["Helvetica", "Arial", "Liberation Sans", "DejaVu Sans"], size, bold=bold)

def safe_deepcopy(x):
    try:
        return copy.deepcopy(x)
    except Exception:
        return x

class HUD:
    """HUD visual class. Attach to controller (Game) and it will draw right-side HUD,
    move history, small replay controls and handle simple visual interactions.
    It deliberately does NOT contain any gameplay/capture logic.
    """
    def __init__(self, controller, res_dir=None):
        self.controller = controller
        self.width = HUD_WIDTH
        self.res_dir = res_dir or os.path.join(os.path.dirname(__file__), "res")
        # button icons
        self.icon_left = None
        self.icon_right = None
        self.icon_play_pause = None
        try:
            left_path = os.path.join(self.res_dir, "left.png")
            right_path = os.path.join(self.res_dir, "right.png")
            play_pause_path = os.path.join(self.res_dir, "play_pause.png")
            if os.path.exists(left_path):
                self.icon_left = pygame.transform.smoothscale(pygame.image.load(left_path).convert_alpha(), (22,22))
            if os.path.exists(right_path):
                self.icon_right = pygame.transform.smoothscale(pygame.image.load(right_path).convert_alpha(), (22,22))
            if os.path.exists(play_pause_path):
                self.icon_play_pause = pygame.transform.smoothscale(pygame.image.load(play_pause_path).convert_alpha(), (22,22))
        except Exception:
            self.icon_left = self.icon_right = self.icon_play_pause = None

        # fonts
        self.font_title = helvetica(20)
        self.font_small = helvetica(14)
        mono_font = pygame.font.match_font("Consolas") or pygame.font.match_font("Courier New") or pygame.font.match_font("DejaVu Sans Mono")
        if mono_font:
            self.font_mono = pygame.font.Font(mono_font, 14)
        else:
            self.font_mono = pygame.font.SysFont(None, 14)

        # thunder icon
        self.thunder_img = None
        png_path = os.path.join(self.res_dir, "thunder.png")
        if os.path.exists(png_path):
            try:
                img = pygame.image.load(png_path).convert_alpha()
                self.thunder_img = pygame.transform.smoothscale(img, (20,20))
            except Exception:
                self.thunder_img = None
        if self.thunder_img is None:
            glyph_font = helvetica(20, bold=True)
            s = glyph_font.render("⚡", True, (255,210,30))
            surf = pygame.Surface((s.get_width()+6, s.get_height()+6), pygame.SRCALPHA)
            surf.blit(s, (3,3))
            self.thunder_img = surf

        # history / preview state
        self.scroll_offset = 0
        self.selected_idx = None
        self.preview_snapshot = None
        self.preview_active = False
        self.list_rect = None

        # replay/traverse state
        self.replay_mode = False
        self.replay_index = None
        self.replay_playing = False
        self.replay_last_tick = 0
        self.replay_interval_ms = 800

        # control rects
        self.btn_step_back = None
        self.btn_play_pause = None
        self.btn_step_forward = None

        # double-click tracking
        self._last_click_idx = -1
        self._last_click_time = 0

    def rect(self, screen_w, screen_h):
        return pygame.Rect(screen_w - self.width, 0, self.width, screen_h)

    def draw(self, surf):
        r = self.rect(self.controller.width, self.controller.height)
        pygame.draw.rect(surf, HUD_BG, r)

        pad = 14
        x = r.x + pad
        y = r.y + pad

        title = "SuperChess" if (getattr(self.controller, "variant", "") == "super") else "Classic"
        surf.blit(self.font_title.render(title, True, TEXT_LIGHT), (x, y))
        y += 34

        # player blocks
        block_h = 44
        engine = getattr(self.controller, "chess", None)
        wc = getattr(self.controller, "current_turn_color", "") == "white"
        bc = getattr(self.controller, "current_turn_color", "") == "black"
        white_charges = 0; black_charges = 0
        try:
            if engine and hasattr(engine, "charges"):
                white_charges = engine.charges.get("white", 0)
                black_charges = engine.charges.get("black", 0)
        except Exception:
            pass

        self._draw_player_block(surf, x, y, r.width - pad*2, getattr(self.controller, "name_white", "White"), wc, white_charges)
        y += block_h + 8
        self._draw_player_block(surf, x, y, r.width - pad*2, getattr(self.controller, "name_black", "Black"), bc, black_charges)
        y += block_h + 12

        # small replay step controls
        step_h = 28
        step_w = (r.width - pad*2 - 16) // 3
        sbx = x; sby = y
        step_back_rect = pygame.Rect(sbx, sby, step_w, step_h)
        play_rect = pygame.Rect(sbx + step_w + 8, sby, step_w, step_h)
        step_forward_rect = pygame.Rect(sbx + 2*(step_w + 8), sby, step_w, step_h)
        play_label = "Pause" if self.replay_playing else "Play"
        self._draw_button(surf, step_back_rect, "Step", icon=self.icon_left, disabled=(not self._can_replay()))
        self._draw_button(surf, play_rect, play_label, icon=self.icon_play_pause, disabled=(not self._can_replay()))
        self._draw_button(surf, step_forward_rect, "Step", icon=self.icon_right, disabled=(not self._can_replay()))
        self.btn_step_back, self.btn_play_pause, self.btn_step_forward = step_back_rect, play_rect, step_forward_rect
        y += step_h + 12

        # divider and "Move History" label
        pygame.draw.line(surf, (60,60,60), (x, y), (r.right - pad, y), 1)
        y += 12
        surf.blit(self.font_small.render("Move History", True, (170,170,170)), (x, y))
        y += 22

        # history list
        list_h = r.bottom - y - 24
        list_rect = pygame.Rect(x, y, r.width - pad*2, list_h)
        pygame.draw.rect(surf, (8,8,8), list_rect)
        self.list_rect = list_rect

        history = getattr(self.controller, "history", []) or []
        total = len(history)
        max_lines = max(1, list_rect.height // 22)
        if total != getattr(self, "_last_history_len", None) and (self.selected_idx is None or self.selected_idx >= total-2):
            self.scroll_offset = max(0, total - max_lines)
        self._last_history_len = total

        visible_start = self.scroll_offset
        visible_end = min(total, visible_start + max_lines)
        ly = list_rect.y + 6
        for i in range(visible_start, visible_end):
            e = history[i]
            num = f"{i+1:3d}."
            san = e.get("san", "?")
            power = e.get("power")
            text = f"{num} {san}" + (f" [{power}]" if power else "")
            if i == self.selected_idx:
                bg = pygame.Surface((list_rect.w - 4, 20))
                bg.fill((50, 50, 50))
                surf.blit(bg, (list_rect.x + 2, ly))
                color = (255, 230, 170)
            else:
                color = (180,180,180)
            surf.blit(self.font_mono.render(text, True, color), (list_rect.x + 8, ly))
            ly += 22

        # scrollbar
        if total > max_lines:
            sb_h = int(max(8, (max_lines / total) * list_rect.height))
            sb_y = list_rect.y + int((self.scroll_offset / max(1, total - max_lines)) * (list_rect.height - sb_h))
            sb_rect = pygame.Rect(list_rect.right - 10, sb_y, 8, sb_h)
            pygame.draw.rect(surf, (90,90,90), sb_rect, border_radius=4)

    def _draw_player_block(self, surf, x, y, w, name, is_turn, charges):
        block_h = 44
        pygame.draw.rect(surf, (30,30,30), (x, y, w, block_h), border_radius=8)
        if is_turn:
            t = time.time(); pulse = (math.sin(t*3)+1)/2; alpha = int(40 + 70*pulse)
            gl = pygame.Surface((w, block_h), pygame.SRCALPHA)
            gl.fill((RIGHT_PANEL_BG[0], RIGHT_PANEL_BG[1], RIGHT_PANEL_BG[2], alpha))
            surf.blit(gl, (x, y))

        # name (left)
        name_surf = self.font_small.render(name, True, TEXT_LIGHT)
        surf.blit(name_surf, (x + 12, y + (block_h - name_surf.get_height())//2))

        # compute timer for this player (visual only)
        color = "white" if name.lower().startswith("w") else "black"
        timer_val = None
        try:
            timer_val = getattr(self.controller, "remaining", {}).get(color)
            # if this player is the live side, account for active elapsed time
            if timer_val is not None and getattr(self.controller, "current_turn_color", "") == color and getattr(self.controller, "turn_start_ticks", None):
                now = pygame.time.get_ticks()
                elapsed = (now - self.controller.turn_start_ticks) / 1000.0
                timer_val = max(0, timer_val - elapsed)
        except Exception:
            timer_val = None

        # format mm:ss or ∞
        if timer_val is None:
            timer_text = "∞"
        else:
            m = int(timer_val) // 60
            s = int(timer_val) % 60
            timer_text = f"{m}:{s:02d}"
        timer_surf = self.font_small.render(timer_text, True, TEXT_LIGHT)

        # draw timer, thunder icon, and charge count on the right — timer is placed left of thunder
        cnt_surf = self.font_small.render(str(charges), True, TEXT_LIGHT)
        thunder_w = self.thunder_img.get_width()
        # right align the charge count at the right edge with some padding
        cnt_x = x + w - 8 - cnt_surf.get_width()
        cnt_y = y + (block_h - cnt_surf.get_height())//2
        thunder_x = cnt_x - 8 - thunder_w
        thunder_y = y + (block_h - self.thunder_img.get_height())//2
        timer_x = thunder_x - 8 - timer_surf.get_width()
        timer_y = y + (block_h - timer_surf.get_height())//2

        # blit in order: timer, thunder, count
        surf.blit(timer_surf, (timer_x, timer_y))
        surf.blit(self.thunder_img, (thunder_x, thunder_y))
        surf.blit(cnt_surf, (cnt_x, cnt_y))


    def _draw_button(self, surf, rect, text, icon=None, disabled=False):
        color = (60,60,60) if not disabled else (40,40,40)
        pygame.draw.rect(surf, color, rect, border_radius=8)
        lab = self.font_small.render(text, True, (220,220,220) if not disabled else (160,160,160))
        x_offset = rect.x + 10
        if icon:
            surf.blit(icon, (x_offset, rect.y + (rect.height - icon.get_height())//2))
            x_offset += icon.get_width() + 6
        surf.blit(lab, (x_offset, rect.y + (rect.height - lab.get_height())//2))

    def handle_event(self, ev):
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
            mx,my = ev.pos
            if self.btn_step_back and self.btn_step_back.collidepoint(mx,my):
                if self._can_replay():
                    self._on_replay_step(-1); return True
            if self.btn_play_pause and self.btn_play_pause.collidepoint(mx,my):
                if self._can_replay():
                    self._on_replay_toggle_play(); return True
            if self.btn_step_forward and self.btn_step_forward.collidepoint(mx,my):
                if self._can_replay():
                    self._on_replay_step(1); return True
            if self.list_rect and self.list_rect.collidepoint(mx,my):
                local_y = my - self.list_rect.y
                idx = self.scroll_offset + (local_y // 22)
                history_len = len(getattr(self.controller, "history", []) or [])
                if 0 <= idx < history_len:
                    now = pygame.time.get_ticks()
                    if getattr(self, "_last_click_idx", -1) == idx and now - getattr(self, "_last_click_time", 0) < 350:
                        self.selected_idx = idx
                        self._on_preview()
                        self._last_click_idx = -1; self._last_click_time = 0
                    else:
                        self.selected_idx = idx; self._last_click_idx = idx; self._last_click_time = now
                return True
        if ev.type == pygame.MOUSEBUTTONDOWN and ev.button in (4,5):
            mx,my = ev.pos
            if self.list_rect and self.list_rect.collidepoint(mx,my):
                history = getattr(self.controller, "history", []) or []
                max_lines = self.list_rect.height // 22
                max_scroll = max(0, len(history) - max_lines)
                if ev.button == 4:
                    self.scroll_offset = max(0, self.scroll_offset - 1)
                else:
                    self.scroll_offset = min(max_scroll, self.scroll_offset + 1)
                return True
        return False

    def _can_replay(self):
        return self.selected_idx is not None and len(getattr(self.controller, "snapshots", []) or []) > 0

    def _on_preview(self):
        if not self._can_replay(): return
        idx = self.selected_idx
        try:
            self.preview_snapshot = self.controller.snapshot_game_state()
            self.controller.start_replay_preview(idx, destructive=False)
        except Exception:
            traceback.print_exc()
            self.preview_snapshot = None
            return
        self.preview_active = True
        self.replay_mode = True
        self.replay_index = idx
        self.replay_playing = False
        self.replay_last_tick = pygame.time.get_ticks()

    def _on_replay_step(self, delta):
        if not self._can_replay(): return
        if self.replay_index is None:
            self.replay_index = self.selected_idx if self.selected_idx is not None else (len(getattr(self.controller, "history", []) or [])-1 if getattr(self.controller, "history", None) else 0)
        new_idx = max(0, min(len(getattr(self.controller, "history", []) or [])-1, self.replay_index + delta))
        self.replay_index = new_idx
        try:
            self.controller._apply_replay_index_to_preview(new_idx)
        except Exception:
            traceback.print_exc()
        self.selected_idx = new_idx

    def _on_replay_toggle_play(self):
        if not self._can_replay(): return
        self.replay_playing = not self.replay_playing
        if self.replay_playing:
            self.replay_last_tick = pygame.time.get_ticks()

    def _on_return_live(self):
        if self.preview_snapshot:
            try:
                self.controller.restore_game_state(self.preview_snapshot)
            except Exception:
                traceback.print_exc()
        self.preview_snapshot = None
        self.preview_active = False
        self.replay_mode = False
        self.replay_index = None
        self.replay_playing = False
        self.controller.preview_piece_location = None
        self.selected_idx = None


RES_DIR = os.path.join(os.path.dirname(__file__), "res")

class Game:
    def __init__(self):
        pygame.display.init()
        pygame.font.init()

        info = pygame.display.Info()
        # window slightly smaller than full screen so X remains accessible
        target_w = max(960, int(info.current_w * 0.88))
        target_h = max(700, int(info.current_h * 0.88))
        self.width, self.height = target_w, target_h
        self.screen = pygame.display.set_mode((self.width, self.height))
        pygame.display.set_caption("SuperChess")
        self.clock = pygame.time.Clock()

        self.resources = "res"
        icon_src = os.path.join(self.resources, "chess_icon.png")
        if os.path.exists(icon_src):
            try:
                pygame.display.set_icon(pygame.image.load(icon_src))
            except Exception:
                pass
        
                # --- load menu background ---
        bg_path = os.path.join(RES_DIR, "background.jpg")   # RES_DIR is already defined near top of file
        if os.path.exists(bg_path):
            try:
                img = pygame.image.load(bg_path)
                # preserve alpha if PNG, otherwise convert for speed
                try:
                    img = img.convert_alpha()
                except Exception:
                    img = img.convert()
                self.background = pygame.transform.smoothscale(img, (self.width, self.height))
            except Exception:
                self.background = None
        else:
            self.background = None


        # state
        self.state = "menu"   # menu, name_entry, playing, end
        self.game_mode = None
        self.variant = "classic"
        self.timer_mode = "timeless"

        self.timer_presets = {"bullet": 60, "blitz": 300, "rapid": 600, "classic": 1800, "timeless": None}

        # runtime
        self.chess = None
        self.utils = Utils()

        # layout
        self.square_length = None
        self.TOP_BAR = 96
        self.BOARD_MARGIN = 40

        # load board image
        bsrc = os.path.join(RES_DIR, "board.png")
        self.board_img = pygame.image.load(bsrc).convert() if os.path.exists(bsrc) else None

        # names & timers
        self.name_white = "White"
        self.name_black = "Black (AI)"
        self.remaining = {"white": None, "black": None}
        self.turn_start_ticks = None
        self.current_turn_color = "white"
        #timer will not start until white makes first move
        self.timers_started= False

        # HUD / visual bookkeeping
        self.hud = HUD(self, res_dir=RES_DIR)
        self.history = []
        self.snapshots = []
        self.preview_piece_location = None
        self.preview_highlight_move = None
        self._last_seen_move_id = None
        self.captured_white = []
        self.captured_black = []

        self.show_resign_modal = False

    def start_game(self):
        while True:
            if self.state == "menu":
                self.menu()                 # sets game_mode/variant/timer
                # ask names
                self.name_entry()
                self.start_variant()
                self.state = "playing"
                continue

            if self.state == "playing":
                self.loop_playing()
                if self.state != "playing":
                    continue

            if self.state == "end":
                self.end_screen()
                continue

            self.clock.tick(60)

    # ---------------- Menu ----------------
    def menu(self):
        big = pygame.font.SysFont("comicsansms", 56)
        med = pygame.font.SysFont("comicsansms", 28)
        small = pygame.font.SysFont("comicsansms", 22)

        self.game_mode = None
        self.variant = "classic"
        self.timer_mode = "timeless"

        cx = self.width // 2
        y = 120
        pvp_btn = pygame.Rect(cx - 160, y + 80, 320, 60)
        engine_btn = pygame.Rect(cx - 160, y + 160, 320, 60)
        classic_btn = pygame.Rect(cx - 180, y + 260, 160, 52)
        super_btn = pygame.Rect(cx + 20, y + 260, 160, 52)

        timer_labels = list(self.timer_presets.keys())
        timer_rects = []
        timer_y = y + 340
        tw = 150; spacing = 10
        row_w = len(timer_labels) * tw + (len(timer_labels)-1)*spacing
        start_x = cx - row_w // 2
        for i,label in enumerate(timer_labels):
            timer_rects.append((pygame.Rect(start_x + i*(tw+spacing), timer_y, tw, 48), label))

        while True:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    pygame.quit(); sys.exit()
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                    pygame.quit(); sys.exit()
                if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                    mx,my = ev.pos
                    if pvp_btn.collidepoint(mx,my):
                        self.game_mode = "pvp"
                    elif engine_btn.collidepoint(mx,my):
                        self.game_mode = "engine"
                    elif classic_btn.collidepoint(mx,my):
                        self.variant = "classic"
                    elif super_btn.collidepoint(mx,my) and HAS_SUPER:
                        self.variant = "super"
                    for r,label in timer_rects:
                        if r.collidepoint(mx,my):
                            self.timer_mode = label
                    if self.game_mode:
                        return

            if getattr(self, "background", None):
                self.screen.blit(self.background, (0,0))
                # subtle dark dim so buttons/readable
                dim = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
                dim.fill((0,0,0,150))
                self.screen.blit(dim, (0,0))
            else:
                self.screen.fill((245,245,245))
                
            

            # glass panel behind the menu content (adjust x,y,w,h to taste)
            box_w = 850
            box_h = 400
            
            panel_rect = pygame.Rect(self.width// 2 - box_w // 2, y + 2, box_w, box_h)
            self.draw_glass_panel(panel_rect, alpha=80)

            
            title = big.render("SuperChess", True, (12,12,12))
            self.screen.blit(title, (self.width//2 - title.get_width()//2, y))

            def draw_btn(rect, label, selected=False):
                pygame.draw.rect(self.screen, (10,10,10), rect, 0 if selected else 2)
                txt = med.render(label, True, (255,255,255) if selected else (0,0,0))
                self.screen.blit(txt, (rect.centerx - txt.get_width()//2, rect.centery - txt.get_height()//2))

            draw_btn(pvp_btn, "Play vs Player", selected=(self.game_mode=="pvp"))
            draw_btn(engine_btn, "Play vs AI", selected=(self.game_mode=="engine"))
            draw_btn(classic_btn, "Classic", selected=(self.variant=="classic"))
            draw_btn(super_btn, "Super" if HAS_SUPER else "Super (missing)", selected=(self.variant=="super" and HAS_SUPER))

            label = small.render("Timer Mode", True, (40,40,40))
            self.screen.blit(label, (self.width//2 - label.get_width()//2, timer_y-28))
            for r,label in timer_rects:
                draw_btn(r, label.capitalize(), selected=(self.timer_mode==label))

            footer = small.render("Press Esc to quit", True, (80,80,80))
            self.screen.blit(footer, (self.width - footer.get_width() - 10, self.height - footer.get_height() - 6))

            pygame.display.flip()
            self.clock.tick(60)

    # ---------------- Name entry modal ----------------
    def name_entry(self):
        """Modal dialog that asks for White and Black player names. Blocks until finished."""
        font = pygame.font.SysFont("comicsansms", 24)
        box_w = 550; box_h = 280
        box = pygame.Rect(self.width//2 - box_w//2, self.height//2 - box_h//2, box_w, box_h)
        input_white = ""
        input_black = ""
        active = "white"  # 'white' or 'black'
        prompt = "Enter player names (press Enter to continue)"

        # default black name when engine selected
        if self.game_mode == "engine":
            input_black = "AI"

        while True:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    pygame.quit(); sys.exit()
                if ev.type == pygame.KEYDOWN:
                    if ev.key == pygame.K_ESCAPE:
                        pygame.quit(); sys.exit()
                    if ev.key == pygame.K_TAB:
                        active = "black" if active == "white" else "white"
                    elif ev.key == pygame.K_RETURN:
                        # accept, but ensure non-empty
                        if input_white.strip() == "":
                            input_white = "White"
                        if input_black.strip() == "":
                            input_black = "Black" if self.game_mode=="pvp" else "AI"
                        self.name_white = input_white.strip()
                        self.name_black = input_black.strip()
                        return
                    elif ev.key == pygame.K_BACKSPACE:
                        if active == "white":
                            input_white = input_white[:-1]
                        else:
                            input_black = input_black[:-1]
                    else:
                        ch = ev.unicode
                        if ch and ord(ch) >= 32:
                            if active == "white":
                                input_white += ch
                            else:
                                input_black += ch
                if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                    mx,my = ev.pos
                    # click to select input areas
                    wbox = pygame.Rect(box.x+24, box.y+60, box.w-48, 40)
                    bbox = pygame.Rect(box.x+24, box.y+130, box.w-48, 40)
                    if wbox.collidepoint(mx,my): active = "white"
                    elif bbox.collidepoint(mx,my): active = "black"

            # draw modal
            # draw background and modal glass
            if getattr(self, "background", None):
                self.screen.blit(self.background, (0,0))
                dim = pygame.Surface((self.width, self.height), pygame.SRCALPHA)
                dim.fill((0,0,0,150))   # darker dim for modal focus
                self.screen.blit(dim, (0,0))
            else:
                self.screen.fill((230,230,230))

            # draw semi-transparent modal (glass) in place of opaque white box
            glass = pygame.Surface((box.w, box.h), pygame.SRCALPHA)
            glass.fill((255,255,255,100))   # alpha=200 to keep inputs readable; lower for more transparency
            pygame.draw.rect(glass, (255,255,255,30), glass.get_rect(), 1, border_radius=8)
            self.screen.blit(glass, (box.x, box.y))

            title = font.render("Enter Player Names", True, (10, 10, 10))
            self.screen.blit(title, (box.centerx - title.get_width()//2, box.y + 8))

            label_w = font.render("White name:", True, (10,10,10))
            self.screen.blit(label_w, (box.x+24, box.y+55))
            wbox = pygame.Rect(box.x+24, box.y+90, box.w-48, 40)
            pygame.draw.rect(self.screen, (245,245,245), wbox)
            txtw = font.render(input_white if input_white else "White", True, (10,10,10))
            self.screen.blit(txtw, (wbox.x+3, wbox.y+3))
            if active == "white":
                pygame.draw.rect(self.screen, (0,0,0), wbox, 2)

            label_b = font.render("Black name:", True, (10,10,10))
            self.screen.blit(label_b, (box.x+24, box.y+140))
            bbox = pygame.Rect(box.x+24, box.y+175, box.w-48, 40)
            pygame.draw.rect(self.screen, (245,245,245), bbox)
            txtb = font.render(input_black if input_black else ("AI" if self.game_mode=="engine" else "Black"), True, (10,10,10))
            self.screen.blit(txtb, (bbox.x+3, bbox.y+3))
            if active == "black":
                pygame.draw.rect(self.screen, (0,0,0), bbox, 2)

            hint = font.render("Enter: Continue  Tab: Switch input", True, (40,40,40))
            self.screen.blit(hint, (box.centerx - hint.get_width()//2, box.y + box.h - 34))

            pygame.display.flip()
            self.clock.tick(60)

    # ---------------- Setup variant ----------------
    def start_variant(self):
        # compute square size to fit board centered and leave space for top HUD
        usable_h = self.height - self.TOP_BAR - self.BOARD_MARGIN
        usable_w = self.width - 2*self.BOARD_MARGIN
        sq = min(usable_w // 8, usable_h // 8)
        self.square_length = max(28, sq)

        board_size = self.square_length * 8
        bx = BOARD_MARGIN
        by = TOP_BAR
        self.board_top_left = (bx, by)
        self.board_rect = pygame.Rect(bx, by, board_size, board_size)

        panel_x = bx + board_size + 12
        panel_w = self.width - panel_x - BOARD_MARGIN
        panel_h = board_size
        self.right_panel_rect = pygame.Rect(panel_x, by, panel_w, panel_h)

        # pre-scale board image
        if self.board_img:
            try:
                self.board_img_scaled = pygame.transform.smoothscale(self.board_img, (board_size, board_size))
            except Exception:
                self.board_img_scaled = pygame.transform.scale(self.board_img, (board_size, board_size))
        else:
            self.board_img_scaled = None

        # build board locations grid
        board_locations = []
        for x in range(8):
            row = []
            for y in range(8):
                px = bx + x * self.square_length
                py = by + y * self.square_length
                row.append([px, py])
            board_locations.append(row)

        pieces_src = os.path.join(self.resources, "pieces.png")
        if self.variant == "super" and HAS_SUPER:
            self.chess = SuperChess(self.screen, pieces_src, board_locations, self.square_length)
        else:
            self.chess = Chess(self.screen, pieces_src, board_locations, self.square_length)

        # timers
        base = self.timer_presets.get(self.timer_mode, None)
    # Always set remaining to base value at start of new game/restart
        self.remaining = {"white": base, "black": base}  # Only set here, never elsewhere
        

        # HUD / visual bookkeeping
        self.hud = HUD(self, res_dir=RES_DIR)
        self.history = []
        self.snapshots = []
        self.preview_piece_location = None
        self.preview_highlight_move = None
        self._last_seen_move_id = None
        self.captured_white = []
        self.captured_black = []
         # DO NOT start turn_start_ticks here — timers begin after White's first move
        self.turn_start_ticks = None
        self.timers_started = False

    # ---------------- Playing loop/frame ----------------
    def loop_playing(self):
        resign_clicked = False
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                pygame.quit(); sys.exit()
            # HUD visual event interception (visual-only)
            if ev.type in (pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP):
                try:
                    mx,my = ev.pos
                    if self.hud.rect(self.width, self.height).collidepoint(mx,my):
                        if self.hud.handle_event(ev):
                            continue
                except Exception:
                    pass
            if ev.type == KEYDOWN:
                if ev.key == K_ESCAPE:
                    pygame.quit(); sys.exit()
                if ev.key == K_SPACE:
                    self.start_variant()
                if ev.key == K_s:
                    # toggle preview if using SuperChess
                    if isinstance(self.chess, SuperChess):
                        if not self.chess.power_preview_active:
                            self.chess.start_power_preview_for_selected(lightning_sound)
                        else:
                            self.chess.cancel_power_preview()
            if ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                mx, my = ev.pos
                # --- Handle Resign Button Click ---
                for ev in pygame.event.get(pygame.MOUSEBUTTONDOWN):
                    if self.resign_btn_rect.collidepoint(ev.pos):
                        self.show_resign_modal = True
                        return
                # if preview active, attempt commit if a board square clicked
                if isinstance(self.chess, SuperChess) and self.chess.power_preview_active:
                    clicked = False
                    for i in range(8):
                        for j in range(8):
                            r = pygame.Rect(self.board_top_left[0] + i*self.square_length,
                                            self.board_top_left[1] + j*self.square_length,
                                            self.square_length, self.square_length)
                            if r.collidepoint(mx,my):
                                # commit with board coords (i,j)
                                # NOTE: preview_moves stores lists like [x,y]. The UI click yields (i,j).
                                # Accept either representation (tuple or list) when testing membership.
                                if ([i, j] in self.chess.preview_moves) or ((i, j) in self.chess.preview_moves) or self.chess.preview_moves == [] or self.chess.power_preview_name in ("fortress_field","sacrifice"):
                                    # commit (some powers accept None target)
                                    self.chess.commit_power_preview((i,j))
                                else:
                                    # not valid preview target - cancel
                                    self.chess.cancel_power_preview()
                                clicked = True
                                break
                        if clicked: break
                    continue

                # otherwise, pass click into chess selection handler: we mimic left click selection by setting util click state
                # The Chess.get_selected_square reads from Utils which reads pygame events; to keep same behavior we simply let Chess.move_piece handle it.
                # No extra handling here.

        # timers handling
        self.update_timers_and_timeout()

        # draw background
        self.screen.fill((28,28,28))

        # draw top HUD
        self.draw_top_hud()

        # --- Draw Resign Button ---
        resign_btn_w = 110  # new width
        resign_btn_h = 32   # new height
        resign_btn_x = self.right_panel_rect.x + (self.right_panel_rect.width - resign_btn_w) // 2 -165
        resign_btn_y = self.board_top_left[1] + self.board_rect.height // 2 - resign_btn_h // 2 - 260  # adjust -10 for vertical centering
        resign_btn_rect = pygame.Rect(resign_btn_x, resign_btn_y, resign_btn_w, resign_btn_h)
        pygame.draw.rect(self.screen, (200, 24, 24), resign_btn_rect, border_radius=8)
        font = pygame.font.SysFont("comicsansms", 20)
        # Load resign icon
        resign_icon = None
        try:
            icon_path = os.path.join(self.resources, "resign.png")
            if os.path.exists(icon_path):
                resign_icon = pygame.image.load(icon_path).convert_alpha()
        except Exception:
            pass
        label = font.render("Resign", True, (200, 200, 200))
        # Draw icon to left of text
        if resign_icon:
            icon_size = 24
            resign_icon = pygame.transform.smoothscale(resign_icon, (icon_size, icon_size))
            icon_x = resign_btn_rect.x + 10
            icon_y = resign_btn_rect.centery - icon_size // 2
            self.screen.blit(resign_icon, (icon_x, icon_y))
            text_x = icon_x + icon_size + 8
        else:
            text_x = resign_btn_rect.x + 10
        text_y = resign_btn_rect.centery - label.get_height() // 2
        self.screen.blit(label, (text_x, text_y))

        # Handle resign click
        for ev in pygame.event.get(pygame.MOUSEBUTTONDOWN):
            if resign_btn_rect.collidepoint(ev.pos):
                self.handle_resign()
                return
            
        self.draw_captured_side(resign_btn_rect)

        # draw board
        self.draw_board()

        # Draw fortress zones (red) if any - so players can see defense zones
        if isinstance(self.chess, SuperChess) and getattr(self.chess, "fortress_zones", None):
            red_surf = pygame.Surface((self.square_length, self.square_length), pygame.SRCALPHA)
            red_surf.fill((200, 24, 24, 120))  # semi-transparent red
            for zone in self.chess.fortress_zones:
                for (zx, zy) in zone['squares']:
                    rx = self.board_top_left[0] + zx * self.square_length
                    ry = self.board_top_left[1] + zy * self.square_length
                    self.screen.blit(red_surf, (rx, ry))

        # let chess handle input & moves (it reads mouse events via Utils)
        # We call move_piece for side-to-move to avoid double "Turn" texts in chess.play_turn.
        side = "black" if self.chess.turn["black"] else "white"
        self.chess.move_piece(side)

        # If vs AI and black to move then call ai_move()
        if self.game_mode == "engine" and (not self.chess.winner) and (not self.chess.turn["white"]):
            # small pause for realism
            pygame.time.delay(180)
            self.chess.ai_move()
        
        self.record_last_move()

        # detect turn change and adjust timers
        # detect turn change and adjust timers
        new_turn = "black" if self.chess.turn["black"] else "white"
        if new_turn != self.current_turn_color:
            # commit elapsed for the player who just moved
            try:
                self.commit_elapsed_to_remaining(self.current_turn_color)
            except Exception:
                pass

            # remember which side just moved
            moved_side = self.current_turn_color

            # switch to the new side
            self.current_turn_color = new_turn

            # timers: start only after White has made their first move
            # - if timers haven't been started yet, only start them when the side that just moved was WHITE
            # - once timers_started True we set a fresh turn_start_ticks for the new player
            if not getattr(self, "timers_started", False):
                if moved_side == "white":
                    # White made the first move -> start the clocks now; the next player (Black) begins counting
                    self.timers_started = True
                    self.turn_start_ticks = pygame.time.get_ticks()
                else:
                    # still waiting for White's first move; leave turn_start_ticks None (clocks paused)
                    self.turn_start_ticks = None
            else:
                # normal flow: start counting for the new player now
                self.turn_start_ticks = pygame.time.get_ticks()

        # draw pieces
        self.chess.draw_pieces()

        # draw preview highlights if active
        if isinstance(self.chess, SuperChess) and self.chess.power_preview_active:
            # default preview highlight (yellow)
            yellow_surf = pygame.Surface((self.square_length, self.square_length), pygame.SRCALPHA)
            yellow_surf.fill((255, 200, 20, 120))

            # red surf for sacrifice / capture indicators
            red_surf = pygame.Surface((self.square_length, self.square_length), pygame.SRCALPHA)
            red_surf.fill((200, 24, 24, 140))

            pname = self.chess.power_preview_name

            # Pawn sacrifice: show red highlight on LEFT/RIGHT capture squares, and yellow on pawn square
            if pname == "sacrifice" and self.chess.preview_source:
                sf, sr = self.chess.preview_source
                sx, sy = self.chess.piece_location[sf][sr][2]
                # pawn square
                px = self.board_top_left[0] + sx * self.square_length
                py = self.board_top_left[1] + sy * self.square_length
                self.screen.blit(yellow_surf, (px, py))
                # left and right capture squares
                for ox in (-1, 1):
                    nx = sx + ox
                    ny = sy
                    if 0 <= nx < 8 and 0 <= ny < 8:
                        rx = self.board_top_left[0] + nx * self.square_length
                        ry = self.board_top_left[1] + ny * self.square_length
                        self.screen.blit(red_surf, (rx, ry))

            else:
                # Normal preview: highlight preview_moves (mostly yellow)
                for mv in self.chess.preview_moves:
                    # mv may be list or tuple
                    px, py = mv
                    rx = self.board_top_left[0] + px * self.square_length
                    ry = self.board_top_left[1] + py * self.square_length
                    self.screen.blit(yellow_surf, (rx, ry))

        # draw captured pieces and timers inside top HUD
        #self.draw_captured_top()

        # HUD (right panel, move history, replay controls)
        try:
            self.hud.draw(self.screen)
        except Exception:
            traceback.print_exc()

        # winner/stalemate detection
        if self.chess.winner:
            if self.chess.winner == "Stalemate":
                self.end_message = "Tie by Stalemate!"
            elif self.chess.winner == "Threefold":
                self.end_message = "Draw by Threefold Repetition!"
            else:
                self.end_message = f"{self.chess.winner} wins!"
            self.state = "end"

        pygame.display.flip()
        self.clock.tick(60)

    # ---------------- timers ----------------
    def commit_elapsed_to_remaining(self, color):
        """
        Subtract elapsed time since self.turn_start_ticks from remaining[color].
        Safe no-op if timers not started or timestamp missing.
        """
        try:
            if self.remaining.get(color) is None:
                return
            if not getattr(self, "timers_started", False):
                return
            if not self.turn_start_ticks:
                return
            now = pygame.time.get_ticks()
            elapsed = (now - self.turn_start_ticks) / 1000.0
            if elapsed > 0:
                self.remaining[color] = max(0, self.remaining[color] - elapsed)
        except Exception:
            # never raise – timing should not crash the game
            pass


    def update_timers_and_timeout(self):
        """
        Called each frame to check the running player's clock and end the game on timeout.
        """
        # timers must have been started by White's first move
        if not getattr(self, "timers_started", False):
            return

        color = self.current_turn_color
        if self.remaining.get(color) is None:
            return
        if not self.turn_start_ticks:
            return

        now = pygame.time.get_ticks()
        elapsed = (now - self.turn_start_ticks) / 1000.0
        left = self.remaining[color] - elapsed

        # update UI continuity (we keep remaining as-is; display uses remaining - elapsed).
        if left <= 0:
            # The side 'color' ran out of time, so the other side wins on time.
            loser = color
            winner_side = "white" if loser == "black" else "black"
            winner_name = self.name_white if winner_side == "white" else self.name_black

            # set winner state (use a special marker so UI can show descriptive message)
            try:
                self.chess.winner = "Timeout"
            except Exception:
                # if chess.winner isn't used, still set end_message and state
                pass
            self.end_message = f"{winner_name} wins on time!"
            self.state = "end"



                
    def draw_glass_panel(self, rect, color=(255,255,255), alpha=110, border=True, border_radius=12):
        """
        rect: pygame.Rect
        Draws a semi-transparent 'glass' panel at rect.
        alpha: 0..255 (0 transparent, 255 fully opaque)
        """
        surf = pygame.Surface((rect.w, rect.h), pygame.SRCALPHA)
        surf.fill((color[0], color[1], color[2], alpha))
        if border:
            # subtle border
            pygame.draw.rect(surf, (255,255,255,30), surf.get_rect(), width=1, border_radius=border_radius)
        self.screen.blit(surf, (rect.x, rect.y))



    # ---------------- drawing helpers ----------------
    def draw_board(self):
        # board background
        if self.board_img_scaled:
            self.screen.blit(self.board_img_scaled, self.board_top_left)
        else:
            light = (246,246,238); dark = (120,120,90)
            for x in range(8):
                for y in range(8):
                    r = pygame.Rect(self.board_top_left[0] + x*self.square_length,
                                    self.board_top_left[1] + y*self.square_length,
                                    self.square_length, self.square_length)
                    pygame.draw.rect(self.screen, light if (x+y)%2==0 else dark, r)

        # file labels a..h bottom, rank labels left
        fnt = pygame.font.SysFont("consolas", max(14, self.square_length//4))
        files = "abcdefgh"
        for i,ch in enumerate(files):
            tx = fnt.render(ch, True, (200,200,200))
            x = self.board_top_left[0] + i*self.square_length + self.square_length//2 - tx.get_width()//2
            y = self.board_top_left[1] + 8*self.square_length + 6
            self.screen.blit(tx,(x,y))
        for j in range(8):
            num = 8 - j
            tx = fnt.render(str(num), True, (200,200,200))
            x = self.board_top_left[0] - 18
            y = self.board_top_left[1] + j*self.square_length + self.square_length//2 - tx.get_height()//2
            self.screen.blit(tx,(x,y))

    def draw_top_hud(self):
        # top bar background
        bar_h = self.TOP_BAR - 8
        pygame.draw.rect(self.screen, (16,16,16), (0,0,self.width, bar_h))
        # center turn indicator
        big = pygame.font.SysFont("comicsansms", 26)
        turn_text = f"Turn: {'Black' if self.current_turn_color == 'black' else 'White'}"
        txt = big.render(turn_text, True, (220,220,220))
        self.screen.blit(txt, (self.width//2 - txt.get_width()//2, 8))

        # (no names or timers shown here — moved to right HUD)


    def draw_captured_top(self):
        """Draw captured piece images inline beneath player names (no overlap)."""
        captured = getattr(self.chess, "captured", [])
        # captured list may be strings or lists in some versions; ensure flat list of piece names
        flat = []
        for item in captured:
            if isinstance(item, str):
                flat.append(item)
            elif isinstance(item, (list,tuple)):
                # some earlier code appended [name,...] - guard
                if item: flat.append(item[0] if isinstance(item[0],str) else str(item))
            else:
                flat.append(str(item))

        white_lost = [p for p in flat if p.startswith("white_")]
        black_lost = [p for p in flat if p.startswith("black_")]

        # scale piece images to small icons
        icon_size = max(20, self.square_length // 3)
        # left (white lost) draw starting below white name
        x = 12
        y = 8 + 28 + 24  # approximate under names
        for idx, name in enumerate(white_lost):
            surf = pygame.Surface((self.square_length, self.square_length), pygame.SRCALPHA)
            self.chess.chess_pieces.draw(surf, name, (0,0))
            icon = pygame.transform.smoothscale(surf, (icon_size, icon_size))
            self.screen.blit(icon, (x + idx * (icon_size + 4), y))

        # right (black lost)
        x = self.width - 12 - len(black_lost)*(icon_size+4)
        y = 8 + 28 + 24
        for idx, name in enumerate(black_lost):
            surf = pygame.Surface((self.square_length, self.square_length), pygame.SRCALPHA)
            self.chess.chess_pieces.draw(surf, name, (0,0))
            icon = pygame.transform.smoothscale(surf, (icon_size, icon_size))
            self.screen.blit(icon, (x + idx * (icon_size + 4), y))
            
    def draw_captured_side(self, anchor_rect):
        """Draw a captured-pieces card inside the right panel, anchored under anchor_rect (Resign button)."""
        try:
            captured = getattr(self.chess, "captured", []) or []
            # normalize to strings
            flat = []
            for it in captured:
                if isinstance(it, str):
                    flat.append(it)
                elif isinstance(it, (list,tuple)) and it:
                    flat.append(it[0] if isinstance(it[0], str) else str(it))
                else:
                    flat.append(str(it))

            white_lost = [p for p in flat if p.startswith("white_")]
            black_lost = [p for p in flat if p.startswith("black_")]

            # layout inside right panel below anchor_rect
            pad = 12
            cap_x = self.right_panel_rect.x + pad
            cap_w = max(140, self.right_panel_rect.width - pad*2)
            cap_y = anchor_rect.bottom + 12
            cap_h = 160  # enough for two rows of thumbs; tweak if needed

            # background card
            pygame.draw.rect(self.screen, (34,34,34), (cap_x, cap_y, cap_w, cap_h), border_radius=8)
            # header
            hdr_font = pygame.font.SysFont(None, 16)
            hdr = hdr_font.render("Captured Pieces", True, (200,200,200))
            self.screen.blit(hdr, (cap_x + 8, cap_y + 8))

            # draw two rows: white lost (top), black lost (bottom)
            icon_size = min(48, max(18, self.square_length // 3))
            row1_y = cap_y + 36
            row2_y = row1_y + icon_size + 8
            start_x = cap_x + 8

            def _draw_row(pieces, y):
                x = start_x
                for p in pieces:
                    try:
                        surf = pygame.Surface((self.square_length, self.square_length), pygame.SRCALPHA)
                        # reuse piece atlas drawing from chess pieces (same as draw_captured_top)
                        self.chess.chess_pieces.draw(surf, p, (0, 0))
                        icon = pygame.transform.smoothscale(surf, (icon_size, icon_size))
                        self.screen.blit(icon, (x, y))
                    except Exception:
                        # fallback placeholder
                        pygame.draw.rect(self.screen, (120,120,120), (x, y, icon_size, icon_size), border_radius=6)
                    x += icon_size + 6

            _draw_row(white_lost, row1_y)
            _draw_row(black_lost, row2_y)
        except Exception:
            # visual-only; do not raise or change game logic
            pass


    
    def snapshot_game_state(self):
        """
        Create a deep snapshot of the engine state used for history/preview.
        We include common castling/move flags so restores don't lose castling rights.
        """
        snap = {}
        # deep-copy main board/piece map (adjust key if your engine uses different name)
        try:
            snap['piece_location'] = copy.deepcopy(getattr(self.chess, 'piece_location', {}))
        except Exception:
            snap['piece_location'] = {}

        # include move history, turn, FEN-like fields if present
        for attr in ('turn', 'move_history', 'halfmove_clock', 'fullmove_number'):
            if hasattr(self.chess, attr):
                try:
                    snap[attr] = copy.deepcopy(getattr(self.chess, attr))
                except Exception:
                    snap[attr] = getattr(self.chess, attr)

        # Defensive: capture common castling/move flags so restores preserve castling rights
        castling_attrs = [
            'castling_rights', 'castle_rights', 'can_castle', 'can_castle_kingside',
            'can_castle_queenside', 'can_castle_white', 'can_castle_black',
            'has_moved', 'king_moved', 'rook_moved', 'castles',
            'white_can_castle_kingside','white_can_castle_queenside',
            'black_can_castle_kingside','black_can_castle_queenside'
        ]
        for a in castling_attrs:
            if hasattr(self.chess, a):
                try:
                    snap[a] = copy.deepcopy(getattr(self.chess, a))
                except Exception:
                    snap[a] = getattr(self.chess, a)

        # other metadata useful for UI preview
        snap['_timestamp'] = time.time()
        return snap


    def restore_game_state(self, snap):
        """
        Restore a snapshot previously created by snapshot_game_state.
        Only restores visual/engine state fields and castling flags; does not alter UI stacks.
        """
        if not snap:
            return

        # restore main piece map if present
        try:
            if 'piece_location' in snap:
                # replace engine's piece_location with the snapshot's copy
                setattr(self.chess, 'piece_location', copy.deepcopy(snap['piece_location']))
        except Exception:
            pass

        # restore simple attributes if present
        for attr in ('turn', 'move_history', 'halfmove_clock', 'fullmove_number'):
            if attr in snap:
                try:
                    setattr(self.chess, attr, copy.deepcopy(snap[attr]))
                except Exception:
                    try:
                        setattr(self.chess, attr, snap[attr])
                    except Exception:
                        pass

        # restore castling/move flags
        castling_attrs = [
            'castling_rights', 'castle_rights', 'can_castle', 'can_castle_kingside',
            'can_castle_queenside', 'can_castle_white', 'can_castle_black',
            'has_moved', 'king_moved', 'rook_moved', 'castles',
            'white_can_castle_kingside','white_can_castle_queenside',
            'black_can_castle_kingside','black_can_castle_queenside'
        ]
        for a in castling_attrs:
            if a in snap:
                try:
                    setattr(self.chess, a, copy.deepcopy(snap[a]))
                except Exception:
                    try:
                        setattr(self.chess, a, snap[a])
                    except Exception:
                        pass

        # optional: if engine uses a fen string representation, restore if present
        if 'fen' in snap and hasattr(self.chess, 'set_fen'):
            try:
                self.chess.set_fen(snap['fen'])
            except Exception:
                pass

        # After restoring, request a board redraw in the UI (non-invasive)
        try:
            self.update_board_visuals()
        except Exception:
            # some codebases use a different redraw method; ignore if not available
            pass


    

    def _apply_replay_index_to_preview(self, idx):
        """
        Apply a history index for preview/stepping.
        - Applies the snapshot (via replay_to_index) so the engine/board reflect that history index.
        - Plays a move or capture sound when stepping through history so audio matches the visual preview.
        Returns True on success, False otherwise.
        """
        try:
            if idx is None:
                return False

            # let replay_to_index handle most of the snapshot/apply UI state (it sets hud.preview_snapshot, preview flags, etc.)
            ok = False
            try:
                ok = self.replay_to_index(idx)
            except Exception:
                # fallback: if replay_to_index isn't available for some reason try original preview application below
                ok = False

            # If replay_to_index applied the snapshot, play the appropriate sound for that index.
            # Determine if the step involved a capture by comparing captured lengths between snapshots.
            try:
                snap_idx = idx + 1 if idx + 1 < len(self.snapshots) else idx
                if 0 <= snap_idx < len(self.snapshots):
                    snap = self.snapshots[snap_idx]
                    eng = snap.get('engine', snap)
                    # find previous snapshot's captured count (if exists)
                    prev_captured_len = 0
                    prev_idx = snap_idx - 1
                    if prev_idx >= 0 and prev_idx < len(self.snapshots):
                        prev_snap = self.snapshots[prev_idx]
                        prev_eng = prev_snap.get('engine', prev_snap)
                        prev_captured_len = len(prev_eng.get('captured', []) or [])

                    cur_captured_len = len(eng.get('captured', []) or [])
                    captures = cur_captured_len > prev_captured_len

                    # Play capture/move sound so stepping has audio feedback.
                    # Use module-level move_sound / capture_sound if they exist.
                    try:
                        if captures:
                            try:
                                capture_sound.play()
                            except Exception:
                                pass
                        else:
                            try:
                                move_sound.play()
                            except Exception:
                                pass
                    except Exception:
                        # ignore if sounds unavailable; preview should remain functional
                        pass

                    # If replay_to_index didn't run above, also set minimal preview state for backward compatibility
                    if not ok:
                        pl = eng.get('piece_location')
                        self.preview_piece_location = safe_deepcopy(pl)
                        lm = eng.get('last_move_meta') or eng.get('last_move')
                        self.preview_highlight_move = None
                        if lm:
                            try:
                                if isinstance(lm, dict):
                                    src = lm.get('src'); dst = lm.get('dst')
                                else:
                                    src, dst = lm[0], lm[1]
                                if src is not None and dst is not None:
                                    # unify to internal highlight tuple format used elsewhere
                                    self.preview_highlight_move = ((int(src[0]), int(src[1])), (int(dst[0]), int(dst[1])))
                            except Exception:
                                self.preview_highlight_move = None

                        self.hud.preview_active = True
                        self.hud.replay_mode = True
                        self.hud.replay_index = idx
                        self.hud.selected_idx = idx
                        # if last index, automatically return to live UI (keeps behaviour consistent)
                        if idx == len(self.history) - 1:
                            try:
                                self.hud._on_return_live()
                            except Exception:
                                pass

                    return True

            except Exception:
                # swallow sound/preview errors so stepping doesn't crash the app
                traceback.print_exc()
                return ok if ok else False

            return ok
        except Exception:
            traceback.print_exc()
            return False


    def start_replay_preview(self, idx, destructive=False):
        return self.replay_to_index(idx, destructive=destructive)

    def replay_to_index(self, idx,destructive=False):
        """
        Jump to history index idx for preview. This now restores the engine snapshot so
        board and castling flags are accurate for the preview.
        """
        if not (0 <= idx < len(self.snapshots)):
            return

        snap = self.snapshots[idx]

        # restore engine state for preview (visual-only, we keep a saved live copy elsewhere)
        try:
            # keep a copy of the current live state if not already saved
            if not hasattr(self, 'live_snapshot'):
                self.live_snapshot = self.snapshot_game_state()
        except Exception:
            pass

        # apply the chosen historical snapshot to the engine so preview shows the correct position
        try:
            self.restore_game_state(snap)
        except Exception:
            pass

        # set preview UI markers so other parts of the UI know we're previewing
        self.previewing = True
        self.preview_index = idx
        # let the HUD know (if you have such a field)
        try:
            self.hud.preview_snapshot = snap
            self.hud.preview_index = idx
        except Exception:
            pass

        # force a redraw to show the restored snapshot immediately
        try:
            self.draw_board()
            pygame.display.flip()
        except Exception:
            pass



    def record_last_move(self):
        """
        Inspect chess.last_move / last_move_meta and append to history only once.
        This function intentionally does NOT manipulate turn-start timestamps.
        The main loop's turn-change block handles timer start/flip.
        It does set a draw-on-only-kings condition by marking chess.winner = "InsufficientMaterial".
        """
        try:
            # do nothing while previewing or after game end
            if getattr(self.hud, "preview_active", False) or getattr(self.hud, "replay_mode", False) or self.state == "end":
                return

            e = self.chess
            meta = getattr(e, "last_move_meta", None)
            if meta is not None:
                fingerprint = str(meta)
                if fingerprint == getattr(self, "_last_seen_move_id", None):
                    return

                san = self._meta_to_san(meta)
                power = meta.get('type') if meta.get('type') and meta.get('type') != 'move' else None

                # play move/capture sound
                captured = meta.get('captured', [])
                try:
                    if captured:
                        capture_sound.play()
                    else:
                        move_sound.play()
                except Exception:
                    pass

                entry = {'idx': len(self.history), 'san': san, 'meta': safe_deepcopy(meta), 'power': power}
                self.history.append(entry)
                snap = self.snapshot_game_state()
                self.snapshots.append(snap)

                # set seen id and HUD index
                self._last_seen_move_id = fingerprint
                self.hud.selected_idx = len(self.history) - 1

                # If after this move only kings remain -> draw by insufficient material
                try:
                    if self._only_kings_left():
                        # set a special winner marker and end the game; loop_playing will display proper message
                        self.chess.winner = "InsufficientMaterial"
                        self.end_message = "Draw by insufficient material!"
                except Exception:
                    pass
        except Exception:
            traceback.print_exc()
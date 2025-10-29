# chess.py
import pygame
from pygame.locals import *
import random
import copy
import sys
import time

from piece import Piece
from utils import Utils


class Chess(object):
    def __init__(self, screen, pieces_src, square_coords, square_length):
        # display surface / board geometry
        self.screen = screen
        self.board_locations = square_coords
        self.square_length = square_length

        # piece renderer (uses same mapping as HUD)
        self.chess_pieces = Piece(pieces_src, cols=6, rows=2)

        # turn tracking: 1 indicates that side to move
        self.turn = {"black": 0, "white": 1}

        # move list / selection / UI helper
        self.moves = []
        self.utils = Utils()

        # tracked state
        self.captured = []            # list of piece_name strings e.g. "white_queen"
        self.winner = ""
        self.has_moved = {}           # map like "e1": bool
        self.last_move = None         # ((sx,sy),(dx,dy), piece_name)
        self.position_counts = {}     # for threefold repetition: key -> count

        # AI support
        self.ai_auto_promote = False

        # initialize board
        self.reset()

    # -------------------- Helpers --------------------

    @staticmethod
    def xy_to_square(x, y):
        """x,y board coords (0..7, 0..7 top=0) -> ('a'..'h', 1..8)"""
        return chr(97 + x), 8 - y

    @staticmethod
    def square_to_xy(file_char, row_no):
        """('a'..'h', 1..8) -> x,y board coords"""
        return ord(file_char) - 97, 8 - row_no

    # -------------------- Initialization / reset --------------------

    def reset(self):
        """Reset board to starting position and clear state counters."""
        self.moves = []
        self.turn = {"black": 0, "white": 1}  # white starts
        self.winner = ""
        self.captured = []
        self.has_moved = {}
        self.last_move = None
        self.position_counts = {}

        # two dimensional dictionary containing details about each board location
        self.piece_location = {}
        x = 0
        for i in range(97, 105):  # a to h
            a = 8
            y = 0
            self.piece_location[chr(i)] = {}
            while a > 0:
                # each square: [piece_name_or_empty, selected_flag, (x,y)boardcoords]
                self.piece_location[chr(i)][a] = ["", False, [x, y]]
                a -= 1
                y += 1
            x += 1

        # set pieces
        order = ["rook", "knight", "bishop", "queen", "king", "bishop", "knight", "rook"]
        for i, file in enumerate("abcdefgh"):
            self.piece_location[file][8][0] = "black_" + order[i]
            self.piece_location[file][7][0] = "black_pawn"
            self.piece_location[file][1][0] = "white_" + order[i]
            self.piece_location[file][2][0] = "white_pawn"

        # mark rooks/kings as not moved if present on starting squares
        for file in "abcdefgh":
            for r in (1, 8):
                p = self.piece_location[file][r][0]
                if p:
                    self.has_moved[file + str(r)] = False

        # initial position count
        key = self.get_position_key()
        self.position_counts[key] = self.position_counts.get(key, 0) + 1

    # -------------------- Main loop helpers --------------------

    def play_turn(self):
        """Draw turn label and allow a move to be attempted."""
        font = pygame.font.SysFont("comicsansms", 20)
        turn_color = "Black" if self.turn["black"] else "White"
        txt = font.render(f"Turn: {turn_color}", True, (255, 255, 255))
        # caller is responsible for placing this; we draw centre-top
        self.screen.blit(txt, ((self.screen.get_width() - txt.get_width()) // 2, 10))
        # handle selection/move input for the side to move
        cur = "black" if self.turn["black"] else "white"
        self.move_piece(cur)

    def draw_pieces(self):
        """Draw piece-selection highlights, check indicator, then pieces."""
        # surfaces for selection highlights
        hl_green = (0, 194, 39, 170)
        hl_blue = (28, 21, 212, 170)

        s_sel_black = pygame.Surface((self.square_length, self.square_length), pygame.SRCALPHA)
        s_sel_black.fill(hl_green)
        s_sel_white = pygame.Surface((self.square_length, self.square_length), pygame.SRCALPHA)
        s_sel_white.fill(hl_blue)

        # show selection + moves
        for val in self.piece_location.values():
            for value in val.values():
                piece_name = value[0]
                x, y = value[2]
                if value[1] and piece_name:
                    surf = s_sel_black if piece_name.startswith("black") else s_sel_white
                    self.screen.blit(surf, self.board_locations[x][y])
                    for mx, my in self.moves:
                        if 0 <= mx < 8 and 0 <= my < 8:
                            self.screen.blit(surf, self.board_locations[mx][my])

        # king in-check highlight
        def draw_red_circle_at(x, y):
            circ = pygame.Surface((self.square_length, self.square_length), pygame.SRCALPHA)
            center = (self.square_length // 2, self.square_length // 2)
            radius = max(8, self.square_length // 2 - 4)
            pygame.draw.circle(circ, (255, 0, 0, 180), center, radius, 0)
            self.screen.blit(circ, self.board_locations[x][y])

        for color in ["white", "black"]:
            if self.is_in_check(color):
                kpos = self.find_king(color)
                if kpos:
                    draw_red_circle_at(*kpos)

        # draw all pieces
        for val in self.piece_location.values():
            for value in val.values():
                piece_name = value[0]
                x, y = value[2]
                if piece_name:
                    self.chess_pieces.draw(self.screen, piece_name, self.board_locations[x][y])

    # -------------------- Input / move flow --------------------

    def get_selected_square(self):
        """Return [piece_name, file_char, row_no] for clicked board square, or None."""
        left_click = self.utils.left_click_event()
        if not left_click:
            return None

        mouse_event = self.utils.get_mouse_event()
        for i in range(len(self.board_locations)):
            for j in range(len(self.board_locations[i])):
                rect = pygame.Rect(
                    self.board_locations[i][j][0],
                    self.board_locations[i][j][1],
                    self.square_length,
                    self.square_length
                )
                if rect.collidepoint(mouse_event[0], mouse_event[1]):
                    x, y = i, j
                    file_char, row_no = self.xy_to_square(x, y)
                    piece_name = self.piece_location[file_char][row_no][0]
                    return [piece_name, file_char, row_no]
        return None

    def move_piece(self, turn):
        """Handle selection and execution for the side `turn` ('white'/'black')."""
        if self.winner:
            return

        square = self.get_selected_square()
        if not square:
            return

        piece_name, file_char, row_no = square
        if not piece_name:
            # attempt to move to an empty square only allowed if it's in moves from a selected piece
            # get x,y of clicked square
            x, y = self.piece_location[file_char][row_no][2]
            if [x, y] in self.moves:
                # find selected source
                selected = None
                for f in self.piece_location:
                    for r in self.piece_location[f]:
                        if self.piece_location[f][r][1]:
                            selected = (f, r)
                            break
                    if selected:
                        break
                if not selected:
                    return
                moved = self.validate_move([x, y], simulate=False, source=selected)
                # update moves selection cleared
                self.moves = []
                # if moved, check end conditions
                if moved:
                    self._after_move_checks(turn)
            return

        # clicked a piece
        x, y = self.piece_location[file_char][row_no][2]
        piece_color = piece_name.split("_", 1)[0]

        if piece_color == turn:
            # select this piece and compute legal moves
            self.moves = self.legal_moves_for(piece_name, [x, y])
            # clear previous selection flags
            for f in self.piece_location:
                for r in self.piece_location[f]:
                    self.piece_location[f][r][1] = False
            self.piece_location[file_char][row_no][1] = True
        else:
            # clicked opponent piece while we might have a selected piece -> try capture move
            if [x, y] in self.moves:
                selected = None
                for f in self.piece_location:
                    for r in self.piece_location[f]:
                        if self.piece_location[f][r][1]:
                            selected = (f, r)
                            break
                    if selected:
                        break
                if not selected:
                    return
                moved = self.validate_move([x, y], simulate=False, source=selected)
                self.moves = []
                if moved:
                    self._after_move_checks(turn)

    def _after_move_checks(self, turn):
        """Common checks after a successful move executed by `turn`."""
        opponent = "white" if turn == "black" else "black"
        # Checkmate
        if self.is_in_check(opponent) and not self.has_legal_moves(opponent):
            self.winner = turn.capitalize()
            return
        # Stalemate
        if (not self.is_in_check(opponent)) and (not self.has_legal_moves(opponent)):
            self.winner = "Stalemate"
            return
        # threefold repetition
        key = self.get_position_key()
        cnt = self.position_counts.get(key, 0)
        if cnt >= 3:
            self.winner = "Threefold"
        # otherwise continue

    # -------------------- Move generation --------------------

    def legal_moves_for(self, piece_name, piece_coord):
        """Pseudo-legal moves filtered by leaving king in check."""
        color = piece_name.split("_")[0]
        pseudo = self.possible_moves(piece_name, piece_coord)
        legal = []
        for dest in pseudo:
            board_backup = copy.deepcopy(self.piece_location)
            moved_backup = copy.deepcopy(self.has_moved)
            last_backup = copy.deepcopy(self.last_move)
            pos_counts_backup = copy.deepcopy(self.position_counts)

            src_file, src_row = self.xy_to_square(*piece_coord)
            ok = self.validate_move(dest, simulate=True, source=(src_file, src_row))
            if ok and not self.is_in_check(color):
                legal.append(dest)

            # restore
            self.piece_location = board_backup
            self.has_moved = moved_backup
            self.last_move = last_backup
            self.position_counts = pos_counts_backup
        return legal

    def has_legal_moves(self, color):
        for f in "abcdefgh":
            for r in range(1, 9):
                p = self.piece_location[f][r][0]
                if p and p.startswith(color):
                    x, y = self.piece_location[f][r][2]
                    if self.legal_moves_for(p, [x, y]):
                        return True
        return False

    def possible_moves(self, piece_name, piece_coord):
        """Generate pseudo-legal moves (may include moves that leave king in check)."""
        positions = []
        if not piece_name:
            return positions

        x, y = piece_coord
        color, kind = piece_name.split("_", 1)

        if kind == "pawn":
            positions = self.pawn_moves(color, (x, y))
        elif kind == "rook":
            positions = self.linear_moves([], piece_name, (x, y))
        elif kind == "bishop":
            positions = self.diagonal_moves([], piece_name, (x, y))
        elif kind == "queen":
            positions = self.linear_moves([], piece_name, (x, y))
            positions = self.diagonal_moves(positions, piece_name, (x, y))
        elif kind == "knight":
            for dx, dy in [(2, 1), (2, -1), (-2, 1), (-2, -1),
                           (1, 2), (1, -2), (-1, 2), (-1, -2)]:
                nx, ny = x + dx, y + dy
                if 0 <= nx < 8 and 0 <= ny < 8:
                    positions.append([nx, ny])
        elif kind == "king":
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < 8 and 0 <= ny < 8:
                        positions.append([nx, ny])
            # castling squares (no self-check filtering inside)
            positions += self.castling_moves(color, (x, y))

        # remove friendly-occupied squares
        legal = []
        for nx, ny in positions:
            f, r = self.xy_to_square(nx, ny)
            tgt = self.piece_location[f][r][0]
            if not tgt or tgt.split("_", 1)[0] != color:
                legal.append([nx, ny])
        return legal

    def pawn_moves(self, color, pos):
        x, y = pos
        moves = []
        dir_ = 1 if color == "black" else -1
        start_y = 1 if color == "black" else 6

        # forward one
        ny = y + dir_
        if 0 <= ny < 8:
            f, r = self.xy_to_square(x, ny)
            if self.piece_location[f][r][0] == "":
                moves.append([x, ny])
                # forward two from start
                if y == start_y:
                    ny2 = y + 2 * dir_
                    f2, r2 = self.xy_to_square(x, ny2)
                    if self.piece_location[f2][r2][0] == "":
                        moves.append([x, ny2])

        # diagonal captures
        for dx in (-1, 1):
            nx, ny = x + dx, y + dir_
            if 0 <= nx < 8 and 0 <= ny < 8:
                f, r = self.xy_to_square(nx, ny)
                tgt = self.piece_location[f][r][0]
                if tgt and tgt.split("_", 1)[0] != color:
                    moves.append([nx, ny])

        # en passant (capture the pawn that just advanced two)
        if self.last_move:
            (sx, sy), (dx, dy), last_piece = self.last_move
            if last_piece.endswith("pawn") and abs(sy - dy) == 2 and dy == y:
                if abs(dx - x) == 1:  # adjacent file
                    moves.append([dx, y + dir_])

        return moves

    def castling_moves(self, color, pos):
        x, y = pos
        back_y = 0 if color == "black" else 7
        row_no = 8 if color == "black" else 1
        king_sq = chr(97 + x) + str(row_no)

        # must be on original square and not moved
        if self.has_moved.get(king_sq, True):
            return []

        res = []
        # king-side
        rook_sq = "h" + str(row_no)
        rook_here = self.piece_location["h"][row_no][0] == f"{color}_rook"
        if not self.has_moved.get(rook_sq, True) and rook_here:
            path_clear = (self.piece_location["f"][row_no][0] == "" and
                          self.piece_location["g"][row_no][0] == "")
            if path_clear and (not self.is_square_attacked(color, (5, back_y))) and (not self.is_square_attacked(color, (6, back_y))):
                res.append([6, back_y])

        # queen-side
        rook_sq = "a" + str(row_no)
        rook_here = self.piece_location["a"][row_no][0] == f"{color}_rook"
        if not self.has_moved.get(rook_sq, True) and rook_here:
            path_clear = (self.piece_location["b"][row_no][0] == "" and
                          self.piece_location["c"][row_no][0] == "" and
                          self.piece_location["d"][row_no][0] == "")
            if path_clear and (not self.is_square_attacked(color, (3, back_y))) and (not self.is_square_attacked(color, (2, back_y))):
                res.append([2, back_y])

        return res

    # -------------------- Attacks / check detection --------------------

    def is_in_check(self, color):
        king = self.find_king(color)
        return self.is_square_attacked(color, king) if king else False

    def is_stalemate(self, color):
        if self.is_in_check(color):
            return False
        for f in self.piece_location:
            for r in self.piece_location[f]:
                piece = self.piece_location[f][r][0]
                if piece and piece.startswith(color):
                    moves = self.legal_moves_for(piece, self.piece_location[f][r][2])
                    if moves:
                        return False
        return True

    def is_square_attacked(self, color, square_xy):
        if not square_xy:
            return False
        x, y = square_xy
        opponent = "white" if color == "black" else "black"
        for f in "abcdefgh":
            for r in range(1, 9):
                p = self.piece_location[f][r][0]
                if not p or not p.startswith(opponent):
                    continue
                px, py = self.piece_location[f][r][2]
                for ax, ay in self.attack_squares_for(p, [px, py]):
                    if ax == x and ay == y:
                        return True
        return False

    def attack_squares_for(self, piece_name, piece_coord):
        """Squares a piece attacks (used for check). Castling excluded."""
        x, y = piece_coord
        color, kind = piece_name.split("_", 1)
        res = []

        if kind == "pawn":
            dir_ = 1 if color == "black" else -1
            for dx in (-1, 1):
                nx, ny = x + dx, y + dir_
                if 0 <= nx < 8 and 0 <= ny < 8:
                    res.append([nx, ny])
            return res

        if kind == "knight":
            for dx, dy in [(2, 1), (2, -1), (-2, 1), (-2, -1),
                           (1, 2), (1, -2), (-1, 2), (-1, -2)]:
                nx, ny = x + dx, y + dy
                if 0 <= nx < 8 and 0 <= ny < 8:
                    res.append([nx, ny])
            return res

        if kind == "king":
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    if dx == dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < 8 and 0 <= ny < 8:
                        res.append([nx, ny])
            return res

        # sliding
        if kind in ("rook", "queen"):
            res = self.linear_moves(res, piece_name, (x, y))
        if kind in ("bishop", "queen"):
            res = self.diagonal_moves(res, piece_name, (x, y))
        return res

    def find_king(self, color):
        for f in "abcdefgh":
            for r in range(1, 9):
                if self.piece_location[f][r][0] == color + "_king":
                    return self.piece_location[f][r][2]
        return None

    # -------------------- Move execution --------------------

    def validate_move(self, destination, simulate=False, source=None):
        """
        Execute a move to `destination` (x,y).
        If simulate=True, do not toggle turns or update selection/UI, but do modify board.
        `source` must be (file_char, row_no) when simulating.
        Returns True if move executed (or simulated) successfully, False otherwise.
        """
        dx, dy = destination
        des_file, des_row = self.xy_to_square(dx, dy)

        # find source
        if source is None:
            src_file = src_row = None
            for f in self.piece_location:
                for r in self.piece_location[f]:
                    if self.piece_location[f][r][1]:
                        src_file, src_row = f, r
                        break
                if src_file:
                    break
            if src_file is None:
                return False
        else:
            src_file, src_row = source

        piece_name = self.piece_location[src_file][src_row][0]
        if not piece_name:
            return False

        color, kind = piece_name.split("_", 1)
        sx, sy = self.piece_location[src_file][src_row][2]
        target_piece = self.piece_location[des_file][des_row][0]

        # EN PASSANT capture (if moving diagonally to empty square)
        did_en_passant = False
        if kind == "pawn" and target_piece == "" and dx != sx:
            if self.last_move:
                (lsx, lsy), (ldx, ldy), lpiece = self.last_move
                if lpiece.endswith("pawn") and abs(lsy - ldy) == 2 and ldx == dx and ldy == sy:
                    cap_file, cap_row = self.xy_to_square(dx, sy)
                    captured = self.piece_location[cap_file][cap_row][0]
                    if captured and not captured.startswith(color):
                        if not simulate:
                            # store captured piece name string
                            self.captured.append(captured)
                        self.piece_location[cap_file][cap_row][0] = ""
                        did_en_passant = True

        # CASTLING: move rook too (robust implementation)
        if kind == "king" and sy == dy and abs(dx - sx) == 2:
            # use the source row (src_row) for rook row (works for white and black)
            rook_src_file = "h" if dx > sx else "a"   # king-side -> rook at 'h', queen-side -> 'a'
            rook_dst_file = "f" if dx > sx else "d"   # rook moves to 'f' (g-side) or 'd' (queen-side)
            rook_row = src_row                         # same rank as king source (1 for white, 8 for black)

            # verify rook is present and is correct color
            rook_here = self.piece_location[rook_src_file][rook_row][0]
            if rook_here == f"{color}_rook":
                # move rook
                self.piece_location[rook_dst_file][rook_row][0] = rook_here
                self.piece_location[rook_src_file][rook_row][0] = ""
                # mark rook as having moved
                self.has_moved[rook_src_file + str(rook_row)] = True

        # normal capture bookkeeping
        if target_piece and not did_en_passant and not simulate:
            self.captured.append(target_piece)

        # move piece
        self.piece_location[des_file][des_row][0] = piece_name
        self.piece_location[src_file][src_row][0] = ""
        self.piece_location[src_file][src_row][1] = False

        # mark has_moved for source square (important for castling)
        self.has_moved[src_file + str(src_row)] = True

        # PROMOTION
        if kind == "pawn":
            if (color == "white" and dy == 0) or (color == "black" and dy == 7):
                if not simulate:
                    if getattr(self, "ai_auto_promote", False):
                        self.piece_location[des_file][des_row][0] = f"{color}_queen"
                    else:
                        choice = self.ask_promotion(color)
                        self.piece_location[des_file][des_row][0] = f"{color}_{choice}"
                else:
                    # in simulation, auto-queen
                    self.piece_location[des_file][des_row][0] = f"{color}_queen"

        # update last_move and position counts & toggle turn (only in real move)
        if not simulate:
            self.last_move = ((sx, sy), (dx, dy), piece_name)
            # toggle turn flags
            self.turn["white"], self.turn["black"] = self.turn["black"], self.turn["white"]

            # update threefold position count
            key = self.get_position_key()
            self.position_counts[key] = self.position_counts.get(key, 0) + 1

        return True


    # -------------------- Sliding move helpers --------------------

    def diagonal_moves(self, positions, piece_name, piece_coord):
        x, y = piece_coord
        for dx, dy in [(-1, -1), (1, 1), (-1, 1), (1, -1)]:
            cx, cy = x, y
            while True:
                cx += dx
                cy += dy
                if cx < 0 or cy < 0 or cx > 7 or cy > 7:
                    break
                positions.append([cx, cy])
                f, r = self.xy_to_square(cx, cy)
                p = self.piece_location[f][r][0]
                if p:
                    break
        return positions

    def linear_moves(self, positions, piece_name, piece_coord):
        x, y = piece_coord
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            cx, cy = x, y
            while True:
                cx += dx
                cy += dy
                if cx < 0 or cy < 0 or cx > 7 or cy > 7:
                    break
                positions.append([cx, cy])
                f, r = self.xy_to_square(cx, cy)
                p = self.piece_location[f][r][0]
                if p:
                    break
        return positions

    # -------------------- Promotion UI --------------------

    def ask_promotion(self, color):
        """Modal promotion chooser. Blocks until user clicks an option."""
        options = ["queen", "rook", "bishop", "knight"]
        font = pygame.font.SysFont("Arial", 32, bold=True)
        option_surfaces = []
        # layout vertically centered
        center_x = self.screen.get_width() // 2
        base_y = self.screen.get_height() // 2 - 80
        for i, opt in enumerate(options):
            text = font.render(opt.capitalize(), True, (0, 0, 0), (220, 220, 220))
            rect = text.get_rect(center=(center_x, base_y + i * 56))
            option_surfaces.append((opt, text, rect))

        while True:
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    mx, my = event.pos
                    for opt, surf, rect in option_surfaces:
                        if rect.collidepoint(mx, my):
                            return opt

            # draw simple dialog
            self.screen.fill((240, 240, 240))
            for _, surf, rect in option_surfaces:
                self.screen.blit(surf, rect)
            pygame.display.flip()

    # -------------------- Utility for repetition detection --------------------

    def get_position_key(self):
        """
        Compose a canonical position key that includes:
          - piece placement (a1..h8)
          - side to move (w/b)
          - castling rights (KQkq subset)
          - en-passant target (e.g. 'e3' or '-')
        This is required for correct threefold repetition detection.
        """
        # Build board in file-major order (a1..a8, b1..b8, ...)
        board_parts = []
        for f in "abcdefgh":
            for r in range(1, 9):
                board_parts.append(self.piece_location[f][r][0] or ".")

        turn = "w" if self.turn["white"] else "b"

        # castling rights: check if rooks on starting squares and haven't moved
        rights = []
        if self.piece_location["h"][1][0] == "white_rook" and not self.has_moved.get("h1", True):
            rights.append("K")
        if self.piece_location["a"][1][0] == "white_rook" and not self.has_moved.get("a1", True):
            rights.append("Q")
        if self.piece_location["h"][8][0] == "black_rook" and not self.has_moved.get("h8", True):
            rights.append("k")
        if self.piece_location["a"][8][0] == "black_rook" and not self.has_moved.get("a8", True):
            rights.append("q")
        rights_str = "".join(sorted(rights)) if rights else "-"

        # en-passant target square: if last move was pawn double-step
        ep = "-"
        if self.last_move:
            (sx, sy), (dx, dy), piece = self.last_move
            if piece.endswith("pawn") and abs(sy - dy) == 2:
                mid_y = (sy + dy) // 2
                ep_file, ep_rank = self.xy_to_square(dx, mid_y)
                ep = f"{ep_file}{ep_rank}"

        return "{}_{}_{}_{}".format("".join(board_parts), turn, rights_str, ep)

    # -------------------- Helpers for engine / debugging --------------------

    def get_all_legal_moves(self, color):
        """Return list of ((f,r), (dx,dy)) legal moves for `color`."""
        moves = []
        for f in "abcdefgh":
            for r in range(1, 9):
                p = self.piece_location[f][r][0]
                if p and p.startswith(color):
                    x, y = self.piece_location[f][r][2]
                    legal = self.legal_moves_for(p, [x, y])
                    for dest in legal:
                        moves.append(((f, r), (dest[0], dest[1])))
        return moves

    def ai_move(self):
        """
        Simple AI for the black side. Chooses captures preferentially, otherwise random.
        Returns True if a move was executed.
        """
        if self.winner:
            return False
        if not self.turn["black"]:
            return False

        moves = self.get_all_legal_moves("black")
        if not moves:
            return False

        capture_moves = []
        for src, dest in moves:
            dx, dy = dest
            f, r = self.xy_to_square(dx, dy)
            tgt = self.piece_location[f][r][0]
            if tgt and tgt.startswith("white"):
                capture_moves.append((src, dest))

        chosen = random.choice(capture_moves) if capture_moves else random.choice(moves)
        src, dest = chosen

        # auto promote for AI to avoid blocking UI
        self.ai_auto_promote = True
        moved = self.validate_move(dest, simulate=False, source=src)
        self.ai_auto_promote = False

        # after move checks (rep/stalemate/checkmate)
        if moved:
            # determine which side moved -> black moved
            self._after_move_checks("black")
        return moved

# superchess.py
import copy
import random
import sys
import pygame
from pygame.locals import *
from chess import Chess

class SuperChess(Chess):
    """
    SuperChess subclass implementing:
      - per-side charges (max 3)
      - previewing and activation of superpowers (press S to toggle preview)
      - fortress zones with TTL (no enemy may move into those squares while active)
      - phase shift, shadow jump, royal teleport, dark empress, fortress, sacrifice
      - AI uses superpowers sometimes
    """

    def __init__(self, screen, pieces_src, square_coords, square_length):
        # --- super-state initialised BEFORE calling Chess.__init__ ---
        self.charges = {"white": 0, "black": 0}
        # preview state (used by Game)
        self.previewing = False
        self.power_preview_active = False
        self.preview_moves = []
        self.preview_source = None
        self.preview_selected = None
        self.power_preview_name = None
        self.fortress_zones = []
        self.power_was_used_this_turn = False
        self.king_recently_checked = {"white": False, "black": False}
        self.last_move_meta = None
        # Call base constructor (calls reset)
        super().__init__(screen, pieces_src, square_coords, square_length)

    def reset(self):
        super().reset()
        self.charges = {"white": 0, "black": 0}
        self.previewing = False
        self.power_preview_active = False
        self.preview_moves = []
        self.preview_source = None
        self.preview_selected = None
        self.power_preview_name = None
        self.fortress_zones = []
        self.power_was_used_this_turn = False
        self.king_recently_checked = {"white": False, "black": False}
        self.last_move_meta = None

    # ---------------- Preview helpers used by Game ----------------

    def start_power_preview_for_selected(self,lightning_sound=None):
        """
        Called by Game when user requests to preview a superpower for the currently selected piece.
        Finds the selected piece, checks charges and generates preview moves.
        Ensures preview_source and preview_moves are set for all piece types.
        """
        sel = None
        for f in self.piece_location:
            for r in self.piece_location[f]:
                if self.piece_location[f][r][1]:
                    sel = (f, r)
                    break
            if sel:
                break
        if not sel:
            return
        sf, sr = sel
        pname = self.piece_location[sf][sr][0]
        if not pname:
            return
        color, kind = pname.split("_", 1)
        self.toggle_preview(color)
        # Always set preview_source for highlight logic
        self.preview_source = sel
        # Always set preview_moves for all piece types
        sx, sy = self.piece_location[sf][sr][2]
        self.preview_moves = self.super_moves_for(pname, (sx, sy))
        lightning_sound.play()

    def toggle_preview(self, color):
        """
        Toggle preview on/off for the currently selected piece (must belong to color).
        Builds preview_moves (filtered by simulation so resulting king isn't left in check).
        """
        # If currently previewing -> cancel
        if self.previewing:
            self._clear_preview(full=True)
            # restore legal moves for selected piece if selection still present
            for f in self.piece_location:
                for r in self.piece_location[f]:
                    if self.piece_location[f][r][1]:
                        pname = self.piece_location[f][r][0]
                        if pname and pname.startswith(color):
                            px, py = self.piece_location[f][r][2]
                            self.moves = self.legal_moves_for(pname, [px, py])
                            return
            self.moves = []
            return

        # find selected piece
        sel = None
        for f in self.piece_location:
            for r in self.piece_location[f]:
                if self.piece_location[f][r][1]:
                    sel = (f, r)
                    break
            if sel:
                break
        if not sel:
            return
        sf, sr = sel
        pname = self.piece_location[sf][sr][0]
        if not pname or not pname.startswith(color):
            return

        # require at least one charge
        if self.charges.get(color, 0) <= 0:
            return

        sx, sy = self.piece_location[sf][sr][2]
        # generate raw super moves
        raw = self.super_moves_for(pname, (sx, sy))
        if not raw:
            return

        # filter via simulation (must not leave own king in check)
        board_b = copy.deepcopy(self.piece_location)
        moved_b = copy.deepcopy(self.has_moved)
        last_b = copy.deepcopy(self.last_move)

        legal = []
        for d in raw:
            # apply simulated effect (must not alter persistent state permanently)
            self.apply_super_move_simulate(sf, sr, d)
            if not self.is_in_check(color):
                legal.append(d)
            # restore
            self.piece_location = copy.deepcopy(board_b)
            self.has_moved = copy.deepcopy(moved_b)
            self.last_move = copy.deepcopy(last_b)

        if not legal:
            return

        # set preview state
        self.previewing = True
        self.power_preview_active = True
        self.preview_moves = legal
        self.preview_source = sel
        self.preview_selected = None
        self.moves = legal[:]  # highlights on board

        kind = pname.split("_", 1)[1]
        self.power_preview_name = {
            "king": "royal_teleport",
            "queen": "dark_empress",
            "rook": "fortress_field",
            "bishop": "phase_shift",
            "knight": "shadow_jump",
            "pawn": "sacrifice"
        }.get(kind, None)

    def cancel_power_preview(self):
        """Called by Game to cancel preview (mouse left board / ESC)."""
        self.previewing = False
        self.power_preview_active = False
        # Remove any temporary fortress zones (red highlights)
        if hasattr(self, "fortress_zones"):
            # Only keep fortress zones that were actually committed
            self.fortress_zones = [zone for zone in self.fortress_zones if zone.get("committed", False)]

    def _clear_preview(self, full=False):
        """Clear preview-related flags and optionally remove selection highlights."""
        self.preview_moves = []
        self.preview_source = None
        self.preview_selected = None
        self.power_preview_name = None
        self.power_preview_active = False
        if full:
            self.previewing = False
            self.moves = []

    # ---------------- Super-move generation ----------------

    def super_moves_for(self, piece_name, piece_coord):
        """
        Return raw list of [x,y] targets representing the superpower landing squares for preview.
        Each piece's superpower preview matches its actual ability:
        - King: can swap with any allied piece (except itself)
        - Queen: knight-like jumps (cannot land on friendly)
        - Rook: fortress activation (own square only)
        - Bishop: phase shift (any diagonal, ignoring friendly blockers, with king+shield redirect)
        - Knight: jump to any square in 3x3 around (cannot land on friendly)
        - Pawn: sacrifice (own square only)
        """
        color, kind = piece_name.split("_", 1)
        x, y = piece_coord
        res = []

        if kind == "king":
            for f in "abcdefgh":
                for r in range(1, 9):
                    p = self.piece_location[f][r][0]
                    if p and p.startswith(color) and p != piece_name:
                        tx, ty = self.piece_location[f][r][2]
                        res.append([tx, ty])

        elif kind == "queen":
            for dx, dy in [(2,1),(2,-1),(-2,1),(-2,-1),(1,2),(1,-2),(-1,2),(-1,-2)]:
                nx, ny = x+dx, y+dy
                if 0 <= nx < 8 and 0 <= ny < 8:
                    df, dr = self.xy_to_square(nx, ny)
                    occ = self.piece_location[df][dr][0]
                    if occ and occ.startswith(color):
                        continue
                    res.append([nx, ny])

        elif kind == "rook":
            res.append([x, y])

        elif kind == "bishop":
            for dx, dy in [(-1,-1),(1,1),(-1,1),(1,-1)]:
                cx, cy = x, y
                while True:
                    cx += dx; cy += dy
                    if cx < 0 or cy < 0 or cx > 7 or cy > 7:
                        break
                    df, dr = self.xy_to_square(cx, cy)
                    occupant = self.piece_location[df][dr][0]
                    if occupant and occupant.endswith("king") and not occupant.startswith(color):
                        sx_shield = cx - dx
                        sy_shield = cy - dy
                        if 0 <= sx_shield < 8 and 0 <= sy_shield < 8:
                            sf_shield, sr_shield = self.xy_to_square(sx_shield, sy_shield)
                            shield_piece = self.piece_location[sf_shield][sr_shield][0]
                            king_color = occupant.split("_",1)[0]
                            if shield_piece and shield_piece.startswith(king_color):
                                res.append([sx_shield, sy_shield])
                                continue
                        res.append([cx, cy])
                        continue
                    res.append([cx, cy])

        elif kind == "knight":
            for nx in range(x-1, x+2):
                for ny in range(y-1, y+2):
                    if 0 <= nx < 8 and 0 <= ny < 8 and not (nx==x and ny==y):
                        df, dr = self.xy_to_square(nx, ny)
                        occ = self.piece_location[df][dr][0]
                        if occ and occ.startswith(color):
                            continue
                        res.append([nx, ny])

        elif kind == "pawn":
            res.append([x, y])

        return res

    # ---------------- Apply super move (simulation or real) ----------------

    def apply_super_move_simulate(self, src_file, src_row, dest):
        """
        Mutates self.piece_location to apply the super effect (used for simulation).
        It mirrors side-effects of real activation but does not toggle turns or alter charges.
        Returns True if applied.
        """
        piece_name = self.piece_location[src_file][src_row][0]
        if not piece_name:
            return False
        color, kind = piece_name.split("_", 1)
        sx, sy = self.piece_location[src_file][src_row][2]
        dx, dy = dest

        def set_piece_at_coords(px, py, name):
            f, r = self.xy_to_square(px, py)
            self.piece_location[f][r][0] = name

        def get_piece_at_coords(px, py):
            f, r = self.xy_to_square(px, py)
            return self.piece_location[f][r][0]

        # KING: swap with allied piece at dest
        if kind == "king":
            tgt = get_piece_at_coords(dx, dy)
            if tgt and tgt.startswith(color) and tgt != piece_name:
                set_piece_at_coords(dx, dy, piece_name)
                # put the other piece on the king's square
                self.piece_location[src_file][src_row][0] = tgt
                return True
            return False

        # ROOK: fortress zone centered on rook pos (rook does not move)
        if kind == "rook":
            zone = []
            for ox in (-1,0,1):
                for oy in (-1,0,1):
                    nx, ny = sx+ox, sy+oy
                    if 0 <= nx < 8 and 0 <= ny < 8:
                        zone.append((nx, ny))
            # for simulation we just record zone (caller should restore)
            self.fortress_zones.append({'owner': color, 'squares': zone, 'ttl': 2})
            return True

        # BISHOP: phase shift move (ignores friendly blocking) - move bishop
        if kind == "bishop":
            # Prevent landing on friendly piece in simulation
            tgt = get_piece_at_coords(dx, dy)
            if tgt and tgt.startswith(color):
                return False
            set_piece_at_coords(dx, dy, piece_name)
            self.piece_location[src_file][src_row][0] = ""
            return True

        # QUEEN/KNIGHT: special movement to dest (capture allowed), but do not allow landing on friendly
        if kind in ("queen", "knight"):
            tgt = get_piece_at_coords(dx, dy)
            if tgt and tgt.startswith(color):
                return False
            set_piece_at_coords(dx, dy, piece_name)
            self.piece_location[src_file][src_row][0] = ""
            return True

        # PAWN: sacrifice simulation: remove pawn and capture left/right if enemy
        if kind == "pawn":
            self.piece_location[src_file][src_row][0] = ""
            for ox in (-1, 1):
                nx = sx + ox
                ny = sy
                if 0 <= nx < 8 and 0 <= ny < 8:
                    f, r = self.xy_to_square(nx, ny)
                    tgt = self.piece_location[f][r][0]
                    if tgt and not tgt.startswith(color):
                        # record captured piece name in captured list (simulation)
                        self.captured.append(tgt)
                        self.piece_location[f][r][0] = ""
            return True

        return False

    # ---------------- small helper: check activation validity without mutating ----------------

    def _can_activate_power(self, src_file, src_row, pname, dx=None, dy=None):
        """
        Return True if activating `pname` from src_file,src_row to (dx,dy) would succeed.
        This performs only checks (no state changes).
        dx/dy may be None for powers that don't need a target (sacrifice uses source).
        """
        piece_name = self.piece_location[src_file][src_row][0]
        if not piece_name:
            return False
        color = piece_name.split("_", 1)[0]

        # For powers that land on a square, check friendly occupancy rules
        if pname == "dark_empress":
            # queen moves like a knight: cannot land on friendly piece
            if dx is None or dy is None:
                return False
            df, dr = self.xy_to_square(dx, dy)
            tgt = self.piece_location[df][dr][0]
            if tgt and tgt.startswith(color):
                return False
            return True

        if pname == "phase_shift":
            # bishop: cannot land on friendly piece. Special king+shield is allowed since shield belongs to king's color (opponent).
            if dx is None or dy is None:
                return False
            df, dr = self.xy_to_square(dx, dy)
            tgt = self.piece_location[df][dr][0]
            if tgt and tgt.startswith(color):
                return False
            return True

        if pname == "shadow_jump":
            # knight: already filtered in preview generation, but double-check
            if dx is None or dy is None:
                return False
            df, dr = self.xy_to_square(dx, dy)
            tgt = self.piece_location[df][dr][0]
            if tgt and tgt.startswith(color):
                return False
            return True

        if pname == "royal_teleport":
            # king swap: must swap with allied piece (not itself)
            if dx is None or dy is None:
                return False
            df, dr = self.xy_to_square(dx, dy)
            tgt = self.piece_location[df][dr][0]
            if not tgt:
                return False
            if not tgt.startswith(color):
                return False
            # cannot swap with itself (same coords)
            sx, sy = self.piece_location[src_file][src_row][2]
            if sx == dx and sy == dy:
                return False
            return True

        if pname == "fortress_field":
            # always allowed (rook's own square or center), no occupancy checks needed
            return True

        if pname == "sacrifice":
            # sacrifice always valid as preview target is pawn's own square
            return True

        # default conservative: require dx/dy and not landing on friendly
        if dx is None or dy is None:
            return False
        df, dr = self.xy_to_square(dx, dy)
        tgt = self.piece_location[df][dr][0]
        if tgt and tgt.startswith(color):
            return False
        return True

    # ---------------- Override validate_move to integrate preview activation & charge awarding ----------------

    def validate_move(self, destination, simulate=False, source=None):
        """
        Handles:
         - normal moves (delegates to Chess.validate_move) and awards charges for captures
         - fortress TTL expiration after real moves
         - when previewing: if destination is one of preview_moves, verify activation validity,
           consume a charge only after verifying, and apply the super move
         - track recent checks to disallow castling if king was put in check by opponent
        """
        # If previewing => handled later in preview branch
        if self.previewing and self.power_preview_active:
            # determine source (either preview_source or currently selected)
            if self.preview_source is None:
                sel = None
                for f in self.piece_location:
                    for r in self.piece_location[f]:
                        if self.piece_location[f][r][1]:
                            sel = (f, r)
                            break
                    if sel:
                        break
                if not sel:
                    self._clear_preview(full=True)
                    return False
                sf, sr = sel
            else:
                sf, sr = self.preview_source

            # Accept either list or tuple membership in preview_moves
            bx, by = destination if isinstance(destination, (list,tuple)) else (destination[0], destination[1])
            if [bx, by] not in self.preview_moves and (bx, by) not in self.preview_moves:
                self._clear_preview(full=True)
                return False

            piece_name = self.piece_location[sf][sr][0]
            if not piece_name:
                self._clear_preview(full=True)
                return False
            color = piece_name.split("_", 1)[0]
            if self.charges.get(color, 0) <= 0:
                self._clear_preview(full=True)
                return False

            # BEFORE consuming charge: check whether activation would be valid
            pname = self.power_preview_name
            if not self._can_activate_power(sf, sr, pname, bx, by):
                # invalid activation (e.g. queen clicked friendly square) -> cancel preview, do not consume
                self._clear_preview(full=True)
                return False

            # consume one charge (only after validation)
            if not self._consume_charge(color):
                self._clear_preview(full=True)
                return False

            # apply the power concretely
            applied = False
            dx, dy = bx, by

            if pname == "royal_teleport":
                applied = self._activate_royal_teleport(sf, sr, dx, dy)
            elif pname == "dark_empress":
                applied = self._activate_dark_empress(sf, sr, dx, dy)
            elif pname == "fortress_field":
                applied = self._activate_fortress_field(sf, sr, dx, dy)
            elif pname == "phase_shift":
                applied = self._activate_phase_shift(sf, sr, dx, dy)
            elif pname == "shadow_jump":
                applied = self._activate_shadow_jump(sf, sr, dx, dy)
            elif pname == "sacrifice":
                applied = self._activate_sacrifice(sf, sr)
            else:
                applied = False

            # if applied, mark used, expire fortress TTLs (consistent with real moves), clear preview
            if applied:
                self.power_was_used_this_turn = True
                # update king check tracking after the activation (activation toggles turn)
                self._update_king_recently_checked()
                self.expire_fortress_zones()
                # clear preview and selection highlights
                self._clear_preview(full=True)
                # ensure no stale selection/moves remain
                for f in self.piece_location:
                    for r in self.piece_location[f]:
                        self.piece_location[f][r][1] = False
                self.moves = []
                return True

            # if not applied (unexpected), clear preview and fail
            self._clear_preview(full=True)
            return False

        # If not previewing => normal move path
        if not self.previewing:
            # determine mover color (the side that currently has the turn)
            mover = "white" if self.turn.get("white") else "black"

            # Determine source square (use provided source if present, else find selected)
            src = None
            if source:
                # source may be (file,row)
                src = source
            else:
                for f in self.piece_location:
                    for r in self.piece_location[f]:
                        if self.piece_location[f][r][1]:
                            src = (f, r)
                            break
                    if src:
                        break

            # If the mover was recently checked, disallow castling attempt
            if src:
                piece = self.piece_location[src[0]][src[1]][0]
                if piece and piece.endswith("king") and self.king_recently_checked.get(mover, False):
                    # attempt to castle is a king moving two squares horizontally
                    # get source coords and dest coords
                    sx, sy = self.square_to_xy(src[0], src[1])
                    dx, dy = destination
                    try:
                        # destination may be list or tuple
                        dx = int(dx); dy = int(dy)
                    except Exception:
                        pass
                    if abs(dx - sx) == 2 and dy == sy:
                        # block castling while recently checked
                        return False

            # normal delegate to base validate_move
            before_captured = len(self.captured)
            # keep piece_name for meta
            piece_name = None
            if src:
                piece_name = self.piece_location[src[0]][src[1]][0]
            ok = super().validate_move(destination, simulate=simulate, source=source)
            if ok and (not simulate):
                # award charge if a capture happened
                after_captured = len(self.captured)
                newly_captured = []
                if after_captured > before_captured:
                    newly_captured = self.captured[before_captured:after_captured]
                    mover_side = "white" if self.turn["black"] else "black"
                    if self.charges.get(mover_side, 0) < 3:
                        self.charges[mover_side] = min(3, self.charges.get(mover_side, 0) + 1)

                # expire fortress zones TTL on every real half-move
                self.expire_fortress_zones()

                # update king_recently_checked flags after the move
                self._update_king_recently_checked()

                # record last_move_meta for a normal move
                # destination normalized to tuple
                try:
                    dx, dy = int(destination[0]), int(destination[1])
                except Exception:
                    dx = dy = None
                self.last_move_meta = {
                    'type': 'move',
                    'src': (src[0], src[1]) if src else None,
                    'dst': (dx, dy),
                    'piece': piece_name,
                    'captured': newly_captured,
                    'consumed_charge': False
                }

            return ok

        # fallback
        return super().validate_move(destination, simulate=simulate, source=source)

    def _update_king_recently_checked(self):
        """
        After a real move/power activation, update the king_recently_checked flags.
        A side whose king is currently in check will have the flag True.
        """
        for color in ("white", "black"):
            try:
                self.king_recently_checked[color] = bool(self.is_in_check(color))
            except Exception:
                # if any failure, be conservative and set False
                self.king_recently_checked[color] = False

    # ---------------- legal move filtering to respect fortress zones ----------------

    def legal_moves_for(self, piece_name, piece_coord):
        base = super().legal_moves_for(piece_name, piece_coord)
        color = piece_name.split("_")[0]
        # Block moves into fortress zones owned by the opponent
        if self.fortress_zones:
            filtered = []
            for dest in base:
                dx, dy = dest
                blocked = False
                for zone in self.fortress_zones:
                    if zone['owner'] != color and (dx, dy) in zone['squares']:
                        blocked = True
                        break
                if not blocked:
                    filtered.append(dest)
            base = filtered

        # Additionally, if piece is king and our king_recently_checked[color] is True, remove castling moves
        kind = piece_name.split("_", 1)[1]
        if kind == "king" and self.king_recently_checked.get(color, False):
            no_castle = []
            for dest in base:
                # castling encoded as king moving two files horizontally — remove those
                sx, sy = piece_coord
                dx, dy = dest
                if abs(dx - sx) == 2 and dy == sy:
                    continue
                no_castle.append(dest)
            return no_castle

        return base

    # ---------------- AI: try to use power sometimes ----------------

    def ai_move(self):
        """
        AI for Black: randomly try to use a superpower (if charges available). If no power used,
        fall back to base AI (random legal move with capture preference).
        """
        if self.winner:
            return False
        # move only when black's turn in this convention
        if not self.turn["black"]:
            return False

        # attempt superpower sometimes
        if self.charges.get("black", 0) > 0 and random.random() < 0.35:
            # scan black pieces and try to find a legal super activation
            for f in "abcdefgh":
                for r in range(1, 9):
                    p = self.piece_location[f][r][0]
                    if not p or not p.startswith("black"):
                        continue
                    x, y = self.piece_location[f][r][2]
                    raw = self.super_moves_for(p, (x, y))
                    if not raw:
                        continue
                    board_b = copy.deepcopy(self.piece_location)
                    moved_b = copy.deepcopy(self.has_moved)
                    last_b = copy.deepcopy(self.last_move)
                    legal = []
                    for d in raw:
                        self.apply_super_move_simulate(f, r, d)
                        if not self.is_in_check("black"):
                            legal.append(d)
                        # restore
                        self.piece_location = copy.deepcopy(board_b)
                        self.has_moved = copy.deepcopy(moved_b)
                        self.last_move = copy.deepcopy(last_b)
                    if legal:
                        chosen = random.choice(legal)
                        # set previewing state so validate_move path consumes charge / commits
                        self.previewing = True
                        self.power_preview_active = True
                        self.preview_moves = legal
                        self.preview_source = (f, r)
                        kind = p.split("_",1)[1]
                        self.power_preview_name = {
                            "king": "royal_teleport",
                            "queen": "dark_empress",
                            "rook": "fortress_field",
                            "bishop": "phase_shift",
                            "knight": "shadow_jump",
                            "pawn": "sacrifice"
                        }.get(kind, None)
                        # commit via validate_move
                        self.validate_move(chosen, simulate=False, source=(f, r))
                        return True

        # otherwise fallback to base AI
        return super().ai_move()

    # ---------------- Commit power preview ----------------

    def commit_power_preview(self, board_xy):
        """
        Called by Game when the user clicks a preview square (or AI/hotkey).
        board_xy is (x,y) indices (0..7).
        Returns True if activation succeeded.
        """
        if not self.previewing:
            return False

        bx, by = board_xy
        # Accept either list or tuple representation in preview_moves
        if [bx, by] not in self.preview_moves and (bx, by) not in self.preview_moves:
            return False

        # source known in preview_source, else find it
        source = self.preview_source
        if source is None:
            sel = None
            for f in self.piece_location:
                for r in self.piece_location[f]:
                    if self.piece_location[f][r][1]:
                        sel = (f, r)
                        break
                if sel:
                    break
            if not sel:
                self._clear_preview(full=True)
                return False
            source = sel

        return self.validate_move([bx, by], simulate=False, source=source)

    # ---------------- Individual power activations (real; should toggle turn / update captured as needed) ----------------

    def _consume_charge(self, color):
        if getattr(self, "charges", None) is None:
            return False
        if self.charges.get(color, 0) <= 0:
            return False
        self.charges[color] -= 1
        return True

    def _apply_move_without_checks(self, src_file, src_row, dst_x, dst_y, simulate=False):
        """
        Move piece from src to dst (dst given as board x,y). Handles captures and promotion,
        sets last_move and toggles turn (unless simulate=True). Returns True if moved.
        Also sets a conservative last_move_meta for the motion.
        """
        dst_file, dst_row = self.xy_to_square(dst_x, dst_y)
        piece_name = self.piece_location[src_file][src_row][0]
        if not piece_name:
            return False

        # capture bookkeeping
        before = len(self.captured)
        tgt = self.piece_location[dst_file][dst_row][0]
        if tgt and not simulate:
            self.captured.append(tgt)

        # move piece
        self.piece_location[dst_file][dst_row][0] = piece_name
        self.piece_location[src_file][src_row][0] = ""
        self.piece_location[src_file][src_row][1] = False
        self.has_moved[src_file + str(src_row)] = True

        # promotion auto-queen for simplicity
        color, kind = piece_name.split("_", 1)
        if kind == "pawn":
            if (color == "white" and dst_y == 0) or (color == "black" and dst_y == 7):
                self.piece_location[dst_file][dst_row][0] = f"{color}_queen"

        # set last_move and toggle turn if real
        sx, sy = self.square_to_xy(src_file, src_row)
        if not simulate:
            self.last_move = ((sx, sy), (dst_x, dst_y), piece_name)
            # toggle whose turn it is
            self.turn["white"], self.turn["black"] = self.turn["black"], self.turn["white"]

            # populate a conservative last_move_meta (activations can overwrite with richer meta)
            after = len(self.captured)
            newly_captured = self.captured[before:after] if after > before else []
            self.last_move_meta = {
                'type': 'move',
                'src': (src_file, src_row),
                'dst': (dst_x, dst_y),
                'piece': piece_name,
                'captured': newly_captured,
                'consumed_charge': False
            }

        return True

    def _activate_royal_teleport(self, src_file, src_row, dst_x, dst_y):
        """
        King: swap with allied piece at dest. Must remain legal (we assume preview done).
        Swap and toggle turn; consume charge.
        """
        src_piece = self.piece_location[src_file][src_row][0]
        if not src_piece or not src_piece.endswith("king"):
            return False
        dst_file, dst_row = self.xy_to_square(dst_x, dst_y)
        target_piece = self.piece_location[dst_file][dst_row][0]
        if not target_piece or not target_piece.startswith(src_piece.split("_",1)[0]):
            return False
        # perform swap
        self.piece_location[dst_file][dst_row][0], self.piece_location[src_file][src_row][0] = (
            self.piece_location[src_file][src_row][0], self.piece_location[dst_file][dst_row][0]
        )
        self.piece_location[src_file][src_row][1] = False
        self.piece_location[dst_file][dst_row][1] = False
        self.has_moved[src_file + str(src_row)] = True
        self.has_moved[dst_file + str(dst_row)] = True
        sx, sy = self.square_to_xy(src_file, src_row)
        self.last_move = ((sx, sy), (dst_x, dst_y), src_piece)
        self.turn["white"], self.turn["black"] = self.turn["black"], self.turn["white"]
        self.last_move_meta = {
            'type': 'royal_teleport',
            'src': (src_file, src_row),
            'dst': (dst_x, dst_y),
            'piece': src_piece,
            'captured': [],
            'consumed_charge': True
        }
        return True

    def _activate_dark_empress(self, src_file, src_row, dst_x, dst_y):
        """
        Queen moves like a knight to dst. Use _apply_move_without_checks to handle capture/turn toggling.
        """
        src_piece = self.piece_location[src_file][src_row][0]
        if not src_piece or not src_piece.endswith("queen"):
            return False
        color = src_piece.split("_")[0]
        # do not land on friendly piece
        dst_file, dst_row = self.xy_to_square(dst_x, dst_y)
        tgt = self.piece_location[dst_file][dst_row][0]
        if tgt and tgt.startswith(color):
            return False

        before = len(self.captured)
        ok = self._apply_move_without_checks(src_file, src_row, dst_x, dst_y)
        if not ok:
            return False
        after = len(self.captured)
        newly = self.captured[before:after] if after > before else []

        # overwrite last_move_meta to include power data
        self.last_move_meta = {
            'type': 'dark_empress',
            'src': (src_file, src_row),
            'dst': (dst_x, dst_y),
            'piece': src_piece,
            'captured': newly,
            'consumed_charge': True
        }
        return True

    def _activate_phase_shift(self, src_file, src_row, dst_x, dst_y):
        """
        Bishop superpower: Phase Shift.
        Move diagonally to any previewed square, ignoring friendly blockers.
        Special rule: if the chosen target corresponds to an opponent king square,
        and the square immediately before the king along the approach diagonal contains
        a friendly piece of that king (a shield), capture the shield instead (bishop lands
        on the shield square). Otherwise capture normally (king square).
        """
        src_piece = self.piece_location[src_file][src_row][0]
        if not src_piece or not src_piece.endswith("bishop"):
            return False
        color = src_piece.split("_")[0]

        # source coords
        sx, sy = self.square_to_xy(src_file, src_row)

        # Normalize destination values
        dx, dy = int(dst_x), int(dst_y)

        # capture bookkeeping
        before = len(self.captured)

        # If destination square contains an opponent king, check for shield redirect
        dst_file, dst_row = self.xy_to_square(dx, dy)
        dst_piece = self.piece_location[dst_file][dst_row][0]
        redirected = False
        redirect_square = None

        if dst_piece and dst_piece.endswith("king") and not dst_piece.startswith(color):
            # compute approach diagonal direction from source to king square
            kx, ky = dx, dy
            ddx = kx - sx
            ddy = ky - sy
            # must be diagonal approach (phase shift only built diagonal moves), but be safe
            sx_sign = 0 if ddx == 0 else (1 if ddx > 0 else -1)
            sy_sign = 0 if ddy == 0 else (1 if ddy > 0 else -1)

            # ensure diagonal (both signs non-zero and abs equal)
            if sx_sign != 0 and sy_sign != 0 and abs(ddx) == abs(ddy):
                shield_x = kx - sx_sign
                shield_y = ky - sy_sign
                if 0 <= shield_x < 8 and 0 <= shield_y < 8:
                    sf_shield, sr_shield = self.xy_to_square(shield_x, shield_y)
                    shield_piece = self.piece_location[sf_shield][sr_shield][0]
                    king_color = dst_piece.split("_", 1)[0]
                    if shield_piece and shield_piece.startswith(king_color):
                        # redirect capture to shield square
                        # remove shield piece, move bishop to shield square
                        self.captured.append(shield_piece)
                        self.piece_location[sf_shield][sr_shield][0] = src_piece
                        # clear source
                        self.piece_location[src_file][src_row][0] = ""
                        self.piece_location[src_file][src_row][1] = False
                        self.has_moved[src_file + str(src_row)] = True
                        # update last_move and toggle turn
                        self.last_move = ((sx, sy), (shield_x, shield_y), src_piece)
                        self.turn["white"], self.turn["black"] = self.turn["black"], self.turn["white"]
                        redirected = True
                        redirect_square = (sf_shield, sr_shield)
                        # populate last_move_meta
                        self.last_move_meta = {
                            'type': 'phase_shift',
                            'src': (src_file, src_row),
                            'dst': (shield_x, shield_y),
                            'piece': src_piece,
                            'captured': [shield_piece],
                            'consumed_charge': True,
                            'redirected': True,
                            'redirect_square': redirect_square
                        }
                        return True

        # If not redirected, proceed with normal bishop landing/capture, but prevent landing on friendly
        if not redirected:
            dst_file, dst_row = self.xy_to_square(dx, dy)
            tgt = self.piece_location[dst_file][dst_row][0]
            if tgt and tgt.startswith(color):
                return False
            if tgt and not tgt.startswith(color):
                self.captured.append(tgt)

            # move bishop
            self.piece_location[dst_file][dst_row][0] = src_piece
            self.piece_location[src_file][src_row][0] = ""
            self.piece_location[src_file][src_row][1] = False
            self.has_moved[src_file + str(src_row)] = True

            # set last move and toggle turn
            self.last_move = ((sx, sy), (dx, dy), src_piece)
            self.turn["white"], self.turn["black"] = self.turn["black"], self.turn["white"]

            after = len(self.captured)
            newly = self.captured[before:after] if after > before else []
            self.last_move_meta = {
                'type': 'phase_shift',
                'src': (src_file, src_row),
                'dst': (dx, dy),
                'piece': src_piece,
                'captured': newly,
                'consumed_charge': True,
                'redirected': False
            }
            return True

        return False

    def _activate_shadow_jump(self, src_file, src_row, dst_x, dst_y):
        """
        Knight variant: jump to any square within 3x3. Perform move.
        Do NOT allow landing on friendly pieces.
        """
        src_piece = self.piece_location[src_file][src_row][0]
        if not src_piece or not src_piece.endswith("knight"):
            return False
        color = src_piece.split("_")[0]

        dst_file, dst_row = self.xy_to_square(dst_x, dst_y)
        tgt = self.piece_location[dst_file][dst_row][0]
        if tgt and tgt.startswith(color):
            return False

        before = len(self.captured)
        ok = self._apply_move_without_checks(src_file, src_row, dst_x, dst_y)
        if not ok:
            return False
        after = len(self.captured)
        newly = self.captured[before:after] if after > before else []
        # overwrite meta
        self.last_move_meta = {
            'type': 'shadow_jump',
            'src': (src_file, src_row),
            'dst': (dst_x, dst_y),
            'piece': src_piece,
            'captured': newly,
            'consumed_charge': True
        }
        return True

    def _activate_fortress_field(self, src_file, src_row, dst_x, dst_y):
        """
        Build a fortress zone (3x3) centered at dst_x,dst_y (or rook square). Enemies cannot enter those squares
        for a short time (we implement as ttl=2 half-moves).
        """
        src_piece = self.piece_location[src_file][src_row][0]
        if not src_piece or not src_piece.endswith("rook"):
            return False
        color = src_piece.split("_")[0]
        cx, cy = dst_x, dst_y

        squares = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                nx, ny = cx + dx, cy + dy
                if 0 <= nx < 8 and 0 <= ny < 8:
                    squares.append((nx, ny))

        # add zone with TTL = 2 half-moves
        self.fortress_zones.append({'owner': color, 'squares': squares, 'ttl': 2})

        # toggling turn (activation counts as move)
        self.turn["white"], self.turn["black"] = self.turn["black"], self.turn["white"]

        # record last_move_meta for fortress activation (no captures)
        self.last_move_meta = {
            'type': 'fortress_field',
            'src': (src_file, src_row),
            'dst': (cx, cy),
            'piece': src_piece,
            'captured': [],
            'consumed_charge': True
        }
        return True

    def _activate_sacrifice(self, src_file, src_row):
        """
        Pawn sacrifice: pawn destroys itself and takes enemy pieces directly left and right of the pawn.
        Remove pawn, capture adjacent enemies, toggle turn (sacrifice counts as a move), even if no enemy is present.
        """
        src_piece = self.piece_location[src_file][src_row][0]
        if not src_piece or not src_piece.endswith("pawn"):
            return False
        color = src_piece.split("_")[0]
        sx, sy = self.piece_location[src_file][src_row][2]
        captured_names = []
        for ox in (-1, 1):
            nx = sx + ox
            ny = sy
            if 0 <= nx < 8 and 0 <= ny < 8:
                f, r = self.xy_to_square(nx, ny)
                tgt = self.piece_location[f][r][0]
                if tgt and not tgt.startswith(color):
                    self.piece_location[f][r][0] = ""  # Remove enemy piece from board
                    self.captured.append(tgt)
                    captured_names.append(tgt)
        # remove pawn itself (always removed)
        self.piece_location[src_file][src_row][0] = ""
        self.turn["white"], self.turn["black"] = self.turn["black"], self.turn["white"]
        self.last_move_meta = {
            'type': 'sacrifice',
            'src': (src_file, src_row),
            'dst': None,
            'piece': src_piece,
            'captured': captured_names,
            'consumed_charge': True
        }
        return True

    # ---------------- Utilities ----------------

    def expire_fortress_zones(self):
        """Decrease TTLs and remove expired fortress zones."""
        if not self.fortress_zones:
            return
        new_zones = []
        for z in self.fortress_zones:
            z['ttl'] -= 1
            if z['ttl'] > 0:
                new_zones.append(z)
        self.fortress_zones = new_zones

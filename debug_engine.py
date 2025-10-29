# debug_engine.py
# Run from your project root (where chess.py and res/ live):
# python debug_engine.py

import os, traceback
import pygame

print("Starting debug_engine.py")
pygame.init()

# create a tiny screen in case engine expects a pygame surface
try:
    screen = pygame.display.set_mode((1,1))
except Exception as e:
    screen = None
    print("Could not create pygame screen:", e)

# try import chess engine
try:
    import chess as engine_mod
    ChessClass = getattr(engine_mod, "Chess", None)
    print("Imported engine module 'chess', Chess class:", ChessClass)
except Exception as e:
    ChessClass = None
    print("Failed importing chess:", e)
    traceback.print_exc()

# helper to produce square_coords similar to Game._calc_board_locations (pixel coords not needed)
square_coords = []
for x in range(8):
    col = []
    for y in range(8):
        # put small sensible pixel coordinates or grid indices
        col.append([x*64, y*64])
    square_coords.append(col)

res_pieces = os.path.join(os.path.dirname(__file__), "res", "pieces.png")
if not os.path.exists(res_pieces):
    print("Warning: pieces.png not found at", res_pieces, "(engine might still load)")

inst = None
tried = []

# try several constructor signatures (the engine used multiple different ones in your repo)
constructors = [
    ("screen, pieces_src, square_coords, square_length", (screen, res_pieces, square_coords, 64)),
    ("pieces_src, square_coords, square_length", (res_pieces, square_coords, 64)),
    ("no-args", ()),
]

for desc, args in constructors:
    if not ChessClass:
        break
    try:
        print(f"\nTrying constructor: {desc} -> args types:", [type(a) for a in args])
        inst = ChessClass(*args)
        print("Constructor OK with:", desc)
        break
    except TypeError as te:
        print("TypeError for constructor", desc, ":", te)
    except Exception as ex:
        print("Exception creating engine with", desc, ":", ex)
        traceback.print_exc()

if not ChessClass:
    print("Chess class not available; aborting.")
    raise SystemExit(1)

if not inst:
    print("Could not instantiate engine with tested signatures. Try starting your game once and copying output instead.")
    raise SystemExit(1)

# Print some engine attributes that are useful
print("\nEngine instance type:", type(inst))
print("Engine dir():", [n for n in dir(inst) if not n.startswith("_")])

# print piece_location sample if it exists
pl = getattr(inst, "piece_location", None)
print("\npiece_location (raw repr, first 2 files):")
try:
    if isinstance(pl, dict):
        keys = list(pl.keys())[:2]
        for k in keys:
            print("  ", k, "->", repr(pl[k])[:400])
    else:
        print("  (not dict) ->", repr(pl)[:400])
except Exception:
    traceback.print_exc()

# helper to try calling a method and show repr
def try_call(obj, name, *args):
    fn = getattr(obj, name, None)
    if not fn:
        print(f"\nMethod {name}() not found.")
        return None
    try:
        print(f"\nCalling {name} with args {args} ...")
        res = fn(*args) if args else fn()
        print("  -> type:", type(res))
        # If it's a large list/dict, show head
        if isinstance(res, (list,tuple,set)):
            print("  -> length:", len(res))
            sample = list(res)[:10]
            for i, item in enumerate(sample):
                print(f"     [{i}] ({type(item)}) -> {repr(item)[:300]}")
        elif isinstance(res, dict):
            keys = list(res.keys())[:10]
            print("  -> dict keys sample:", keys)
            for k in keys:
                print(f"     {k}: {repr(res[k])[:300]}")
        else:
            print("  -> repr:", repr(res)[:400])
        return res
    except Exception as e:
        print(f"  -> raised {type(e).__name__}: {e}")
        traceback.print_exc()
        return None

# Now query moves for a few sample squares (algebraic and tuple)
samples = ["e2", "d2", "a2", "g1", "e7"]
tuple_samples = [(4,6), (3,6), (0,6), (6,7)]
# Try legal_moves_for with algebraic squares first
for s in samples:
    try_call(inst, "legal_moves_for", s)

# Try legal_moves_for with tuple coords
for t in tuple_samples:
    try_call(inst, "legal_moves_for", t)

# Try other common methods
try_call(inst, "moves")
try_call(inst, "get_all_legal_moves")
try_call(inst, "possible_moves")
try_call(inst, "possible_moves_for", (4,6))  # some engines use similar name

print("\nDone — copy the full console output and paste it here (including any 'raised' tracebacks).")

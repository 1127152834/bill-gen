"""Load separately installed private font declarations without embedding them in source."""
import json
from pathlib import Path

_ASSET_PATH = Path(__file__).resolve().parent / "assets" / "declared_fonts.json"
ASSETS = json.loads(_ASSET_PATH.read_text(encoding="utf-8"))
DECLARED_FONTS = ASSETS["fonts"]
ENCODING_OBJ = ASSETS["encoding"]
META_DEFAULTS = {"producer": "BillGen Sample Editor"}

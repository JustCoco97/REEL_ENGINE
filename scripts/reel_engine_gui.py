"""Windows desktop entry point for REEL ENGINE."""

import sys
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ENGINE_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

try:
    from reel_engine.gui.app import main
except ImportError as exc:
    raise SystemExit(
        "REEL ENGINE Desktop richiede PySide6. Installa la dipendenza con "
        "'python -m pip install -r requirements-gui.txt'.\n" + str(exc)
    ) from exc


if __name__ == "__main__":
    raise SystemExit(main())

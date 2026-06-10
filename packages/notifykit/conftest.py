"""모노레포 루트에서 pip install 없이 pytest를 실행해도 notifykit를 임포트할 수 있게 한다."""

import sys
from pathlib import Path

_pkg_root = str(Path(__file__).resolve().parent)
if _pkg_root not in sys.path:
    sys.path.insert(0, _pkg_root)

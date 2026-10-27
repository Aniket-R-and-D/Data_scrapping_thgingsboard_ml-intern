#!/usr/bin/env python
"""scripts/cloud_runner.py
Backward-compatible proxy entrypoint for scripts/cloud/cloud_runner.py.
Ensures Docker container CMD ["python", "scripts/cloud_runner.py", "--daemon"] continues seamlessly.
"""

import runpy
from pathlib import Path

if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "cloud" / "cloud_runner.py"
    runpy.run_path(str(target), run_name="__main__")

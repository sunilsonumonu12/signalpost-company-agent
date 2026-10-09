from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from run import configure_tesseract_path


def main() -> int:
    configure_tesseract_path()
    executable = shutil.which("tesseract")
    if executable is None:
        print("Tesseract is not discoverable on PATH.", file=sys.stderr)
        return 1

    result = subprocess.run(
        [executable, "--list-langs"],
        check=True,
        capture_output=True,
        text=True,
    )
    languages = {line.strip() for line in result.stdout.splitlines()[1:] if line.strip()}
    print(f"Tesseract executable: {executable}")
    print(f"Available languages: {', '.join(sorted(languages))}")
    if "nor" not in languages:
        print("Norwegian language data (nor) is unavailable.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
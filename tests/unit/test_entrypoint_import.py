"""Package import order in pytest must not mask a broken production entrypoint."""

import subprocess
import sys


def test_bot_entrypoint_imports_in_a_fresh_process() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import veloexpress_bot.__main__"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr

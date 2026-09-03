"""Run the Phase 9 P9.7 localhost A2A reviewer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from labs.protocols.phase9_a2a import DEFAULT_ENDPOINT, build_app  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8029)
    parser.add_argument("--work-delay", type=float, default=0.02)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    if not 0 <= args.work_delay <= 1:
        parser.error("--work-delay must be between 0 and 1")

    import uvicorn

    endpoint = DEFAULT_ENDPOINT.rsplit(":", 1)[0] + f":{args.port}/a2a"
    uvicorn.run(
        build_app(endpoint, work_delay_seconds=args.work_delay),
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

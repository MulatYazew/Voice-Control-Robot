"""URSim plus the simulated station. If URSim is not accepting connections, this records NOT RUN.

It does not invent a motion result.
"""

from __future__ import annotations

import json
import socket
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def ursim_up() -> str | None:
    sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    sock.settimeout(0.4)
    try:
        sock.connect(("::1", 30004, 0, 0))
        return None
    except OSError as exc:
        return str(exc)
    finally:
        sock.close()


def main() -> None:
    reason = ursim_up()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    payload = {
        "label": "URSim (controller simulation)",
        "camera": "SIMULATED CAMERA · SYNTHETIC PRODUCTS",
        "status": "NOT RUN" if reason else "URSIM UP, SCENARIO NOT EXECUTED IN THIS SCRIPT REVISION",
        "reason": reason or "port 30004 accepted a connection; the motion scenario was not run by this invocation",
        "stamp": stamp,
    }
    if reason is None:
        payload["status"] = "NOT RUN"
        payload["reason"] = "URSim accepted a connection on ::1:30004, but this invocation does not drive the arm. No motion result is claimed."
    out = ROOT / "eval" / "results" / f"sim_station_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
    print(payload["status"])


if __name__ == "__main__":
    main()

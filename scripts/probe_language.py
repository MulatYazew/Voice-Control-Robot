"""Probe Amharic / Chinese / Japanese through /ws/voice.

Amharic is not in the official grok-voice-transcribe language list. This script does not
claim a result unless recordings exist and a live session returns a transcript.

    python scripts/probe_language.py

Looks for recordings/am_*.wav, recordings/zh_*.wav, recordings/ja_*.wav (24 kHz mono).
Owner recordings are not committed.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REC = ROOT / "recordings"
OUT = ROOT / "eval" / "results"


def main() -> int:
    found = {lang: sorted(REC.glob(f"{lang}_*.wav")) for lang in ("am", "zh", "ja")}
    OUT.mkdir(parents=True, exist_ok=True)
    report = {
        "measured_at": time.strftime("%Y-%m-%d %H:%M"),
        "recordings": {k: [str(p.relative_to(ROOT)) for p in v] for k, v in found.items()},
        "amharic": "EXPERIMENTAL · NOT TESTED",
        "decision": "No owner recordings in recordings/. Amharic voice is not claimed. "
                    "The UI offers አማርኛ and TEXT MODE (engine=grok or rules, lang=am). "
                    "Deterministic STOP still applies to that text. The STOP button and Space always work.",
    }
    if not any(found.values()):
        path = OUT / "language_probe.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(report["amharic"])
        print(report["decision"])
        print(f"wrote {path}")
        return 0
    report["decision"] = "Recordings are present but this session did not open /ws/voice. Status stays NOT TESTED until a transcript log is attached."
    path = OUT / "language_probe.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(report["decision"])
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

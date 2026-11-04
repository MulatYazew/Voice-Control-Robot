#!/usr/bin/env bash
# URSim e-Series in Docker (official image universalrobots/ursim_e-series).
#   scripts/ursim.sh start   — start the simulator (PolyScope GUI: http://localhost:6080/vnc.html)
#   scripts/ursim.sh init    — power on + release brakes (needs Remote Control enabled in URSim settings)
#   scripts/ursim.sh status  — robot mode / safety / remote control via the Dashboard Server
#   scripts/ursim.sh stop    — stop and remove the container
set -euo pipefail
DOCKER=${DOCKER:-$(command -v docker || echo /Applications/Docker.app/Contents/Resources/bin/docker)}
NAME=voce-ursim
IMAGE=${URSIM_IMAGE:-universalrobots/ursim_e-series:latest}
MODEL=${URSIM_MODEL:-UR5}   # simulator model only; the real Arm 3 model is UNKNOWN until verified

dash() { python3 - "$1" "${URSIM_HOST:-::1}" <<'PY'
import socket, sys
s = socket.create_connection((sys.argv[2] if len(sys.argv) > 2 else "::1", 29999), timeout=5); s.recv(1024)
s.sendall((sys.argv[1] + "\n").encode()); print(sys.argv[1], "->", s.recv(1024).decode().strip()); s.close()
PY
}

case "${1:-start}" in
  start)
    if "$DOCKER" ps --format '{{.Names}}' | grep -qx "$NAME"; then echo "URSim already running"; exit 0; fi
    if "$DOCKER" ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
      # restart the existing container: keeps Remote Control / Simulation settings made in PolyScope
      "$DOCKER" start "$NAME" >/dev/null
    else
      "$DOCKER" run -d --name "$NAME" --platform linux/amd64 -e ROBOT_MODEL="$MODEL" \
        -p 5900:5900 -p 6080:6080 -p 29999:29999 -p 30001-30004:30001-30004 "$IMAGE" >/dev/null
      echo "NEW container: in PolyScope enable Remote Control, switch Simulation ON, select Remote (see README §7)"
    fi
    echo "URSim starting ($MODEL). GUI: http://localhost:6080/vnc.html"
    for i in $(seq 1 120); do
      if python3 -c "import socket; socket.create_connection(('::1',29999),timeout=1)" 2>/dev/null; then
        echo "Dashboard server up after ${i}s"; exit 0; fi
      sleep 1
    done
    echo "URSim did not come up in 120 s"; exit 1 ;;
  init)  dash "power on"; sleep 3; dash "brake release"; sleep 3; dash "robotmode"; dash "safetystatus" ;;
  status) dash "robotmode"; dash "safetystatus"; dash "programState"; dash "is in remote control"; dash "PolyscopeVersion" ;;
  stop)  "$DOCKER" rm -f "$NAME" ;;
  *) echo "usage: $0 start|init|status|stop"; exit 2 ;;
esac

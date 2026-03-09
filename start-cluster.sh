#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 2 || $# -gt 4 ]]; then
  echo "Usage: $0 <machine1|machine2|machine3> <M1_IP> [M2_IP] [M3_IP]"
  echo "Examples:"
  echo "  1 laptop : $0 machine1 10.0.0.11"
  echo "  2 laptops: $0 machine1 10.0.0.11 10.0.0.12"
  echo "  3 laptops: $0 machine1 10.0.0.11 10.0.0.12 10.0.0.13"
  exit 1
fi

ROLE="$1"
M1_IP="$2"
M2_IP="${3:-}"
M3_IP="${4:-}"

if [[ -z "$M2_IP" && -n "$M3_IP" ]]; then
  echo "If M3_IP is provided, M2_IP must also be provided."
  exit 1
fi

add_remote_machine_peers() {
  local ip="$1"
  local self_ip="$2"

  if [[ -z "$ip" || "$ip" == "$self_ip" ]]; then
    return
  fi

  REMOTE_PEERS+=("$ip:5001" "$ip:5002" "$ip:5003")
}

case "$ROLE" in
  machine1)
    N1="Mumbai"
    N2="Chennai"
    N3="Bangalore"
    SELF_IP="$M1_IP"
    OTHER_A="$M2_IP"
    OTHER_B="$M3_IP"
    ;;
  machine2)
    if [[ -z "$M2_IP" ]]; then
      echo "machine2 requires M2_IP."
      exit 1
    fi
    N1="Virginia"
    N2="NewYork"
    N3="WashingtonDC"
    SELF_IP="$M2_IP"
    OTHER_A="$M1_IP"
    OTHER_B="$M3_IP"
    ;;
  machine3)
    if [[ -z "$M3_IP" ]]; then
      echo "machine3 requires M3_IP."
      exit 1
    fi
    N1="London"
    N2="Paris"
    N3="Berlin"
    SELF_IP="$M3_IP"
    OTHER_A="$M1_IP"
    OTHER_B="$M2_IP"
    ;;
  *)
    echo "Invalid role: $ROLE"
    echo "Valid roles: machine1, machine2, machine3"
    exit 1
    ;;
esac

REMOTE_PEERS=()
add_remote_machine_peers "$OTHER_A" "$SELF_IP"
add_remote_machine_peers "$OTHER_B" "$SELF_IP"

REMOTE_JOINED=""
if [[ ${#REMOTE_PEERS[@]} -gt 0 ]]; then
  REMOTE_JOINED="$(IFS=,; echo "${REMOTE_PEERS[*]}")"
fi

echo "[INFO] Role=$ROLE"
echo "[INFO] Local nodes: $N1, $N2, $N3"
if [[ -n "$REMOTE_JOINED" ]]; then
  echo "[INFO] Remote peers in PEERS: $REMOTE_JOINED"
else
  echo "[INFO] Remote peers in PEERS: none (single-machine mode)"
fi

docker build -t geo-quorum .
docker network create geo-net >/dev/null 2>&1 || true

docker rm -f "$N1" "$N2" "$N3" >/dev/null 2>&1 || true

if [[ -n "$REMOTE_JOINED" ]]; then
  PEERS_N1="$N2:5000,$N3:5000,$REMOTE_JOINED"
  PEERS_N2="$N1:5000,$N3:5000,$REMOTE_JOINED"
  PEERS_N3="$N1:5000,$N2:5000,$REMOTE_JOINED"
else
  PEERS_N1="$N2:5000,$N3:5000"
  PEERS_N2="$N1:5000,$N3:5000"
  PEERS_N3="$N1:5000,$N2:5000"
fi

docker run -d --name "$N1" --network geo-net -p 5001:5000 \
  -e NODE_NAME="$N1" \
  -e PEERS="$PEERS_N1" \
  geo-quorum

docker run -d --name "$N2" --network geo-net -p 5002:5000 \
  -e NODE_NAME="$N2" \
  -e PEERS="$PEERS_N2" \
  geo-quorum

docker run -d --name "$N3" --network geo-net -p 5003:5000 \
  -e NODE_NAME="$N3" \
  -e PEERS="$PEERS_N3" \
  geo-quorum

echo "[OK] Started $ROLE cluster"
echo "[OK] Local URLs: http://localhost:5001  http://localhost:5002  http://localhost:5003"

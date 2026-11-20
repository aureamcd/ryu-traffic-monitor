#!/bin/bash
# run_docker.sh -- executa o experimento no ambiente Docker usado nas aulas
# (conteineres: ryu-ctrl com Ryu 4.34 e mininet-lab com Mininet 2.3.0, rede 'host').
# Execute no WSL como root, de dentro da pasta projeto_final:  sudo bash run_docker.sh [intervalo_s]
set -e
INTERVAL="${1:-2}"
HERE="$(cd "$(dirname "$0")" && pwd)"

docker start ryu-ctrl mininet-lab >/dev/null
docker restart ryu-ctrl >/dev/null   # a imagem do ryu-ctrl nao possui pkill
docker exec mininet-lab bash -c '
if ! pgrep -x ovs-vswitchd >/dev/null; then
  mkdir -p /var/run/openvswitch /etc/openvswitch
  [ -f /etc/openvswitch/conf.db ] || ovsdb-tool create /etc/openvswitch/conf.db
  ovsdb-server --remote=punix:/var/run/openvswitch/db.sock --pidfile --detach
  ovs-vsctl --no-wait init
  ovs-vswitchd --pidfile --detach
fi
mn -c >/dev/null 2>&1 || true'

for c in ryu-ctrl mininet-lab; do
  docker exec $c rm -rf /tmp/proj; docker exec $c mkdir -p /tmp/proj
  docker cp "$HERE/monitor_traffic.py" $c:/tmp/proj/
  docker cp "$HERE/run_experiment.py"  $c:/tmp/proj/
done

docker exec -d ryu-ctrl bash -c "cd /tmp/proj && MONITOR_INTERVAL=$INTERVAL MONITOR_OUTDIR=/tmp/proj/telemetria \
  ryu-manager --ofp-tcp-listen-port 6653 monitor_traffic.py > ryu_monitor.log 2>&1"
sleep 3
docker exec mininet-lab bash -c "cd /tmp/proj && python3 run_experiment.py --userspace --out /tmp/proj/resultados"
sleep 3

rm -rf "$HERE/dados"; mkdir -p "$HERE/dados"
docker cp ryu-ctrl:/tmp/proj/telemetria/. "$HERE/dados/"
docker cp ryu-ctrl:/tmp/proj/ryu_monitor.log "$HERE/dados/"
docker cp mininet-lab:/tmp/proj/resultados/. "$HERE/dados/"
docker restart ryu-ctrl >/dev/null
echo "Dados salvos em $HERE/dados"

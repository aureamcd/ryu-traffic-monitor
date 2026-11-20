#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_experiment.py -- Experimento automatizado (Mininet + iperf) do Traffic Monitor.

Topologia: single,3 (s1 + h1=10.0.0.1, h2=10.0.0.2, h3=10.0.0.3), OVS em OpenFlow 1.3,
controlador remoto Ryu em 127.0.0.1:6653.

Trafego heterogeneo (conforme enunciado):
  fase 1 (ociosa)  : sem trafego (linha de base)
  fase 2 (ICMP)    : h1 ping h2
  fase 3 (TCP)     : h2 iperf -s ; h1 iperf -c 10.0.0.2 -t 30 -i 2
  fase 4 (TCP+UDP) : h3 iperf -c 10.0.0.2 -u -b 10M -t 30 (simultaneo ao TCP)

Uso (como root):  python3 run_experiment.py [--out DIR] [--tcp-bw 20M] [--udp-bw 10M]
"""
import argparse
import os
import subprocess
import time

from mininet.link import TCLink
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.topo import SingleSwitchTopo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='resultados')
    ap.add_argument('--port', type=int, default=6653)
    ap.add_argument('--duration', type=int, default=30)
    ap.add_argument('--udp-duration', type=int, default=30)
    ap.add_argument('--udp-bw', default='10M')
    ap.add_argument('--tcp-bw', default='',
                    help='limite de banda do cliente TCP; vazio = sem limite')
    ap.add_argument('--link-bw', type=float, default=20.0,
                    help='capacidade dos enlaces em Mbit/s via TCLink (0 = sem limite)')
    ap.add_argument('--idle', type=int, default=12, help='segundos da fase ociosa')
    ap.add_argument('--userspace', action='store_true',
                    help="usa datapath 'user' (ambientes sem modulo kernel do OVS)")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    kw = {'datapath': 'user'} if args.userspace else {}
    link_kw = {'link': TCLink} if args.link_bw > 0 else {}
    topo = SingleSwitchTopo(k=3, **({'lopts': {'bw': args.link_bw}} if args.link_bw > 0 else {}))
    net = Mininet(
        topo=topo, autoSetMacs=True,
        controller=lambda n: RemoteController(n, ip='127.0.0.1', port=args.port),
        switch=lambda n, **k: OVSSwitch(n, protocols='OpenFlow13', **dict(k, **kw)),
        **link_kw)
    net.start()
    h1, h2, h3 = net.get('h1', 'h2', 'h3')
    # O datapath em espaco de usuario do OVS nao recalcula checksums: sem isso o
    # handshake TCP falha. Desabilita offloads nas interfaces dos hosts.
    for h in (h1, h2, h3):
        h.cmd('ethtool -K %s-eth0 tx off rx off sg off tso off gso off gro off'
              % h.name)
    log = open(os.path.join(args.out, 'eventos.log'), 'w')

    def mark(msg):
        line = '%s  %s' % (time.strftime('%Y-%m-%dT%H:%M:%S'), msg)
        print(line, flush=True)
        log.write(line + '\n')
        log.flush()

    try:
        time.sleep(3)
        mark('FASE1 ociosa (%ds)' % args.idle)
        time.sleep(args.idle)

        mark('FASE2 ICMP: h1 ping h2 (10 pacotes)')
        out = h1.cmd('ping -c 10 -i 1 %s' % h2.IP())
        open(os.path.join(args.out, 'ping.txt'), 'w').write(out)

        mark('FASE3 TCP: h2 iperf -s ; h1 iperf -c h2 -t %d -i 2' % args.duration)
        h2.cmd('iperf -s -i 2 > %s/iperf_server_tcp.txt 2>&1 &' % args.out)
        h2.cmd('iperf -s -u -i 2 -p 5002 > %s/iperf_server_udp.txt 2>&1 &' % args.out)
        time.sleep(1)
        bw = ('-b %s' % args.tcp_bw) if args.tcp_bw else ''
        h1.cmd('iperf -c %s -t %d -i 2 %s > %s/iperf_h1_tcp.txt 2>&1 &'
               % (h2.IP(), args.duration, bw, args.out))
        time.sleep(args.duration // 2)

        mark('FASE4 UDP simultaneo: h3 iperf -c h2 -u -b %s -t %d'
             % (args.udp_bw, args.udp_duration))
        h3.cmd('iperf -c %s -p 5002 -u -b %s -t %d -i 2 > %s/iperf_h3_udp.txt 2>&1 &'
               % (h2.IP(), args.udp_bw, args.udp_duration, args.out))
        time.sleep(args.udp_duration + 3)

        mark('FASE5 ociosa final (10s)')
        time.sleep(10)
        flows = subprocess.check_output(
            ['ovs-ofctl', '-O', 'OpenFlow13', 'dump-flows', 's1']).decode()
        open(os.path.join(args.out, 'ovs_flows.txt'), 'w').write(flows)
        mark('FIM')
    finally:
        h2.cmd('pkill iperf')
        net.stop()
        log.close()


if __name__ == '__main__':
    main()

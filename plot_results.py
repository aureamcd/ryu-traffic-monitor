#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plot_results.py -- Gera os graficos de telemetria a partir de dados/ports.csv e dados/flows.csv.

Saidas (pasta figuras/):
  vazao_portas.png    vazao RX por porta do switch s1 (h1=p1, h2=p2, h3=p3) ao longo do tempo
  vazao_fluxos.png    vazao por fluxo (src->dst) medida pelo Ryu (OFPFlowStats)
  pacotes_portas.png  taxa de pacotes por segundo por porta (derivada de rx_pkts)
  comparacao_iperf.png vazao do Ryu vs. relatorio do iperf (validacao)

Uso: python3 plot_results.py [--dados dados] [--out figuras]
"""
import argparse
import csv
import os
import re
from collections import defaultdict
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HOSTS = {'1': 'h1', '2': 'h2', '3': 'h3'}
MAC = {'00:00:00:00:00:01': 'h1', '00:00:00:00:00:02': 'h2', '00:00:00:00:00:03': 'h3'}


def ts(s):
    return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S')


def read(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dados', default='dados')
    ap.add_argument('--out', default='figuras')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    ports = [r for r in read(os.path.join(a.dados, 'ports.csv'))
             if r['port'] in HOSTS]
    flows = read(os.path.join(a.dados, 'flows.csv'))
    t0 = min(ts(r['timestamp']) for r in ports)

    def x(r):
        return (ts(r['timestamp']) - t0).total_seconds()

    # eventos (marcadores de fase)
    marks = []
    ev = os.path.join(a.dados, 'eventos.log')
    if os.path.exists(ev):
        for line in open(ev):
            m = re.match(r'(\S+)\s+(FASE\d)', line)
            if m:
                marks.append(((ts(m.group(1)) - t0).total_seconds(), m.group(2)))

    def decorate(ax, title, ylabel):
        for t, name in marks:
            ax.axvline(t, color='gray', ls=':', lw=1)
            ax.text(t, ax.get_ylim()[1], name, rotation=90, va='top', ha='right', fontsize=7)
        ax.set_title(title); ax.set_xlabel('Tempo (s)'); ax.set_ylabel(ylabel)
        ax.grid(alpha=.3); ax.legend(fontsize=8)

    # 1) vazao por porta (RX no switch = trafego gerado pelo host da porta)
    fig, ax = plt.subplots(figsize=(8, 4))
    for p, h in HOSTS.items():
        rows = [r for r in ports if r['port'] == p][1:]
        ax.plot([x(r) for r in rows], [float(r['rx_bps']) / 1e6 for r in rows],
                marker='o', ms=3, label='porta %s (%s) RX' % (p, h))
    ax.set_yscale('symlog', linthresh=0.1)
    decorate(ax, 'Vazao instantanea por porta do switch s1 (Ryu)', 'Mbit/s (escala symlog)')
    fig.tight_layout(); fig.savefig(os.path.join(a.out, 'vazao_portas.png'), dpi=160); plt.close(fig)

    # 2) vazao por fluxo
    series = defaultdict(list)
    for r in flows:
        if r['eth_src'] in MAC:
            series[(MAC[r['eth_src']], MAC.get(r['eth_dst'], r['eth_dst']))].append(
                ((ts(r['timestamp']) - t0).total_seconds(), float(r['bps']) / 1e6))
    fig, ax = plt.subplots(figsize=(8, 4))
    for (s, d), pts in sorted(series.items()):
        if max(v for _, v in pts) > 0.01:
            ax.plot(*zip(*pts[1:]), marker='o', ms=3, label='%s -> %s' % (s, d))
    ax.set_yscale('symlog', linthresh=0.1)
    decorate(ax, 'Vazao instantanea por fluxo (OFPFlowStatsReply)', 'Mbit/s (escala symlog)')
    fig.tight_layout(); fig.savefig(os.path.join(a.out, 'vazao_fluxos.png'), dpi=160); plt.close(fig)

    # 3) pacotes por segundo por porta
    fig, ax = plt.subplots(figsize=(8, 4))
    for p, h in HOSTS.items():
        rows = [r for r in ports if r['port'] == p]
        xs, ys = [], []
        for a_, b_ in zip(rows, rows[1:]):
            dt = x(b_) - x(a_)
            if dt > 0:
                xs.append(x(b_)); ys.append((int(b_['rx_pkts']) - int(a_['rx_pkts'])) / dt)
        ax.plot(xs, ys, marker='o', ms=3, label='porta %s (%s) RX' % (p, h))
    ax.set_yscale('symlog', linthresh=10)
    decorate(ax, 'Taxa de pacotes recebidos por porta (derivada de rx_packets)', 'pacotes/s (escala symlog)')
    fig.tight_layout(); fig.savefig(os.path.join(a.out, 'pacotes_portas.png'), dpi=160); plt.close(fig)

    # 4) validacao: Ryu (porta 1 RX, h1 -> TCP) x iperf do cliente h1
    iperf = []
    p = os.path.join(a.dados, 'iperf_h1_tcp.txt')
    for line in open(p):
        m = re.search(r'\[\s*\d+\]\s+(\d+\.\d+)-\s*(\d+\.\d+) sec.*?([\d.]+) (M|G|K)bits/sec', line)
        if m and float(m.group(2)) - float(m.group(1)) < 5:
            mult = {'K': 1e-3, 'M': 1, 'G': 1e3}[m.group(4)]
            iperf.append((float(m.group(2)), float(m.group(3)) * mult))
    # alinha o inicio do iperf TCP com o marcador FASE3
    start = next((t for t, n in marks if n == 'FASE3'), 0) + 1
    rows = [r for r in ports if r['port'] == '1'][1:]
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.plot([x(r) for r in rows], [float(r['rx_bps']) / 1e6 for r in rows],
            marker='o', ms=3, label='Ryu: porta 1 RX')
    if iperf:
        ax.plot([start + t for t, _ in iperf], [v for _, v in iperf], marker='s', ms=3,
                label='iperf cliente h1 (TCP)')
    ax.set_yscale('symlog', linthresh=0.1)
    decorate(ax, 'Validacao: vazao medida pelo Ryu x relatorio do iperf', 'Mbit/s (escala symlog)')
    fig.tight_layout(); fig.savefig(os.path.join(a.out, 'comparacao_iperf.png'), dpi=160); plt.close(fig)
    print('Figuras salvas em', a.out)


if __name__ == '__main__':
    main()

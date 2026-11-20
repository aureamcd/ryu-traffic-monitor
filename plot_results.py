#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plot_results.py -- Gera os graficos de telemetria (PNG e PDF vetorial) com fontes ampliadas
e sem titulos internos, ideais para insercao em artigos SBC.
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
IP_TO_HOST = {'10.0.0.1': 'h1', '10.0.0.2': 'h2', '10.0.0.3': 'h3'}
PROTO = {'1': 'ICMP', '6': 'TCP', '17': 'UDP'}


def ts(s):
    return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S')


def read(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def parse_iperf(path):
    pts = []
    if not os.path.exists(path):
        return pts
    for line in open(path):
        m = re.search(r'\[\s*\d+\]\s+(\d+\.\d+)-\s*(\d+\.\d+) sec.*?([\d.]+)\s+(K|M|G)bits/sec', line)
        if m and float(m.group(2)) - float(m.group(1)) < 5:
            mult = {'K': 1e-3, 'M': 1.0, 'G': 1e3}[m.group(4)]
            pts.append((float(m.group(2)), float(m.group(3)) * mult))
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dados', default='dados')
    ap.add_argument('--out', default='figuras')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    plt.rcParams.update({
        'font.size': 13,
        'axes.labelsize': 15,
        'xtick.labelsize': 13,
        'ytick.labelsize': 13,
        'legend.fontsize': 12,
        'axes.linewidth': 1.1,
    })

    ports = [r for r in read(os.path.join(a.dados, 'ports.csv')) if r['port'] in HOSTS]
    flows = read(os.path.join(a.dados, 'flows.csv'))
    t0 = min(ts(r['timestamp']) for r in ports)

    def x(r):
        return (ts(r['timestamp']) - t0).total_seconds()

    marks = []
    ev = os.path.join(a.dados, 'eventos.log')
    if os.path.exists(ev):
        for line in open(ev):
            m = re.match(r'(\S+)\s+FASE(\d)', line)
            if m:
                marks.append(((ts(m.group(1)) - t0).total_seconds(), f'F{m.group(2)}'))

    max_port_mbps = max(max(float(r['rx_bps']), float(r['tx_bps'])) / 1e6 for r in ports)
    use_symlog = max_port_mbps > 100.0

    def decorate(ax, ylabel, ncol=2):
        ymin, ymax = ax.get_ylim()
        ax.set_ylim(ymin, ymax * 1.12)
        _, ymax2 = ax.get_ylim()
        for t, name in marks:
            ax.axvline(t, color='#64748b', ls=':', lw=1.2)
            ax.text(t + 0.8, ymax2 * 0.96, name, va='top', ha='left',
                    fontsize=11, fontweight='bold', color='#475569')
        ax.set_xlabel('Tempo (s)', fontweight='bold')
        ax.set_ylabel(ylabel, fontweight='bold')
        ax.legend(loc='lower center', bbox_to_anchor=(0.5, 1.02),
                  ncol=ncol, frameon=False, columnspacing=1.2, handletextpad=0.5)

    def save_fig(fig, stem):
        fig.tight_layout()
        fig.savefig(os.path.join(a.out, stem + '.pdf'), bbox_inches='tight')
        plt.close(fig)

    # 1) Vazao por porta
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    for p, label in [('1', 'Porta 1 (h1) RX — TCP'),
                     ('3', 'Porta 3 (h3) RX — UDP'),
                     ('2', 'Porta 2 (h2) RX — ACKs')]:
        rows = [r for r in ports if r['port'] == p][1:]
        ax.plot([x(r) for r in rows], [float(r['rx_bps']) / 1e6 for r in rows],
                marker='o', ms=4, lw=2.0, label=label)
    rows_h2 = [r for r in ports if r['port'] == '2'][1:]
    ax.plot([x(r) for r in rows_h2], [float(r['tx_bps']) / 1e6 for r in rows_h2],
            marker='^', ms=4, lw=2.2, ls='--', color='#d62728',
            label='Porta 2 (h2) TX — Total')
    if use_symlog:
        ax.set_yscale('symlog', linthresh=0.1)
    decorate(ax, 'Vazão (Mbit/s)', ncol=2)
    save_fig(fig, 'vazao_portas')

    # 2) Vazao por fluxo L3/L4
    series = defaultdict(list)
    for r in flows:
        ip_s, ip_d, proto = r.get('ip_src', ''), r.get('ip_dst', ''), r.get('ip_proto', '')
        if ip_s and ip_d:
            hs = IP_TO_HOST.get(ip_s, ip_s)
            hd = IP_TO_HOST.get(ip_d, ip_d)
            p_name = PROTO.get(proto, f'proto {proto}')
            if p_name == 'TCP' and hs == 'h2':
                lbl = 'h2 → h1 (TCP ACK)'
            else:
                lbl = f'{hs} → {hd} ({p_name})'
        else:
            lbl = f"{r['eth_src']} → {r['eth_dst']}"
        series[lbl].append(((ts(r['timestamp']) - t0).total_seconds(), float(r['bps']) / 1e6))

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    for lbl, pts in sorted(series.items()):
        if max(v for _, v in pts) > 0.05:
            pts_plot = pts[1:] if len(pts) > 1 else pts
            ax.plot(*zip(*pts_plot), marker='o', ms=4, lw=2.0, label=lbl)
    if use_symlog:
        ax.set_yscale('symlog', linthresh=0.1)
    decorate(ax, 'Vazão (Mbit/s)', ncol=2)
    save_fig(fig, 'vazao_fluxos')

    # 3) Pacotes por segundo por porta
    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    for p, h in HOSTS.items():
        rows = [r for r in ports if r['port'] == p]
        xs, ys = [], []
        for a_, b_ in zip(rows, rows[1:]):
            dt = x(b_) - x(a_)
            if dt > 0:
                xs.append(x(b_))
                ys.append((int(b_['rx_pkts']) - int(a_['rx_pkts'])) / dt)
        ax.plot(xs, ys, marker='o', ms=4, lw=2.0, label=f'Porta {p} ({h}) RX')
    ax.set_yscale('symlog', linthresh=10)
    decorate(ax, 'Pacotes/s (symlog)', ncol=3)
    save_fig(fig, 'pacotes_portas')

    # 4) Validacao: Ryu vs iperf (TCP e UDP)
    iperf_tcp = parse_iperf(os.path.join(a.dados, 'iperf_h1_tcp.txt'))
    iperf_udp = parse_iperf(os.path.join(a.dados, 'iperf_h3_udp.txt'))
    start_tcp = next((t for t, n in marks if n == 'F3'), 0) + 1
    start_udp = next((t for t, n in marks if n == 'F4'), 0)

    fig, ax = plt.subplots(figsize=(7.2, 4.4))
    rows1 = [r for r in ports if r['port'] == '1'][1:]
    rows3 = [r for r in ports if r['port'] == '3'][1:]
    ax.plot([x(r) for r in rows1], [float(r['rx_bps']) / 1e6 for r in rows1],
            marker='o', ms=4, lw=2.0, label='Ryu (h1 TCP)')
    if iperf_tcp:
        ax.plot([start_tcp + t for t, _ in iperf_tcp], [v for _, v in iperf_tcp],
                marker='s', ms=4.5, ls='--', lw=2.0, label='iperf (h1 TCP)')
    ax.plot([x(r) for r in rows3], [float(r['rx_bps']) / 1e6 for r in rows3],
            marker='o', ms=4, lw=2.0, label='Ryu (h3 UDP)')
    if iperf_udp:
        ax.plot([start_udp + t for t, _ in iperf_udp], [v for _, v in iperf_udp],
                marker='s', ms=4.5, ls='--', lw=2.0, label='iperf (h3 UDP)')
    if use_symlog:
        ax.set_yscale('symlog', linthresh=0.1)
    decorate(ax, 'Vazão (Mbit/s)', ncol=2)
    save_fig(fig, 'comparacao_iperf')
    print('Figuras salvas em', a.out)


if __name__ == '__main__':
    main()

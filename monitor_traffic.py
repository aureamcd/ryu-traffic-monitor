# -*- coding: utf-8 -*-
"""
monitor_traffic.py -- Traffic Monitor (telemetria programavel) para Ryu / OpenFlow 1.3

Disciplina: CPPGCC/CCN005 - Topicos Especiais em Redes de Computadores (SDN) | PPGCC/UFPI
Projeto Final (3a Avaliacao) - Semana 1

Descricao
---------
Estende a aplicacao ``SimpleSwitch13`` (switch de aprendizagem L2, OpenFlow 1.3)
com o monitor ``SimpleMonitor13`` do Cap. 3 do livro oficial do Ryu (Traffic Monitor):

  * Corrotina cooperativa periodica (``hub.spawn(self._monitor)``) que consulta
    todos os switches conectados em intervalos fixos e configuraveis.
  * ``OFPFlowStatsRequest``/``OFPFlowStatsReply``: dpid, in_port, out_port,
    enderecos L2 (e L3/L4, quando presentes no match), packet_count e byte_count.
  * ``OFPPortStatsRequest``/``OFPPortStatsReply``: rx/tx packets, bytes e errors.
  * Vazao instantanea (Kbit/s ou Mbit/s) = (delta_bytes * 8) / delta_t, calculada
    entre duas amostras consecutivas, por porta (RX e TX) e por fluxo.
  * Saida legivel no terminal e exportacao estruturada em CSV (``ports.csv`` e
    ``flows.csv``) e em JSON Lines (``samples.jsonl``).

Configuracao (variaveis de ambiente)
------------------------------------
  MONITOR_INTERVAL  intervalo de polling em segundos            (padrao: 10)
  MONITOR_OUTDIR    diretorio de saida dos arquivos estruturados (padrao: ./telemetria)

Execucao
--------
  ryu-manager --ofp-tcp-listen-port 6653 monitor_traffic.py
"""

import csv
import json
import os
import time
from operator import attrgetter

from ryu.app import simple_switch_13
from ryu.controller import ofp_event
from ryu.controller.handler import MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.lib import hub
from ryu.lib.packet import ethernet, ether_types, ipv4, tcp, udp, icmp, packet

# Cabecalhos dos arquivos CSV
PORT_FIELDS = ['timestamp', 'dpid', 'port', 'rx_pkts', 'rx_bytes', 'rx_errors',
               'tx_pkts', 'tx_bytes', 'tx_errors', 'rx_bps', 'tx_bps']
FLOW_FIELDS = ['timestamp', 'dpid', 'in_port', 'eth_src', 'eth_dst', 'ip_src',
               'ip_dst', 'ip_proto', 'l4_src', 'l4_dst', 'out_port',
               'packet_count', 'byte_count', 'duration_s', 'bps']


def format_rate(bps):
    """Converte bit/s para uma string legivel em Kbit/s ou Mbit/s."""
    if bps >= 1e6:
        return '%9.3f Mbit/s' % (bps / 1e6)
    return '%9.3f Kbit/s' % (bps / 1e3)


class TrafficMonitor13(simple_switch_13.SimpleSwitch13):
    """Switch L2 (OpenFlow 1.3) com telemetria periodica de fluxos e portas."""

    def __init__(self, *args, **kwargs):
        super(TrafficMonitor13, self).__init__(*args, **kwargs)
        self.datapaths = {}                  # dpid -> Datapath ativo
        self.interval = float(os.environ.get('MONITOR_INTERVAL', 10))
        self.outdir = os.environ.get('MONITOR_OUTDIR', 'telemetria')
        # Amostras anteriores para o calculo da taxa: chave -> (t, bytes...)
        self._prev_ports = {}
        self._prev_flows = {}
        self._init_outputs()
        # Corrotina cooperativa (Eventlet) que dispara o polling periodico
        self.monitor_thread = hub.spawn(self._monitor)
        self.logger.info('TrafficMonitor13 iniciado: intervalo=%.1fs saida=%s',
                         self.interval, self.outdir)

    # ------------------------------------------------------------------ saida
    def _init_outputs(self):
        os.makedirs(self.outdir, exist_ok=True)
        self._ports_csv = open(os.path.join(self.outdir, 'ports.csv'), 'w',
                               newline='')
        self._flows_csv = open(os.path.join(self.outdir, 'flows.csv'), 'w',
                               newline='')
        self._jsonl = open(os.path.join(self.outdir, 'samples.jsonl'), 'w')
        self._ports_w = csv.DictWriter(self._ports_csv, fieldnames=PORT_FIELDS)
        self._flows_w = csv.DictWriter(self._flows_csv, fieldnames=FLOW_FIELDS)
        self._ports_w.writeheader()
        self._flows_w.writeheader()
        self._ports_csv.flush()
        self._flows_csv.flush()

    def _export(self, kind, writer, fh, row):
        """Grava a linha no CSV e no JSON Lines (formato estruturado)."""
        writer.writerow(row)
        fh.flush()
        self._jsonl.write(json.dumps(dict(row, type=kind)) + '\n')
        self._jsonl.flush()

    # ------------------------------------ comutacao L2 com match L3/L4 (IPv4)
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in_handler(self, ev):
        """
        Estende o switch de aprendizagem L2 (SimpleSwitch13) para instalar regras
        com match L3/L4 em pacotes IPv4 (IP origem/destino, protocolo e portas
        TCP/UDP), permitindo que OFPFlowStatsReply capture enderecos de rede e
        transporte e separe fluxos ICMP, TCP e UDP individualmente.
        """
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match['in_port']

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocols(ethernet.ethernet)[0]
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dst = eth.dst
        src = eth.src
        dpid = format(datapath.id, "d").zfill(16)
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][src] = in_port

        if dst in self.mac_to_port[dpid]:
            out_port = self.mac_to_port[dpid][dst]
        else:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        if out_port != ofproto.OFPP_FLOOD:
            match_fields = {
                'in_port': in_port,
                'eth_src': src,
                'eth_dst': dst,
                'eth_type': eth.ethertype,
            }
            priority = 1
            ip_pkt = pkt.get_protocol(ipv4.ipv4)
            if ip_pkt is not None:
                match_fields['ipv4_src'] = ip_pkt.src
                match_fields['ipv4_dst'] = ip_pkt.dst
                match_fields['ip_proto'] = ip_pkt.proto
                priority = 10
                tcp_pkt = pkt.get_protocol(tcp.tcp)
                udp_pkt = pkt.get_protocol(udp.udp)
                if tcp_pkt is not None:
                    match_fields['tcp_src'] = tcp_pkt.src_port
                    match_fields['tcp_dst'] = tcp_pkt.dst_port
                    priority = 20
                elif udp_pkt is not None:
                    match_fields['udp_src'] = udp_pkt.src_port
                    match_fields['udp_dst'] = udp_pkt.dst_port
                    priority = 20

            match = parser.OFPMatch(**match_fields)
            if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                self.add_flow(datapath, priority, match, actions, msg.buffer_id)
                return
            else:
                self.add_flow(datapath, priority, match, actions)

        data = None
        if msg.buffer_id == ofproto.OFP_NO_BUFFER:
            data = msg.data

        out = parser.OFPPacketOut(
            datapath=datapath, buffer_id=msg.buffer_id,
            in_port=in_port, actions=actions, data=data)
        datapath.send_msg(out)

    # ------------------------------------------- rastreio de switches (dpids)
    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change_handler(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            if datapath.id not in self.datapaths:
                self.logger.debug('register datapath: %016x', datapath.id)
                self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                self.logger.debug('unregister datapath: %016x', datapath.id)
                del self.datapaths[datapath.id]
                # descarta amostras antigas: evita taxa errada apos reconexao
                self._prev_ports = {k: v for k, v in self._prev_ports.items()
                                    if k[0] != datapath.id}
                self._prev_flows = {k: v for k, v in self._prev_flows.items()
                                    if k[0] != datapath.id}

    # ------------------------------------------------------------- polling
    def _monitor(self):
        while True:
            for dp in list(self.datapaths.values()):
                self._request_stats(dp)
            hub.sleep(self.interval)

    def _request_stats(self, datapath):
        self.logger.debug('send stats request: %016x', datapath.id)
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        req = parser.OFPFlowStatsRequest(datapath)
        datapath.send_msg(req)

        req = parser.OFPPortStatsRequest(datapath, 0, ofproto.OFPP_ANY)
        datapath.send_msg(req)

    # --------------------------------------------------------- estat. fluxos
    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        now = time.monotonic()
        stamp = time.strftime('%Y-%m-%dT%H:%M:%S')

        self.logger.info('')
        self.logger.info('=== FLUXOS  dpid=%016x ===', dpid)
        self.logger.info('in  out  ip-src          ip-dst          proto  l4-src l4-dst'
                         '   packets      bytes          vazao')
        self.logger.info('--- ---- --------------- --------------- ------ ------ ------'
                         ' --------- ---------- --------------')
        # priority=0 e a regra table-miss (envio ao controlador): ignorada
        flows = [f for f in body if f.priority >= 1]
        for stat in sorted(flows, key=lambda f: (f.match.get('in_port', 0),
                                                 f.match.get('ipv4_dst', ''),
                                                 f.match.get('eth_dst', ''))):
            m = stat.match
            out_port = self._out_port(stat)
            l4_src = m.get('tcp_src', m.get('udp_src'))
            l4_dst = m.get('tcp_dst', m.get('udp_dst'))
            key = (dpid, m.get('in_port'), m.get('eth_src'), m.get('eth_dst'),
                   m.get('ipv4_src'), m.get('ipv4_dst'), m.get('ip_proto'),
                   l4_src, l4_dst, out_port)
            bps = self._rate(self._prev_flows, key, now, stat.byte_count)
            proto_map = {1: 'ICMP', 6: 'TCP', 17: 'UDP'}
            proto_str = proto_map.get(m.get('ip_proto'), str(m.get('ip_proto') or 'L2'))
            self.logger.info('%3s %4s %15s %15s %6s %6s %6s %9d %10d %s',
                             m.get('in_port', '-'), out_port,
                             m.get('ipv4_src') or m.get('eth_src', '-'),
                             m.get('ipv4_dst') or m.get('eth_dst', '-'),
                             proto_str, l4_src or '-', l4_dst or '-',
                             stat.packet_count, stat.byte_count,
                             format_rate(bps))
            self._export('flow', self._flows_w, self._flows_csv, {
                'timestamp': stamp, 'dpid': '%016x' % dpid,
                'in_port': m.get('in_port'), 'eth_src': m.get('eth_src'),
                'eth_dst': m.get('eth_dst'), 'ip_src': m.get('ipv4_src'),
                'ip_dst': m.get('ipv4_dst'), 'ip_proto': m.get('ip_proto'),
                'l4_src': l4_src, 'l4_dst': l4_dst, 'out_port': out_port,
                'packet_count': stat.packet_count,
                'byte_count': stat.byte_count,
                'duration_s': stat.duration_sec,
                'bps': round(bps, 1)})

    @staticmethod
    def _out_port(stat):
        """Primeira acao OUTPUT da primeira instrucao (ou '-' se nao houver)."""
        for inst in stat.instructions:
            for action in getattr(inst, 'actions', []):
                if hasattr(action, 'port'):
                    return action.port
        return '-'

    # --------------------------------------------------------- estat. portas
    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply_handler(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        now = time.monotonic()
        stamp = time.strftime('%Y-%m-%dT%H:%M:%S')

        self.logger.info('')
        self.logger.info('=== PORTAS  dpid=%016x ===', dpid)
        self.logger.info('  port  rx-pkts  rx-bytes rx-err  tx-pkts  tx-bytes '
                         'tx-err         rx-vazao         tx-vazao')
        self.logger.info('------ -------- --------- ------ -------- --------- '
                         '------ ---------------- ----------------')
        ofproto = ev.msg.datapath.ofproto
        for stat in sorted(body, key=attrgetter('port_no')):
            if stat.port_no >= ofproto.OFPP_MAX:
                continue
            rx_bps = self._rate(self._prev_ports, (dpid, stat.port_no, 'rx'),
                                now, stat.rx_bytes)
            tx_bps = self._rate(self._prev_ports, (dpid, stat.port_no, 'tx'),
                                now, stat.tx_bytes)
            self.logger.info('%6x %8d %9d %6d %8d %9d %6d %s %s',
                             stat.port_no, stat.rx_packets, stat.rx_bytes,
                             stat.rx_errors, stat.tx_packets, stat.tx_bytes,
                             stat.tx_errors, format_rate(rx_bps),
                             format_rate(tx_bps))
            self._export('port', self._ports_w, self._ports_csv, {
                'timestamp': stamp, 'dpid': '%016x' % dpid,
                'port': stat.port_no, 'rx_pkts': stat.rx_packets,
                'rx_bytes': stat.rx_bytes, 'rx_errors': stat.rx_errors,
                'tx_pkts': stat.tx_packets, 'tx_bytes': stat.tx_bytes,
                'tx_errors': stat.tx_errors, 'rx_bps': round(rx_bps, 1),
                'tx_bps': round(tx_bps, 1)})

    # ------------------------------------------------------ vazao instantanea
    @staticmethod
    def _rate(store, key, now, byte_count):
        """
        Vazao instantanea em bit/s: (delta_bytes * 8) / delta_t.
        Usa relogio monotonico; a primeira amostra de uma chave retorna 0.
        Se o contador regredir (regra reinstalada), retorna 0 e reinicia a base.
        """
        prev = store.get(key)
        store[key] = (now, byte_count)
        if prev is None:
            return 0.0
        dt = now - prev[0]
        delta = byte_count - prev[1]
        if dt <= 0 or delta < 0:
            return 0.0
        return (delta * 8.0) / dt

# Traffic Monitor com Ryu (OpenFlow 1.3) — Telemetria Programável em SDN

Projeto Final (3ª Avaliação) — CPPGCC/CCN005 *Tópicos Especiais em Redes de Computadores (SDN)* — PPGCC/UFPI  
Docente: Prof. Dr. Rayner Gomes Sousa · Discente: Áurea Letícia Carvalho Macedo

Aplicação `monitor_traffic.py`: um switch de aprendizagem L2 (`SimpleSwitch13`) estendido com um
monitor periódico de **fluxos** e **portas** que calcula a **vazão instantânea** e exporta CSV/JSON.

## Funcionalidades

| Requisito | Implementação |
|---|---|
| Herança + comutação L2 | `TrafficMonitor13(simple_switch_13.SimpleSwitch13)`, OpenFlow 1.3 |
| Corrotina periódica | `hub.spawn(self._monitor)`; intervalo configurável (`MONITOR_INTERVAL`, padrão 10 s) |
| Estatísticas de fluxos | `OFPFlowStatsRequest/Reply`: dpid, in/out port, MACs (e IP/L4 se houver), `packet_count`, `byte_count` |
| Estatísticas de portas | `OFPPortStatsRequest/Reply`: `rx/tx_packets`, `rx/tx_bytes`, `rx/tx_errors` |
| Vazão instantânea | `(Δbytes × 8) / Δt` entre amostras consecutivas (relógio monotônico), em Kbit/s ou Mbit/s |
| Saída estruturada | `ports.csv`, `flows.csv`, `samples.jsonl` em `MONITOR_OUTDIR` (padrão `./telemetria`) |

A regra *table-miss* (`priority=0`) é ignorada nos fluxos. Se o contador regredir (regra reinstalada), a taxa é zerada e a base reiniciada.

## Pré-requisitos

- Linux (ou WSL2) com **Mininet 2.3**, **Open vSwitch**, **iperf 2** e **Python 3.9**
- **Ryu 4.34** (`pip install ryu`; no Python ≥ 3.10 use `eventlet==0.30.2` e `setuptools<68`, ou o Python 3.9)
- Para os gráficos: `pip install matplotlib`
- Em ambientes sem módulo de kernel do OVS (ex.: WSL/Docker), use `--userspace` e instale `ethtool` (os offloads das NICs dos hosts precisam ser desligados para o TCP funcionar nesse modo)

## Execução manual (passo a passo)

**Terminal 1 — controlador**
```bash
MONITOR_INTERVAL=2 ryu-manager --ofp-tcp-listen-port 6653 monitor_traffic.py
```

**Terminal 2 — topologia**
```bash
sudo mn --topo single,3 --mac --controller remote,ip=127.0.0.1,port=6653 --switch ovsk,protocols=OpenFlow13
```

**Geração de tráfego (CLI do Mininet)**
```text
mininet> h1 ping -c 10 h2                                  # ICMP (leve)
mininet> h2 iperf -s &                                      # servidor TCP
mininet> h2 iperf -s -u -p 5002 &                           # servidor UDP
mininet> h1 iperf -c 10.0.0.2 -t 30 -i 2                    # TCP (alta vazão)
mininet> h3 iperf -c 10.0.0.2 -p 5002 -u -b 10M -t 20 -i 2  # UDP limitado a 10 Mbit/s
```
Observe no Terminal 1 as tabelas `=== FLUXOS ===` e `=== PORTAS ===` com a vazão de cada porta/fluxo.

## Execução automatizada

```bash
# dentro do Mininet (root), com o Ryu já rodando em outro terminal:
sudo python3 run_experiment.py --out resultados          # acrescente --userspace se necessário

# ambiente Docker das aulas (contêineres ryu-ctrl e mininet-lab), no WSL como root:
sudo bash run_docker.sh 2        # 2 = intervalo de polling em segundos
```
O experimento executa 5 fases: ociosa (25 s) → ICMP → TCP (30 s) → TCP + UDP simultâneos → ociosa. Os dados vão para `dados/`.

## Gráficos

```bash
python3 plot_results.py --dados dados --out figuras
```
Gera `vazao_portas.png`, `vazao_fluxos.png`, `pacotes_portas.png` e `comparacao_iperf.png` (validação Ryu × iperf).

## Resultados obtidos (intervalo de 2 s)

| Fase | iperf (referência) | Medido pelo Ryu |
|---|---|---|
| TCP h1→h2 (30 s) | 572 Mbit/s (média) | ≈ 560–650 Mbit/s na porta 1 (RX) |
| UDP h3→h2 (`-b 10M`, 20 s) | 10,5 Mbit/s, 0 % de perda | ≈ 10,5 Mbit/s na porta 3 (RX) |
| Ociosa | — | ≈ 0 Mbit/s |

Os valores de `dados/` e `figuras/` foram gerados em Mininet 2.3.0 + OVS (datapath de usuário) + Ryu 4.34, em contêineres Docker sobre WSL2. A vazão absoluta do TCP depende da CPU do host, pois o datapath em espaço de usuário não é acelerado em hardware.

## Estrutura do repositório

```
monitor_traffic.py   controlador Ryu (switch L2 + telemetria)
run_experiment.py    topologia Mininet + tráfego ICMP/TCP/UDP
run_docker.sh        orquestração no ambiente Docker/WSL
plot_results.py      geração dos gráficos
dados/               CSV/JSONL do Ryu + saídas do iperf/ping (execução de referência)
figuras/             gráficos gerados
```

## Referências

- Ryu Book, cap. 3 *Traffic Monitor*: <https://ryu-sdn.org/ryu-book/>
- McKeown et al. *OpenFlow: enabling innovation in campus networks*. ACM CCR, 2008.
- ONF. *OpenFlow Switch Specification v1.3.0*, 2012.

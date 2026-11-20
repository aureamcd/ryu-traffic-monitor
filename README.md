# Traffic Monitor com Ryu (OpenFlow 1.3) — Telemetria Programável em SDN

Projeto Final (3ª Avaliação) — CPPGCC/CCN005 *Tópicos Especiais em Redes de Computadores (SDN)* — PPGCC/UFPI (CSHNB)  
Docente: Prof. Dr. Rayner Gomes Sousa · Discentes: Áurea Letícia Carvalho Macedo e Jonas César Gomes Nunes

Aplicação `monitor_traffic.py`: switch de aprendizagem L2 (`SimpleSwitch13`) estendido com **regras granulares L2/L3/L4** e monitoramento periódico de **fluxos** e **portas** no controlador Ryu (OpenFlow 1.3), com cálculo de **vazão instantânea** e exportação estruturada em CSV e JSON Lines.

## Funcionalidades

| Requisito | Implementação |
|---|---|
| Herança + comutação L2/L3/L4 | `TrafficMonitor13(simple_switch_13.SimpleSwitch13)` em OpenFlow 1.3, com regras granulares diferenciando ARP, ICMP, TCP e UDP |
| Corrotina periódica | `hub.spawn(self._monitor)`; intervalo configurável (`MONITOR_INTERVAL`, padrão 10 s) |
| Estatísticas de fluxos | `OFPFlowStatsRequest/Reply`: dpid, in/out port, endereços MAC, IPv4, `ip_proto`, portas L4 (TCP/UDP), `packet_count`, `byte_count` e `duration_s` |
| Estatísticas de portas | `OFPPortStatsRequest/Reply`: `rx/tx_packets`, `rx/tx_bytes`, `rx/tx_errors` |
| Vazão instantânea | `(Δbytes × 8) / Δt` entre amostras consecutivas (relógio monotônico), em Kbit/s ou Mbit/s |
| Saída estruturada | `ports.csv`, `flows.csv` e `samples.jsonl` em `MONITOR_OUTDIR` (padrão `./telemetria`) |

A regra *table-miss* (`priority=0`) e portas virtuais reservadas são filtradas nas métricas. Se um contador regredir (regra reinstalada), a estimativa de taxa é zerada e a base é reiniciada.

## Pré-requisitos

- Linux (ou WSL2) com **Mininet 2.3**, **Open vSwitch**, **iperf 2** e **Python 3.9**
- **Ryu 4.34** (`pip install ryu`; no Python ≥ 3.10 use `eventlet==0.30.2` e `setuptools<68`, ou Python 3.9)
- Para os gráficos: `pip install matplotlib`
- Em ambientes sem módulo de kernel do OVS (ex.: WSL/Docker), use `--userspace` e instale `ethtool` (os offloads das NICs dos hosts são desabilitados pelo script para o handshake TCP funcionar corretamente)

## Execução manual (passo a passo)

**Terminal 1 — controlador**
```bash
MONITOR_INTERVAL=2 ryu-manager --ofp-tcp-listen-port 6653 monitor_traffic.py
```

**Terminal 2 — topologia**
```bash
sudo mn --topo single,3 --mac --link tc,bw=20 --controller remote,ip=127.0.0.1,port=6653 --switch ovsk,protocols=OpenFlow13
```

**Geração de tráfego (CLI do Mininet)**
```text
mininet> h1 ping -c 10 h2                                  # ICMP (tráfego leve)
mininet> h2 iperf -s &                                      # servidor TCP
mininet> h2 iperf -s -u -p 5002 &                           # servidor UDP
mininet> h1 iperf -c 10.0.0.2 -t 30 -i 2                    # TCP saturando o enlace (20 Mbit/s)
mininet> h3 iperf -c 10.0.0.2 -p 5002 -u -b 10M -t 30 -i 2  # UDP concorrente (10 Mbit/s)
```
Observe no Terminal 1 as tabelas periódicas de fluxos e portas com a vazão instantânea calculada em tempo real.

## Execução automatizada

```bash
# dentro do Mininet (root), com o Ryu já rodando em outro terminal:
sudo python3 run_experiment.py --out resultados --link-bw 20   # acrescente --userspace se necessário

# ambiente Docker das aulas (contêineres ryu-ctrl e mininet-lab), no WSL como root:
sudo bash run_docker.sh 2        # 2 = intervalo de polling em segundos
```
O experimento executa 5 fases com enlaces limitados a **20 Mbit/s** (`TCLink`):
1. **F1 (ociosa):** 12 s sem tráfego (linha de base)
2. **F2 (ICMP):** `h1 ping -c 10 -i 1 h2` (~10 s)
3. **F3 (TCP):** `h1 → h2` via `iperf` por 30 s saturando o enlace em ~20 Mbit/s
4. **F4 (TCP + UDP simultâneo):** aos 15 s do TCP, entrada de `h3 → h2` UDP a 10 Mbit/s por 30 s, forçando o recuo do TCP
5. **F5 (ociosa final):** 10 s sem tráfego

Os arquivos coletados são salvos em `dados/`.

## Gráficos

```bash
python3 plot_results.py --dados dados --out figuras
```
Gera em `figuras/` os gráficos vetoriais em PDF:
- `vazao_portas.pdf` — vazão instantânea por porta do switch `s1`
- `vazao_fluxos.pdf` — vazão instantânea por fluxo L3/L4 (`OFPFlowStatsReply`)
- `pacotes_portas.pdf` — taxa de pacotes recebidos por porta (pacotes/s)
- `comparacao_iperf.pdf` — validação da vazão medida pelo Ryu frente aos relatórios do `iperf`

## Resultados obtidos (enlaces de 20 Mbit/s, polling de 2 s)

| Fase | iperf (referência) | Medido pelo Ryu |
|---|---|---|
| F1 / F5 (Ociosa) | — | ≈ 0 Mbit/s |
| F2 (ICMP `h1 → h2`) | 10 pacotes, 0% de perda | ≈ 0,8 Kbit/s (1–2 pacotes/s) |
| F3 (TCP `h1 → h2` isolado) | ≈ 19,0–19,9 Mbit/s | ≈ 20,0 Mbit/s (saturação do enlace) |
| F4 (TCP + UDP `10M` concorrentes) | TCP recua para ≈ 8,9–9,4 Mbit/s; UDP ≈ 10,5 Mbit/s | Porta 2 TX mantida em 20,0 Mbit/s; TCP ≈ 9,3 Mbit/s; UDP ≈ 10,8 Mbit/s |

A diferença de ~3% entre o Ryu e o `iperf` (ex.: 10,8 vs. 10,5 Mbit/s no UDP) decorre dos cabeçalhos de enlace, rede e transporte contabilizados nos contadores do switch OpenFlow.

## Estrutura do repositório

```
monitor_traffic.py   controlador Ryu (switch L2/L3/L4 + telemetria de fluxos e portas)
run_experiment.py    topologia Mininet (TCLink 20 Mbit/s) + tráfego ICMP/TCP/UDP
run_docker.sh        orquestração no ambiente Docker/WSL
plot_results.py      geração dos gráficos em PDF
dados/               CSV/JSONL do Ryu + saídas do iperf/ping/ovs-ofctl (execução de referência)
figuras/             gráficos vetoriais gerados (.pdf)
```

## Referências

- Ryu Book, cap. 3 *Traffic Monitor*: <https://ryu-sdn.org/ryu-book/>
- McKeown et al. *OpenFlow: enabling innovation in campus networks*. ACM CCR, 2008.
- ONF. *OpenFlow Switch Specification v1.3.0*, 2012.
- Alashhab et al. *Enhancing DDoS attack detection and mitigation in SDN using an ensemble online machine learning model*. IEEE Access, 12:51630–51649, 2024.

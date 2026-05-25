# 📻 hblink-baliza

> **EN** — Cyclic **voice beacon** for DMR servers (hblink3 / FreeDMR / ADN). It periodically transmits a spoken announcement (text → TTS → AMBE) to a talkgroup, connecting as a normal **homebrew peer** — no patching of `bridge.py` or the master.
>
> **ES** — Baliza **cíclica de voz** para servidores DMR (hblink3 / FreeDMR / ADN). Emite cada N minutos un anuncio hablado (texto → TTS → AMBE) en un talkgroup, conectándose como **peer homebrew** normal — sin tocar `bridge.py` ni el master.

![License](https://img.shields.io/github/license/pyopower/hblink-baliza?color=blue)
![Python](https://img.shields.io/badge/python-3.x-blue.svg)
![Platform](https://img.shields.io/badge/platform-Linux-lightgrey.svg)
![DMR](https://img.shields.io/badge/DMR-hblink3%20%7C%20FreeDMR-green.svg)
![TTS](https://img.shields.io/badge/voice-piper%20%7C%20espeak-orange.svg)
![Last commit](https://img.shields.io/github/last-commit/pyopower/hblink-baliza)

**🌐 [English](#english) · [Español](#español)**

---

## English

### What it is
A standalone **voice beacon** for DMR. The message is defined as **text**, synthesised by TTS (neural **piper** or **espeak**), encoded to **AMBE** (via the md380-emu AMBEserver) and transmitted to a configurable **talkgroup / timeslot** every N minutes. It logs into the master as an ordinary homebrew **peer**, so it is portable to any hblink3/FreeDMR server by editing one config file.

### How it works
1. **Audio** — `MESSAGE` is synthesised (piper/espeak) → 8 kHz PCM → **AMBE72** (DMR format with FEC) through the md380-emu **AMBEserver** (`md380-emu -s`, UDP `:2460`, AMBE3000 protocol). Cached as `baliza.ambe`.
2. **Transmission** — `baliza.py` brings up a homebrew **peer**, logs into the master and every `INTERVAL_MIN` builds a group voice call with `mk_voice.pkt_gen` (LC header + 3-AMBE-frame bursts + terminator), streamed at 60 ms/frame. The master routes it like any call.
3. **Auto-regeneration** — on start, if `MESSAGE` changed (or the cache is missing) the audio is regenerated automatically (`AUTO_REGEN: True`).

### Components
| File | Purpose |
|---|---|
| `baliza.py` | Long-running beacon: homebrew peer + scheduler (transmits to TG/slot). |
| `tts_ambe.py` | Audio generator: text → espeak/piper → PCM → AMBE72 (AMBEserver). |
| `provision_master.py` | Safely adds a dedicated permissive master for locked servers (backup + dry-run validate). |
| `install-baliza.sh` | Installer: auto-detects the hblink install & server type, installs the service. |
| `baliza.cfg.example` | Config template (copy to `baliza.cfg` and edit). |
| `hblink-baliza.service` | systemd unit. |

> The real `baliza.cfg` and the generated `*.ambe` are **git-ignored** (they hold per-server settings / audio). Copy `baliza.cfg.example` → `baliza.cfg`.

### Requirements
- A DMR master based on **hblink3** / **FreeDMR** (provides `hblink.py`, `config.py`, `mk_voice.py`, `voice_lib.py`).
- `python3`, `python3-bitarray`, `dmr_utils3`, `twisted` (already used by hblink).
- For (re)generating audio: `sox`, and `espeak` and/or **piper**, plus an **AMBEserver** (md380-emu `-s`). At runtime the beacon only **reads** the `.ambe` cache, so you can pre-generate the audio on another machine and copy it.

### Quick install
```bash
tar xzf hblink-baliza-portable.tar.gz -C /tmp/baliza && cd /tmp/baliza
sudo ./install-baliza.sh                 # auto-detects install & server type
sudo nano /opt/hblink-baliza/baliza.cfg  # set MASTER_*, RADIO_ID, TG, SRC_ID, MESSAGE
sudo systemctl enable --now hblink-baliza
journalctl -u hblink-baliza -f
```

### Configuration (`[BALIZA]` highlights)
- `TG`, `SLOT` (1=TS1, 2=TS2 — prefer **TS2**, TS1 is often denied), `SRC_ID` (DMR ID shown as caller).
- `INTERVAL_MIN` (or `INTERVAL_SEC` for testing — it is the **gap between beacons**, not a fixed clock).
- `MESSAGE`, `TTS_ENGINE` (`piper`/`espeak`), `PIPER_MODEL`/`PIPER_SPEAKER` or `TTS_VOICE`.
- `HBLINK_PATH` (e.g. `/opt/HBlink3` or `/opt/ADN-DMR-Peer-Server`), `AUTO_REGEN`.

### Portability playbook
**1. Open master** (hblink3 `REG_ACL: PERMIT:ALL`, or `ALLOW_UNREG_ID: True`)
→ just point the peer at it in `baliza.cfg`. Done.

**2. Locked master** (FreeDMR/ADN: registered IDs only, per-user self-care passwords, proxy that reserves `127.0.0.1`, `MAX_PEERS: 1`)
→ don't connect to its public port (you'd clash with real hotspots / the proxy). Add a **dedicated permissive master** on a port **outside the proxy range** with `provision_master.py`, restart the master **once** (a few-second blip), then point the beacon there. FreeDMR bridges the TG dynamically.

```bash
sudo python3 /opt/hblink-baliza/provision_master.py \
     --cfg <master-config-in-use> --port 62090 --pass <beaconpass> --tg <TG>
sudo systemctl restart <master-service>     # then set MASTER_PORT 62090 in baliza.cfg
```
`provision_master.py` clones an existing `MODE: MASTER` section (so it inherits every key that fork requires), makes it permissive, **backs up**, **validates with a dry parse** and reverts on failure — it never rewrites your file.

### Notes / gotchas
- **TS1 may be denied** (`TGID_TS1_ACL: DENY:ALL`); use `SLOT: 2` unless you know TS1 is open for your TG.
- Some `hblink.py` forks run `config.build_config('hblink.cfg')` at import time (cwd-relative); `baliza.py` therefore `chdir(HBLINK_PATH)` first, which must contain a valid `hblink.cfg`.
- The peer `RADIO_ID` must not collide with real connected repeaters; `SRC_ID` is the identity shown to listeners.

---

## Español

### Qué es
Una **baliza de voz** autónoma para DMR. El mensaje se define como **texto**, se sintetiza por TTS (neural **piper** o **espeak**), se codifica a **AMBE** (vía el AMBEserver de md380-emu) y se transmite a un **talkgroup / timeslot** configurables cada N minutos. Entra al master como un **peer** homebrew normal, así que es portable a cualquier hblink3/FreeDMR cambiando un fichero de config.

### Cómo funciona
1. **Audio** — `MESSAGE` se sintetiza (piper/espeak) → PCM 8 kHz → **AMBE72** (formato DMR con FEC) por el **AMBEserver** de md380-emu (`md380-emu -s`, UDP `:2460`, protocolo AMBE3000). Se cachea en `baliza.ambe`.
2. **Transmisión** — `baliza.py` levanta un **peer** homebrew, hace login y cada `INTERVAL_MIN` arma una llamada de grupo de voz con `mk_voice.pkt_gen` (cabecera LC + bursts de 3 frames AMBE + terminador) a 60 ms/frame. El master la enruta como cualquier llamada.
3. **Auto-regeneración** — al arrancar, si `MESSAGE` cambió (o falta el cache) el audio se regenera solo (`AUTO_REGEN: True`).

### Componentes
| Fichero | Qué hace |
|---|---|
| `baliza.py` | Proceso permanente: peer homebrew + planificador (emite al TG/slot). |
| `tts_ambe.py` | Generador de audio: texto → espeak/piper → PCM → AMBE72 (AMBEserver). |
| `provision_master.py` | Añade con seguridad un master dedicado permisivo en servidores blindados (backup + validación en seco). |
| `install-baliza.sh` | Instalador: autodetecta la instalación hblink y el tipo de servidor, instala el servicio. |
| `baliza.cfg.example` | Plantilla de config (copiar a `baliza.cfg` y editar). |
| `hblink-baliza.service` | Unidad systemd. |

> El `baliza.cfg` real y los `*.ambe` generados están **excluidos del git** (datos por servidor / audio). Copia `baliza.cfg.example` → `baliza.cfg`.

### Requisitos
- Un master DMR basado en **hblink3** / **FreeDMR** (aporta `hblink.py`, `config.py`, `mk_voice.py`, `voice_lib.py`).
- `python3`, `python3-bitarray`, `dmr_utils3`, `twisted` (ya los usa hblink).
- Para (re)generar audio: `sox`, y `espeak` y/o **piper**, más un **AMBEserver** (md380-emu `-s`). En runtime la baliza solo **lee** el cache `.ambe`, así que puedes generar el audio en otra máquina y copiarlo.

### Instalación rápida
```bash
tar xzf hblink-baliza-portable.tar.gz -C /tmp/baliza && cd /tmp/baliza
sudo ./install-baliza.sh                 # autodetecta instalación y tipo de servidor
sudo nano /opt/hblink-baliza/baliza.cfg  # MASTER_*, RADIO_ID, TG, SRC_ID, MESSAGE
sudo systemctl enable --now hblink-baliza
journalctl -u hblink-baliza -f
```

### Configuración (claves de `[BALIZA]`)
- `TG`, `SLOT` (1=TS1, 2=TS2 — usa **TS2**, TS1 suele estar denegado), `SRC_ID` (ID DMR que se ve como "quien llama").
- `INTERVAL_MIN` (o `INTERVAL_SEC` para pruebas — es el **hueco entre balizas**, no reloj fijo).
- `MESSAGE`, `TTS_ENGINE` (`piper`/`espeak`), `PIPER_MODEL`/`PIPER_SPEAKER` o `TTS_VOICE`.
- `HBLINK_PATH` (p.ej. `/opt/HBlink3` o `/opt/ADN-DMR-Peer-Server`), `AUTO_REGEN`.

### Playbook de portabilidad
**1. Master abierto** (hblink3 `REG_ACL: PERMIT:ALL`, o `ALLOW_UNREG_ID: True`)
→ apunta el peer a él en `baliza.cfg`. Listo.

**2. Master blindado** (FreeDMR/ADN: solo IDs registrados, contraseñas self-care por usuario, proxy que reserva `127.0.0.1`, `MAX_PEERS: 1`)
→ no conectes a su puerto público (chocarías con hotspots reales / el proxy). Añade un **master dedicado permisivo** en un puerto **fuera del rango del proxy** con `provision_master.py`, reinicia el master **una vez** (corte de segundos) y apunta la baliza ahí. FreeDMR puentea el TG dinámicamente.

```bash
sudo python3 /opt/hblink-baliza/provision_master.py \
     --cfg <config-master-en-uso> --port 62090 --pass <passbaliza> --tg <TG>
sudo systemctl restart <servicio-master>     # luego MASTER_PORT 62090 en baliza.cfg
```
`provision_master.py` clona una sección `MODE: MASTER` existente (hereda todas las claves del fork), la deja permisiva, hace **backup**, **valida con parseo en seco** y revierte si falla — nunca reescribe tu fichero.

### Notas / gotchas
- **TS1 puede estar denegado** (`TGID_TS1_ACL: DENY:ALL`); usa `SLOT: 2` salvo que sepas que TS1 está abierto para tu TG.
- Algunos forks de `hblink.py` ejecutan `config.build_config('hblink.cfg')` al importarse (relativo al cwd); por eso `baliza.py` hace `chdir(HBLINK_PATH)` antes, que debe contener un `hblink.cfg` válido.
- El `RADIO_ID` del peer no debe colisionar con repetidores reales conectados; `SRC_ID` es la identidad que ven los oyentes.

---

## License
GPLv3 — see [LICENSE](LICENSE). Builds on the hblink3 / dmr_utils3 ecosystem (GPLv3).

*Generated with help from Claude (Anthropic).*

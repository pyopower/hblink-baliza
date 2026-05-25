# Baliza cíclica de voz para hblink3 / ADN-DMR-Peer-Server

Sistema de **baliza** (beacon) que cada N minutos transmite un anuncio de **voz**
a un **Talkgroup** y **timeslot** configurables de un servidor hblink3. El mensaje
se define como **texto** y se sintetiza con TTS (espeak) → AMBE (md380-emu).

Se conecta al master **como un PEER homebrew normal**: NO se modifica `bridge.py`
ni el master. Por eso es **portable** a cualquier hblink3/ADN cambiando el `.cfg`.

## Componentes (`/opt/hblink-baliza/`)

| Fichero | Qué hace |
|---|---|
| `baliza.cfg` | Configuración: peer hblink + sección `[BALIZA]` con TG, slot, intervalo y mensaje. |
| `tts_ambe.py` | Genera el audio: texto → espeak → PCM → AMBE72 (vía AMBEserver UDP). Cachea en `baliza.ambe`. |
| `baliza.py` | Proceso permanente: peer al master; cada N min transmite el audio al TG/slot. |
| `hblink-baliza.service` | Unidad systemd. |

## Cómo funciona

**Motor TTS** (`TTS_ENGINE` en `[BALIZA]`):
- `piper` — voz neural natural. Binario en `/opt/piper/`, modelo en
  `/opt/piper/voices/`. Voz por defecto: `es_ES-sharvard-medium` (España,
  locutor `F`=1 femenino, `M`=0 masculino) vía `PIPER_MODEL`/`PIPER_SPEAKER`.
  Velocidad con `PIPER_LENGTH_SCALE` (1.0 normal, >1 más lento).
- `espeak` — sintético (fallback). Voz con `TTS_VOICE` (`es+f1..f5` femeninas).

1. **Audio**: el texto de `MESSAGE` se sintetiza (piper o espeak) y se codifica a
   AMBE72 (formato DMR, con FEC) usando el **AMBEserver** de md380-emu
   (`md380-emu -s`, UDP :2460, protocolo AMBE3000 con paridad). El resultado se
   guarda en `baliza.ambe` (cabecera `BLZA` + nº frames + frames de 9 bytes).
2. **Transmisión**: `baliza.py` levanta un PEER hblink que hace login en el
   master. Cada `INTERVAL_MIN` minutos arma una llamada de grupo de voz con
   `mk_voice.pkt_gen` (cabecera LC + bursts de 3 frames AMBE + terminador) y la
   envía al master a 60 ms/frame. El master la enruta como cualquier llamada.
3. **Auto-regeneración**: al arrancar, si `MESSAGE` cambió (o falta el cache),
   `baliza.py` regenera `baliza.ambe` automáticamente (`AUTO_REGEN: True`).

## Cambiar el mensaje o el tiempo

Edita `baliza.cfg` (`MESSAGE`, `INTERVAL_MIN`, `TG`, `SLOT`, voz...) y reinicia:

```bash
sudo systemctl restart hblink-baliza
```

O regenera el audio a mano sin reiniciar (la baliza recarga el cache cada ciclo):

```bash
sudo python3 /opt/hblink-baliza/tts_ambe.py -c /opt/hblink-baliza/baliza.cfg
```

## Instalar como servicio

```bash
sudo cp hblink-baliza.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hblink-baliza
journalctl -u hblink-baliza -f
```

## Portar a otro servidor hblink3 / ADN-DMR-Peer-Server

1. Copia `/opt/hblink-baliza/` al nuevo host.
2. En `baliza.cfg`:
   - `[BEACON-PEER]`: `MASTER_IP`, `MASTER_PORT`, `PASSPHRASE`, `RADIO_ID`
     (un ID DMR libre), `PORT` (puerto UDP local libre del peer).
   - `[BALIZA]`: `HBLINK_PATH` (p.ej. `/opt/ADN-DMR-Peer-Server`), `TG`, `SLOT`,
     `INTERVAL_MIN`, `MESSAGE`, y `AMBE_HOST/AMBE_PORT` del AMBEserver.
3. Para voz neural piper: copia `/opt/piper/` (binario + libs + `espeak-ng-data`)
   y el modelo en `/opt/piper/voices/`, o pon `TTS_ENGINE: espeak`.
4. Requisitos del host: `python3`, `espeak`, `sox`, los módulos de hblink en
   `HBLINK_PATH` (incluye `mk_voice.py`/`voice_lib.py`), `python3-bitarray`,
   `dmr_utils3`, y un **AMBEserver** accesible (md380-emu `-s`) **solo para
   (re)generar el audio** — en runtime la baliza solo lee el cache `.ambe`, así
   que puedes pre-generar el `.ambe` en otra máquina y copiarlo.

## Playbook de portabilidad (sistema repetible)

El bundle trae dos herramientas que automatizan las dos situaciones reales:

- **`install-baliza.sh`** — copia todo, **autodetecta** la instalación hblink y el
  tipo de servidor (HBlink3 abierto vs FreeDMR/ADN blindado), instala el servicio
  y te dice cómo conectar según el caso.
- **`provision_master.py`** — para servidores **blindados**: añade de forma SEGURA
  un master dedicado permisivo para la baliza (clona una sección MASTER existente
  → hereda todas las claves del fork; hace backup; **valida con parseo en seco**;
  no reescribe el fichero; revierte si falla). No reinicia: te da el comando.

### Árbol de decisión

1. **¿El master acepta IDs no registrados?** (HBlink3 con `REG_ACL: PERMIT:ALL`, o
   un master con `ALLOW_UNREG_ID: True`):
   → Apunta el peer a ese master en `baliza.cfg`. Listo. (servidor abierto / HBlink3)

2. **¿Master blindado?** (FreeDMR/ADN: `ALLOW_UNREG_ID: False`, self-care por
   usuario, proxy que reserva `127.0.0.1`, `MAX_PEERS: 1`):
   → NO conectes a su puerto directo (chocas con hotspots / proxy). Crea un master
   dedicado con `provision_master.py` en un puerto **fuera del rango del proxy**, y
   reinicia el servicio del master **una vez** (corte de segundos). La baliza conecta
   ahí; FreeDMR puentea el TG dinámicamente. (servidor blindado / FreeDMR)

### Receta completa para un servidor nuevo

```bash
# en el destino
tar xzf hblink-baliza-portable.tar.gz -C /tmp/baliza && cd /tmp/baliza
sudo ./install-baliza.sh                 # autodetecta y guía
# (solo si es blindado) crear master dedicado, validado:
sudo python3 /opt/hblink-baliza/provision_master.py \
     --cfg <config-master-en-uso> --port 62090 --pass <passbaliza> --tg <TG>
sudo systemctl restart <servicio-del-master>
# editar /opt/hblink-baliza/baliza.cfg (MASTER_PORT/PASSPHRASE/TG/SRC_ID/MESSAGE)
sudo systemctl enable --now hblink-baliza
```

El audio viaja pre-generado (`baliza.ambe`); el destino no necesita TTS. Para
cambiar el mensaje, regenera el `.ambe` donde haya espeak/piper+AMBEserver y cópialo.

### Notas / gotchas

- **TS1 puede estar denegado** en el master (`TGID_TS1_ACL: DENY:ALL`). Usa
  `SLOT: 2` (TS2) salvo que sepas que TS1 está permitido para tu TG.
- Algunos forks de `hblink.py` ejecutan `config.build_config('hblink.cfg')` al
  importarse (relativo al cwd); por eso `baliza.py` hace `chdir(HBLINK_PATH)`
  antes de importar. Requiere que exista un `hblink.cfg` válido en `HBLINK_PATH`.
- El `RADIO_ID` del peer no debe colisionar con repetidores reales conectados.

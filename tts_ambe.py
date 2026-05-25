#!/usr/bin/env python3
#
# tts_ambe.py -- Genera el audio de la baliza (TTS -> WAV -> PCM -> AMBE72)
#                y lo cachea como frames AMBE listos para mk_voice/pkt_gen.
#
# Parte del sistema "baliza ciclica" para hblink3 / ADN-DMR-Peer-Server.
# No depende de hblink: solo de espeak, sox y un AMBEserver (md380-emu -s).
#
# El fichero de salida (.ambe) es: cabecera "BLZA" + uint16(num_frames) +
# num_frames * 9 bytes (AMBE72 con FEC, formato DMR, tal cual va al aire).
# El numero de frames se rellena a multiplo de 3 (un burst DMR = 3 frames).

import sys, os, socket, struct, subprocess, argparse, configparser

MAGIC = b'BLZA'
SAMPLES = 160                 # 20 ms a 8 kHz
FRAME_BYTES = SAMPLES * 2     # 320 bytes PCM s16
AMBE72_BYTES = 9

# --- AMBEserver (protocolo AMBE3000, tal y como lo implementa md380-emu -s) ---
# El CFG por defecto de md380-emu activa PARIDAD (cfg[2]=0xEC) y modo DMR
# 2450/1150 (72-bit con FEC, cfg[1]=0x21). Por eso cada paquete lleva el campo
# 0x2F + byte de paridad XOR, y la respuesta de voz es 72-bit.

def _ambe_pkt(ptype, body):
    """Construye un paquete AMBE3000 con campo de paridad."""
    length = len(body) + 2                       # body + 0x2F + paridad
    hdr = bytes([0x61, (length >> 8) & 0xff, length & 0xff, ptype])
    pre = hdr[1:] + body + b'\x2f'               # buffer[1..n-2]
    par = 0
    for b in pre:
        par ^= b
    return hdr + body + bytes([0x2f, par])

def ambe_encode(pcm, host, port):
    """Codifica PCM (s16le mono 8k, multiplo de 320 bytes) -> lista de frames de 9 bytes."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(4)
    s.connect((host, port))
    # RESET -> limpia el buffer interno del codificador (campo 0x33)
    s.send(_ambe_pkt(0x00, bytes([0x33])))
    r = s.recv(512)
    if not (len(r) >= 5 and r[3] == 0x00 and r[4] == 0x39):
        raise RuntimeError('AMBEserver no respondio READY al RESET: %s' % r.hex())

    frames = []
    n = len(pcm) // FRAME_BYTES
    for i in range(n):
        chunk = pcm[i * FRAME_BYTES:(i + 1) * FRAME_BYTES]
        # SPEECHD (0x00) + 160 muestras; el server las trata como big-endian.
        # El WAV/PCM de sox es little-endian, asi que reordenamos a BE aqui.
        be = b''.join(struct.pack('>h', struct.unpack_from('<h', chunk, j * 2)[0])
                      for j in range(SAMPLES))
        s.send(_ambe_pkt(0x02, bytes([0x00, SAMPLES]) + be))
        resp = s.recv(512)
        # resp: 0x61 len 0x01(CHANNEL) 0x01(CHAND) 72 <9 bytes> 0x2F par
        if not (len(resp) >= 15 and resp[3] == 0x01 and resp[4] == 0x01 and resp[5] == 72):
            raise RuntimeError('Respuesta AMBE inesperada en frame %d: %s' % (i, resp.hex()))
        frames.append(resp[6:6 + AMBE72_BYTES])
    s.close()
    return frames

def synth_pcm(B, text):
    """Sintetiza 'text' a PCM s16le mono 8k segun el motor TTS de [BALIZA].
    TTS_ENGINE = espeak (sintetico) | piper (neural). Devuelve bytes PCM."""
    engine = B.get('TTS_ENGINE', fallback='espeak').strip().lower()
    if engine == 'piper':
        wav = subprocess.run(
            [B.get('PIPER_BIN', fallback='/opt/piper/piper'), '-q',
             '-m', B.get('PIPER_MODEL'),
             '-s', B.get('PIPER_SPEAKER', fallback='0'),
             '--length_scale', B.get('PIPER_LENGTH_SCALE', fallback='1.0'),
             '-f', '-'],
            input=text.encode('utf-8'), check=True,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
    else:
        wav = subprocess.run(
            ['espeak', '-v', B.get('TTS_VOICE', fallback='es'),
             '-s', B.get('TTS_SPEED', fallback='150'),
             '-p', B.get('TTS_PITCH', fallback='50'),
             '-a', B.get('TTS_AMPLITUDE', fallback='150'),
             '--stdout', text],
            check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout
    sox = subprocess.run(
        ['sox', '-t', 'wav', '-', '-t', 'raw', '-r', '8000',
         '-e', 'signed', '-b', '16', '-c', '1', '-'],
        input=wav, check=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return sox.stdout

def write_cache(frames, path):
    with open(path, 'wb') as f:
        f.write(MAGIC)
        f.write(struct.pack('>H', len(frames)))
        for fr in frames:
            f.write(fr)
    return len(frames)

def main():
    ap = argparse.ArgumentParser(description='Genera el audio AMBE de la baliza desde texto (TTS).')
    ap.add_argument('-c', '--config', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'baliza.cfg'))
    ap.add_argument('-m', '--message', help='Texto a sintetizar (por defecto: el de [BALIZA] MESSAGE).')
    ap.add_argument('-o', '--out', help='Ruta del .ambe de salida (por defecto: [BALIZA] AMBE_FILE).')
    args = ap.parse_args()

    cfg = configparser.ConfigParser()
    cfg.read(args.config)
    b = cfg['BALIZA']
    text = args.message or b.get('MESSAGE')
    out = args.out or b.get('AMBE_FILE')
    host = b.get('AMBE_HOST', fallback='127.0.0.1')
    port = b.getint('AMBE_PORT', fallback=2460)
    engine = b.get('TTS_ENGINE', fallback='espeak')

    print('[baliza] TTS  : "%s"  (motor=%s)' % (text, engine))
    pcm = synth_pcm(b, text)
    # Rellena con silencio (PCM cero) hasta multiplo de 3 frames = 1 burst DMR.
    burst_bytes = FRAME_BYTES * 3
    if len(pcm) % burst_bytes:
        pcm += b'\x00' * (burst_bytes - (len(pcm) % burst_bytes))
    dur = len(pcm) / FRAME_BYTES * 0.02
    print('[baliza] audio: %.1f s  (%d frames de 20 ms)' % (dur, len(pcm) // FRAME_BYTES))
    print('[baliza] AMBE : codificando via %s:%d ...' % (host, port))
    frames = ambe_encode(pcm, host, port)
    nf = write_cache(frames, out)
    print('[baliza] OK   : %d frames AMBE -> %s' % (nf, out))

if __name__ == '__main__':
    main()

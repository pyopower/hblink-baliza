#!/usr/bin/env python3
#
# baliza.py -- Baliza ciclica de voz para hblink3 / ADN-DMR-Peer-Server.
#
# Se conecta al master local como un PEER homebrew normal (no toca bridge.py)
# y cada N minutos transmite un anuncio de voz (AMBE) a un TG/slot configurables.
# El audio se prepara con tts_ambe.py (TTS -> AMBE) y se recarga en cada ciclo,
# de modo que cambiar el mensaje no requiere reiniciar el servicio.
#
# PORTABLE: el mismo binario sirve para cualquier hblink3/ADN cambiando baliza.cfg.
# Solo necesita poder importar los modulos de hblink (HBLINK_PATH en [BALIZA]).

import sys, os, struct, argparse, configparser, hashlib
from bitarray import bitarray

MAGIC = b'BLZA'


def ensure_audio(B, logger):
    """Regenera el cache AMBE si falta o si cambio el mensaje/parametros de voz.
    Usa tts_ambe (espeak + AMBEserver). Si algo falla, reutiliza el cache previo."""
    ambe_file = B.get('AMBE_FILE')
    sig_parts = [B.get('MESSAGE', ''), B.get('TTS_ENGINE', 'espeak'),
                 B.get('TTS_VOICE', 'es'), B.get('TTS_SPEED', '150'),
                 B.get('TTS_PITCH', '50'), B.get('TTS_AMPLITUDE', '150'),
                 B.get('PIPER_MODEL', ''), B.get('PIPER_SPEAKER', '0'),
                 B.get('PIPER_LENGTH_SCALE', '1.0')]
    sig = hashlib.sha1('|'.join(sig_parts).encode('utf-8')).hexdigest()
    sigfile = ambe_file + '.sig'
    cur = None
    if os.path.exists(sigfile):
        with open(sigfile) as f:
            cur = f.read().strip()
    if os.path.exists(ambe_file) and cur == sig:
        return  # cache al dia
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import tts_ambe
        logger.info('(BALIZA) Mensaje/voz cambiado: regenerando audio AMBE...')
        pcm = tts_ambe.synth_pcm(B, B.get('MESSAGE'))
        bb = tts_ambe.FRAME_BYTES * 3
        if len(pcm) % bb:
            pcm += b'\x00' * (bb - (len(pcm) % bb))
        frames = tts_ambe.ambe_encode(pcm, B.get('AMBE_HOST', fallback='127.0.0.1'),
                                      B.getint('AMBE_PORT', fallback=2460))
        nf = tts_ambe.write_cache(frames, ambe_file)
        with open(sigfile, 'w') as f:
            f.write(sig)
        logger.info('(BALIZA) Audio regenerado: %d frames AMBE', nf)
    except Exception as e:
        if os.path.exists(ambe_file):
            logger.warning('(BALIZA) No pude regenerar (%s); uso el cache existente', e)
        else:
            logger.error('(BALIZA) No pude generar el audio y no hay cache previo: %s', e)
            raise

def load_params(cfg_path):
    cfg = configparser.ConfigParser()
    cfg.read(cfg_path)
    return cfg['BALIZA']

def load_phrase(path):
    """Lee el cache .ambe y lo convierte en una 'frase' para mk_voice.pkt_gen.
    Cada burst DMR = 3 frames AMBE72 repartidos en dos mitades de 108 bits."""
    with open(path, 'rb') as f:
        data = f.read()
    if data[:4] != MAGIC:
        raise RuntimeError('Cache AMBE invalido (magic): %s' % path)
    n = struct.unpack('>H', data[4:6])[0]
    off = 6
    frames = []
    for _ in range(n):
        ba = bitarray(endian='big')
        ba.frombytes(data[off:off + 9])
        off += 9
        frames.append(ba)
    word = []
    for i in range(0, n - (n % 3), 3):
        a0, a1, a2 = frames[i], frames[i + 1], frames[i + 2]
        # burst = bits[0:264] = a0(72) + a1[0:36] + EMB(48) + a1[36:72] + a2(72)
        word.append([a0 + a1[0:36], a1[36:72] + a2])
    return [word]   # una sola "palabra" = el mensaje entero


def main():
    ap = argparse.ArgumentParser(description='Baliza ciclica de voz (peer hblink).')
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument('-c', '--config', default=os.path.join(here, 'baliza.cfg'))
    args = ap.parse_args()

    B = load_params(args.config)
    cfg_abspath = os.path.abspath(args.config)
    hblink_path = B.get('HBLINK_PATH', fallback='/opt/HBlink3')
    sys.path.insert(0, hblink_path)
    # Algunos forks de hblink.py ejecutan config.build_config('hblink.cfg') a
    # nivel de modulo (relativo al cwd) al importarse. Nos situamos en HBLINK_PATH
    # para que esa lectura encuentre su fichero. Nuestras rutas son absolutas.
    os.chdir(hblink_path)

    # Imports de hblink (despues de fijar el path y el cwd)
    import config as hb_config
    import log
    from hblink import HBSYSTEM, systems
    from mk_voice import pkt_gen
    from dmr_utils3.utils import bytes_3, bytes_4
    from twisted.internet import reactor, task

    CONFIG = hb_config.build_config(cfg_abspath)
    logger = log.config_logging(CONFIG['LOGGER'])
    logger.info('(BALIZA) Iniciando baliza ciclica de voz')

    # Regenera el audio si el mensaje cambio (o si no existe el cache)
    if B.getboolean('AUTO_REGEN', fallback=True):
        ensure_audio(B, logger)

    # Parametros de la baliza
    sysname   = B.get('SYSTEM', fallback='BEACON-PEER')
    tg        = B.getint('TG')
    ts        = B.getint('SLOT', fallback=2)          # 1 = TS1, 2 = TS2
    slot      = 0 if ts == 1 else 1                    # mk_voice usa 0/1
    # INTERVAL_SEC (segundos) tiene prioridad sobre INTERVAL_MIN (minutos).
    # Es el HUECO entre el fin de una baliza y el inicio de la siguiente.
    if B.get('INTERVAL_SEC', fallback='').strip():
        interval = B.getfloat('INTERVAL_SEC')
    else:
        interval = B.getint('INTERVAL_MIN', fallback=15) * 60
    src_id    = B.getint('SRC_ID', fallback=CONFIG['SYSTEMS'][sysname]['RADIO_ID'] and
                          int.from_bytes(CONFIG['SYSTEMS'][sysname]['RADIO_ID'], 'big'))
    ambe_file = B.get('AMBE_FILE')

    # Estado de la conexion peer
    if sysname not in CONFIG['SYSTEMS']:
        logger.error('(BALIZA) No existe el sistema PEER "%s" en %s', sysname, args.config)
        sys.exit(1)

    systems[sysname] = HBSYSTEM(sysname, CONFIG, None)
    reactor.listenUDP(CONFIG['SYSTEMS'][sysname]['PORT'], systems[sysname],
                      interface=CONFIG['SYSTEMS'][sysname]['IP'])
    logger.info('(BALIZA) Peer "%s" -> master %s:%s, TG %d TS%d, cada %d min',
                sysname, CONFIG['SYSTEMS'][sysname]['MASTER_IP'],
                CONFIG['SYSTEMS'][sysname]['MASTER_PORT'], tg, ts, interval // 60)

    state = {'tx': False}

    def schedule_next(delay):
        reactor.callLater(delay, transmit)

    def transmit():
        if state['tx']:
            return
        conn = CONFIG['SYSTEMS'][sysname]['STATS']['CONNECTION']
        if conn != 'YES':
            logger.info('(BALIZA) Peer no conectado (estado=%s), reintento en 5s', conn)
            schedule_next(5)
            return
        try:
            phrase = load_phrase(ambe_file)
        except Exception as e:
            logger.error('(BALIZA) No puedo cargar el audio %s: %s', ambe_file, e)
            schedule_next(max(interval, 5))
            return
        nbursts = len(phrase[0])
        logger.info('(BALIZA) TX baliza: TG %d TS%d, %d bursts (~%.1fs)',
                    tg, ts, nbursts, nbursts * 3 * 0.02)
        speech = pkt_gen(bytes_3(src_id), bytes_3(tg), bytes_4(src_id), slot, phrase)
        state['tx'] = True
        # Bombeo no bloqueante: un frame DMR cada 60 ms (mantiene vivo el reactor/pings)
        pump = task.LoopingCall(None)

        def _send_next():
            try:
                pkt = next(speech)
            except StopIteration:
                if pump.running:
                    pump.stop()
                state['tx'] = False
                logger.info('(BALIZA) Fin de la transmision; siguiente en %.0fs', interval)
                schedule_next(interval)   # hueco entre balizas, no reloj fijo
                return
            systems[sysname].send_system(pkt)

        pump.f = _send_next
        pump.start(0.06)

    # Primer disparo retrasado (deja que el peer haga login); luego se autoencadena.
    first = B.getint('FIRST_DELAY_SEC', fallback=20)
    reactor.callLater(first, transmit)

    reactor.run()


if __name__ == '__main__':
    main()

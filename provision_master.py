#!/usr/bin/env python3
#
# provision_master.py -- Anade de forma SEGURA un master dedicado para la baliza
# a la config de un servidor hblink3 / FreeDMR / ADN-DMR-Peer-Server.
#
# Uso (en el servidor destino):
#   sudo python3 provision_master.py --cfg /ruta/al/master.cfg --port 62090 \
#        --pass MiPassBaliza --tg 213 [--name BALIZA-MASTER] [--hblink /opt/XXX]
#
# Que hace, sin romper nada:
#   1) Localiza la instalacion hblink (para validar e importar config.build_config).
#   2) CLONA una seccion [MODE: MASTER] existente y habilitada  ->  hereda TODAS
#      las claves que ese fork concreto exige (HBlink3 y FreeDMR difieren).
#   3) La deja permisiva para la baliza: ALLOW_UNREG_ID True (si el fork lo usa),
#      TGID_TS2_ACL PERMIT:ALL, TS2_STATIC=<tg>, SINGLE_MODE False, MAX_PEERS 1,
#      PORT y PASSPHRASE propios.
#   4) Hace BACKUP, ANEXA la seccion (no reescribe el fichero: conserva comentarios).
#   5) VALIDA con un parseo en seco (config.build_config). Si falla, REVIERTE.
#   6) NO reinicia nada: imprime el comando de reinicio y los ajustes del peer.

import argparse, os, sys, re, shutil, time

def find_hblink_path(cfg_path, override):
    if override:
        return override
    d = os.path.dirname(os.path.abspath(cfg_path))
    for _ in range(4):                       # sube hasta 4 niveles buscando hblink.py
        if os.path.exists(os.path.join(d, 'hblink.py')) and os.path.exists(os.path.join(d, 'config.py')):
            return d
        d = os.path.dirname(d)
    return None

def read_sections(text):
    """Devuelve lista de (nombre, [lineas]) preservando el texto original."""
    sections = []
    cur, buf = None, []
    for line in text.splitlines():
        m = re.match(r'^\[([^\]]+)\]\s*$', line)
        if m:
            if cur is not None:
                sections.append((cur, buf))
            cur, buf = m.group(1), []
        elif cur is not None:
            buf.append(line)
    if cur is not None:
        sections.append((cur, buf))
    return sections

def kv(line):
    """Parsea 'KEY: val' o 'KEY:val' o 'KEY = val'. Devuelve (key, sep, val) o None."""
    m = re.match(r'^([A-Za-z0-9_]+)\s*([:=])\s?(.*)$', line)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3)

def main():
    ap = argparse.ArgumentParser(description='Anade un master dedicado para la baliza (seguro).')
    ap.add_argument('--cfg', required=True, help='Config del master en uso (la que carga el servicio).')
    ap.add_argument('--port', required=True, type=int, help='Puerto UDP del master de la baliza (libre, fuera del rango del proxy).')
    ap.add_argument('--pass', dest='passphrase', required=True, help='Passphrase del master de la baliza.')
    ap.add_argument('--tg', required=True, type=int, help='Talkgroup a fijar como estatico en TS2.')
    ap.add_argument('--name', default='BALIZA-MASTER', help='Nombre de la nueva seccion.')
    ap.add_argument('--hblink', default=None, help='Ruta de la instalacion hblink (autodetecta si se omite).')
    args = ap.parse_args()

    cfg = os.path.abspath(args.cfg)
    if not os.path.isfile(cfg):
        sys.exit('ERROR: no existe %s' % cfg)

    hb = find_hblink_path(cfg, args.hblink)
    if not hb:
        sys.exit('ERROR: no encuentro la instalacion hblink (hblink.py/config.py). Usa --hblink.')

    text = open(cfg, encoding='utf-8', errors='replace').read()
    sections = read_sections(text)
    names = [n for n, _ in sections]

    if args.name in names:
        sys.exit('ERROR: la seccion [%s] ya existe en %s' % (args.name, cfg))

    # Comprobar que el puerto no este ya usado en la config
    for n, buf in sections:
        for ln in buf:
            p = kv(ln)
            if p and p[0] == 'PORT' and p[2].strip() == str(args.port):
                sys.exit('ERROR: el puerto %d ya esta en uso por la seccion [%s]' % (args.port, n))

    # Buscar una seccion MASTER habilitada para clonar
    template = None
    for n, buf in sections:
        d = {}
        for ln in buf:
            p = kv(ln)
            if p:
                d[p[0]] = p[2].strip()
        if d.get('MODE', '').upper() == 'MASTER' and d.get('ENABLED', '').lower() in ('true', '1', 'yes'):
            template = (n, buf)
            break
    if not template:
        sys.exit('ERROR: no encuentro ninguna seccion [MODE: MASTER] habilitada para clonar.')

    tmpl_name, tmpl_lines = template
    # Overrides que hacen el master apto para la baliza
    overrides = {
        'ENABLED': 'True',
        'PORT': str(args.port),
        'PASSPHRASE': args.passphrase,
        'MAX_PEERS': '1',
        'TGID_TS1_ACL': 'DENY:ALL',
        'TGID_TS2_ACL': 'PERMIT:ALL',
        'TS2_STATIC': str(args.tg),
        'TS1_STATIC': '',
        'SINGLE_MODE': 'False',
        'ALLOW_UNREG_ID': 'True',   # se aplica solo si la plantilla ya tiene la clave (FreeDMR)
        'IP': '127.0.0.1',
    }

    out = ['', '[%s]' % args.name]
    seen = set()
    for ln in tmpl_lines:
        p = kv(ln)
        if not p:
            continue                          # descartamos comentarios/lineas vacias de la plantilla
        key, sep, val = p
        if key in seen:
            continue
        seen.add(key)
        if key in overrides:
            out.append('%s: %s' % (key, overrides[key]))
        else:
            out.append('%s: %s' % (key, val))
    # claves obligatorias que la plantilla pudiera no tener
    for k in ('MODE', 'ENABLED', 'PORT', 'PASSPHRASE', 'TGID_TS2_ACL', 'TS2_STATIC'):
        if k not in seen:
            out.append('%s: %s' % (k, 'MASTER' if k == 'MODE' else overrides.get(k, '')))
    new_block = '\n'.join(out) + '\n'

    # Backup + anexar (sin reescribir lo existente)
    ts = time.strftime('%Y%m%d_%H%M%S')
    bak = '%s.bak-baliza-%s' % (cfg, ts)
    shutil.copy2(cfg, bak)
    with open(cfg, 'a', encoding='utf-8') as f:
        if not text.endswith('\n'):
            f.write('\n')
        f.write(new_block)
    print('Backup: %s' % bak)
    print('Anadida seccion [%s] (clonada de [%s]) en %s' % (args.name, tmpl_name, cfg))

    # Validacion en seco
    sys.path.insert(0, hb)
    cwd = os.getcwd()
    os.chdir(hb)
    try:
        import config as hb_config
        C = hb_config.build_config(cfg)
        assert args.name in C['SYSTEMS'], 'la seccion no aparece en SYSTEMS tras el parseo'
        print('VALIDACION OK. El master [%s] (puerto %d) parsea correctamente.' % (args.name, args.port))
    except SystemExit as e:
        shutil.copy2(bak, cfg)
        sys.exit('VALIDACION FALLIDA (%s). He REVERTIDO el cambio desde el backup.' % e)
    except Exception as e:
        shutil.copy2(bak, cfg)
        import traceback; traceback.print_exc()
        sys.exit('VALIDACION FALLIDA. He REVERTIDO el cambio desde el backup.')
    finally:
        os.chdir(cwd)

    print()
    print('Siguiente paso (cuando quieras, corta unos segundos a los hotspots):')
    print('   sudo systemctl restart <servicio-del-master>')
    print()
    print('Y en baliza.cfg de la baliza, en [BEACON-PEER]:')
    print('   MASTER_IP: 127.0.0.1')
    print('   MASTER_PORT: %d' % args.port)
    print('   PASSPHRASE: %s' % args.passphrase)
    print('   (RADIO_ID puede ser cualquiera; el master es permisivo)')
    print('Y en [BALIZA]:  TG: %d   SLOT: 2' % args.tg)

if __name__ == '__main__':
    main()

#!/bin/bash
# install-baliza.sh -- Instala la baliza ciclica de voz en un servidor
#                      hblink3 / FreeDMR / ADN-DMR-Peer-Server.
# Ejecutar EN el servidor destino:  sudo ./install-baliza.sh
set -e

DEST=/opt/hblink-baliza
SRC="$(cd "$(dirname "$0")" && pwd)"

echo ">> Instalando baliza en $DEST"
sudo mkdir -p "$DEST"
sudo cp "$SRC/baliza.py" "$SRC/tts_ambe.py" "$SRC/provision_master.py" "$SRC/README.md" "$DEST/"
sudo chmod +x "$DEST/baliza.py" "$DEST/tts_ambe.py" "$DEST/provision_master.py"

# --- Autodeteccion de la instalacion hblink/FreeDMR ---
HBP=""
for d in /opt/HBlink3 /opt/FreeDMR /opt/ADN-DMR-Peer-Server /opt/freedmr /opt/hblink3; do
  if [ -f "$d/hblink.py" ] && [ -f "$d/config.py" ]; then HBP="$d"; break; fi
done
if [ -z "$HBP" ]; then
  HBP="$(dirname "$(find /opt -maxdepth 3 -name hblink.py 2>/dev/null | head -1)")"
fi
[ -n "$HBP" ] && echo ">> Instalacion hblink detectada: $HBP" || echo "   (aviso) no detecte la instalacion hblink; pon HBLINK_PATH a mano en baliza.cfg"

# Detectar fork (HBlink3 abierto vs FreeDMR blindado)
FORK="hblink3"
if [ -n "$HBP" ] && grep -qiE "ALLOW_UNREG_ID|user_passwords|hotspot_proxy" "$HBP"/*.py 2>/dev/null; then
  FORK="freedmr"
fi
echo ">> Tipo de servidor: $FORK"

# Config: no pisar una existente
if [ -f "$DEST/baliza.cfg" ]; then
  echo ">> Conservo $DEST/baliza.cfg existente"
  sudo cp "$SRC/baliza.cfg.example" "$DEST/baliza.cfg.example"
else
  sudo cp "$SRC/baliza.cfg.example" "$DEST/baliza.cfg"
  [ -n "$HBP" ] && sudo sed -i -E "s|^HBLINK_PATH:.*|HBLINK_PATH: $HBP|" "$DEST/baliza.cfg"
  echo ">> Copiada plantilla -> $DEST/baliza.cfg  (EDITALA antes de arrancar)"
fi

# Audio pre-generado (modo portatil sin TTS local)
[ -f "$SRC/baliza.ambe" ] && sudo cp "$SRC/baliza.ambe" "$DEST/" && echo ">> Copiado audio pre-generado baliza.ambe"

# Servicio
sudo cp "$SRC/hblink-baliza.service" /etc/systemd/system/
sudo systemctl daemon-reload

echo
echo ">> Hecho. Conexion al master segun el tipo de servidor:"
if [ "$FORK" = "freedmr" ]; then
  cat <<EOF
   Servidor FreeDMR/ADN (blindado): el master principal exige IDs registrados y
   el proxy reserva localhost. NO conectes a su puerto directo. En su lugar, anade
   un master dedicado permisivo (seguro: backup + validacion + sin reescribir):

     sudo python3 $DEST/provision_master.py \\
          --cfg <config-del-master-en-uso> --port 62090 --pass <passbaliza> --tg <TG>
     sudo systemctl restart <servicio-del-master>     # corta unos seg a los hotspots

   Luego en $DEST/baliza.cfg [BEACON-PEER]: MASTER_PORT 62090, PASSPHRASE <passbaliza>.
EOF
else
  cat <<EOF
   Servidor HBlink3 (abierto): apunta el peer al master existente. En
   $DEST/baliza.cfg [BEACON-PEER]: MASTER_IP/MASTER_PORT/PASSPHRASE del master,
   RADIO_ID libre. (Si su master tuviera REG_ACL restrictivo, usa provision_master.py.)
EOF
fi
echo
echo "   Ajusta tambien [BALIZA]: TG / SLOT / INTERVAL_MIN / SRC_ID / MESSAGE"
echo "   Arranca:  sudo systemctl enable --now hblink-baliza ; journalctl -u hblink-baliza -f"

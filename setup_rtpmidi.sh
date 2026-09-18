#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# ================================================================
# setup_rtpmidi.sh -- expõe a porta MIDI virtual "ESP32 Drum" pela
# rede (RTP-MIDI/AppleMIDI, via rtpmidid: https://github.com/davidmoreno/rtpmidid),
# pra gravar no PC (Reaper + EZdrummer/Addictive Drums, ou qualquer
# outro programa que aceite MIDI) SEM mexer em nada do hardware do
# módulo -- só software, pela mesma rede que o módulo já usa.
#
# Opcional: só rode isso se quiser essa função (gravar no PC). Não é
# preciso pro uso normal ao vivo (com os celulares/tablets dos
# músicos) -- essa parte já funciona sem isso.
#
# AVISO: este script foi escrito com base na documentação oficial do
# rtpmidid (README do projeto), mas não foi possível testar contra um
# Raspberry Pi real neste momento -- então ele é cauteloso (avisa e
# para em vez de adivinhar) se algo não bater com o esperado. Se
# algum passo falhar, roda os comandos abaixo na mão e me avisa o que
# apareceu, que a gente ajusta.
#
# Uso:
#     sudo ./setup_rtpmidi.sh
# ================================================================

set -e

if [ "$(id -u)" -ne 0 ]; then
  echo "Rode este script com sudo: sudo ./setup_rtpmidi.sh"
  exit 1
fi

PORT_NAME="${DRUM_MIDI_PORT_NAME:-ESP32 Drum}"
NETWORK_PORT_NAME="RTP-MIDI Modulo Bateria"   # nome da porta ALSA local que o rtpmidid cria (ver default.ini abaixo)
SESSION_NAME="Modulo Bateria ($(hostname))"    # nome que aparece no Windows (rtpMIDI / Bonjour)

echo "==> Instalando rtpmidid..."
if apt-get install -y rtpmidid; then
  echo "rtpmidid instalado via apt."
else
  echo
  echo "O pacote 'rtpmidid' não está disponível no apt desta instalação"
  echo "(comum em versões mais antigas do Raspberry Pi OS -- o pacote é"
  echo "relativamente recente no Debian/Raspbian)."
  echo
  echo "Alternativa: baixar o .deb pronto direto do GitHub. Confira a"
  echo "versão mais recente em https://github.com/davidmoreno/rtpmidid/releases"
  echo "e (pro Raspberry Pi OS de 64 bits usado neste projeto, arquitetura arm64):"
  echo
  echo "    wget https://github.com/davidmoreno/rtpmidid/releases/download/vXX.YY/rtpmidid_XX.YY_arm64.deb"
  echo "    sudo dpkg -i rtpmidid_XX.YY_arm64.deb"
  echo "    sudo apt -f install -y     # resolve dependências que faltarem"
  echo
  echo "(troque vXX.YY/XX.YY pela versão real que aparecer na página de releases)"
  echo "Depois de instalado desse jeito, rode este script de novo -- ele pula"
  echo "direto pra configuração já que o pacote vai estar instalado."
  exit 1
fi

CONF="/etc/rtpmidid/default.ini"
mkdir -p /etc/rtpmidid
if [ -f "$CONF" ]; then
  cp "$CONF" "$CONF.bak.$(date +%s)"
  echo "Config existente salva como backup: $CONF.bak.*"
fi

# [rtpmidi_announce] name=... -- é o nome que aparece no Windows (rtpMIDI /
# Bonjour, na lista de sessões remotas).
# [alsa_announce] name=...    -- é o nome da porta ALSA LOCAL que o
# rtpmidid cria neste Raspberry -- é NELA que a gente vai conectar a porta
# "ESP32 Drum" (com aconnect), pra tudo que chegar nela seguir pra rede.
cat > "$CONF" <<EOF
# Gerado por setup_rtpmidi.sh
[general]
alsa_name=rtpmidid

[rtpmidi_announce]
name=${SESSION_NAME}
port=5004

[alsa_announce]
name=${NETWORK_PORT_NAME}
EOF

systemctl enable --now rtpmidid
echo "Aguardando o rtpmidid subir..."
sleep 2
systemctl --no-pager status rtpmidid || true

# ---- conecta "ESP32 Drum" -> porta de rede do rtpmidid via aconnect ----
#
# O mesmo problema que já existe pra conectar "ESP32 Drum" no DrumGizmo
# (ver _reconnect_midi em kit_manager.py) se repete aqui: cada vez que um
# dos dois serviços reinicia, o cliente ALSA-seq dele muda de id, entao a
# conexao manual anterior se perde. Por isso instala um servico pequeno,
# à parte, só pra manter essa conexão específica sempre viva -- não mexe
# em nada do que já existe pro drum-backend/DrumGizmo.
cat > /usr/local/bin/rtpmidid-connect-loop.sh <<'EOFSCRIPT'
#!/bin/bash
# Mantém "ESP32 Drum" conectado na porta de rede do rtpmidid, reconectando
# sozinho sempre que algum dos dois lados reiniciar (ver setup_rtpmidi.sh).
PORT_NAME="__PORT_NAME__"
NETWORK_PORT_NAME="__NETWORK_PORT_NAME__"
while true; do
  SRC=$(aconnect -l | awk -v p="$PORT_NAME" '
    /^client/ { cid=$2; gsub(":","",cid) }
    index($0, p) { print cid; exit }
  ')
  DST=$(aconnect -l | awk -v p="$NETWORK_PORT_NAME" '
    /^client/ { cid=$2; gsub(":","",cid) }
    index($0, p) { print cid; exit }
  ')
  if [ -n "$SRC" ] && [ -n "$DST" ]; then
    aconnect "${SRC}:0" "${DST}:0" 2>/dev/null || true
  fi
  sleep 5
done
EOFSCRIPT
sed -i "s#__PORT_NAME__#${PORT_NAME}#g; s#__NETWORK_PORT_NAME__#${NETWORK_PORT_NAME}#g" /usr/local/bin/rtpmidid-connect-loop.sh
chmod +x /usr/local/bin/rtpmidid-connect-loop.sh

cat > /etc/systemd/system/rtpmidid-connect.service <<EOF
[Unit]
Description=Conecta ESP32 Drum na porta de rede do rtpmidid (reconecta sozinho)
After=drum-backend.service rtpmidid.service
Wants=drum-backend.service rtpmidid.service

[Service]
ExecStart=/usr/local/bin/rtpmidid-connect-loop.sh
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now rtpmidid-connect

echo
echo "================================================================"
echo "Pronto (best-effort -- confira o resultado abaixo)."
echo
echo "Confira se a porta '${PORT_NAME}' apareceu e foi conectada na porta"
echo "'${NETWORK_PORT_NAME}':"
echo "    aconnect -l"
echo
echo "Se a conexão não aparecer sozinha em alguns segundos, veja o log:"
echo "    journalctl -u rtpmidid -f"
echo "    journalctl -u rtpmidid-connect -f"
echo
echo "No Windows: instale o driver 'rtpMIDI' de Tobias Erichsen"
echo "(https://www.tobias-erichsen.de/software/rtpmidi.html), abra o"
echo "programa -- este Raspberry deve aparecer sozinho em 'Remote Sessions'"
echo "como '${SESSION_NAME}' (via Bonjour/mDNS); clique 'Connect'. Se não"
echo "aparecer sozinho, adicione manualmente em 'My Sessions' com o IP"
echo "deste Raspberry e porta 5004."
echo
echo "Depois de conectado, qualquer DAW no Windows (ex: Reaper) vai ver"
echo "uma porta MIDI de entrada nova -- escolha ela na faixa do"
echo "EZdrummer/Addictive Drums."
echo "================================================================"

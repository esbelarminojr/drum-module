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
# TESTADO num Raspberry Pi 3B+ real (Raspberry Pi OS / Debian 13 "trixie",
# arm64, rtpmidid 26.01) em 2026-09-19: instalação via .deb manual (pacote
# não estava no apt dessa versão), conexão automática "ESP32 Drum" -> rede
# confirmada via `aconnect -l`. Ainda não testado ponta-a-ponta gravando de
# fato no Windows (driver rtpMIDI + DAW).
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
NETWORK_PORT_NAME="RTP-MIDI Modulo Bateria"   # nome que TENTAMOS dar à porta ALSA local via [alsa_announce] (ver default.ini abaixo) -- ver aviso abaixo
SESSION_NAME="Modulo Bateria ($(hostname))"    # nome que aparece no Windows (rtpMIDI / Bonjour)

# ATENÇÃO (confirmado testando num Raspberry real, rtpmidid 26.01): essa
# versão do rtpmidid NÃO está aplicando o "[alsa_announce] name=..." --
# a porta local sai sempre com o nome padrão dele, "Network Export". O
# nome do CLIENT ALSA, esse sim, respeita o "[general] alsa_name=..." lá
# embaixo (confirmado via `aconnect -l`: aparece "client N: 'rtpmidid'").
# Por isso o script de auto-conexão (mais abaixo) procura pelo NOME DO
# CLIENT ("rtpmidid"), e não pelo nome da porta -- assim funciona mesmo
# se essa versão nunca vier a respeitar o "alsa_announce".
NETWORK_MATCH_NAME="rtpmidid"

# `systemctl restart` às vezes não reinicia de fato quando chamado de
# dentro deste script logo em seguida de outra chamada systemctl (visto
# num teste real: o comando retorna sucesso mas o serviço continua com o
# PID/timestamp antigos). Essa função confere se o timestamp mudou e, se
# não mudou, força um stop+start explícito como reforço.
restart_confirmando() {
  local unit="$1"
  local antes depois
  antes=$(systemctl show "$unit" -p ActiveEnterTimestamp --value 2>/dev/null || true)
  systemctl restart "$unit"
  sleep 1
  depois=$(systemctl show "$unit" -p ActiveEnterTimestamp --value 2>/dev/null || true)
  if [ "$antes" = "$depois" ]; then
    systemctl stop "$unit" || true
    sleep 1
    systemctl start "$unit"
  fi
}

mostrar_instrucoes_manuais() {
  echo
  echo "Baixe o .deb manualmente em:"
  echo "    https://github.com/davidmoreno/rtpmidid/releases"
  echo "Escolha o arquivo pro seu Debian/arquitetura (ex:"
  echo "rtpmidid-debian-<codinome>-<arquitetura>-<versao>.deb) e instale com:"
  echo
  echo "    wget <link-do-arquivo-escolhido>"
  echo "    sudo dpkg -i <arquivo>.deb"
  echo "    sudo apt -f install -y     # resolve dependências que faltarem"
  echo
  echo "Depois de instalado desse jeito, rode este script de novo -- ele pula"
  echo "direto pra configuração já que o pacote vai estar instalado."
}

# Tenta baixar e instalar sozinho o .deb certo pra este sistema, direto dos
# releases do GitHub -- evita ter que ficar catando manualmente qual arquivo
# baixar (foi um processo bem manual/demorado a primeira vez que isso foi
# testado). Só usa isso como ultimo recurso, se o apt nao tiver o pacote.
instalar_rtpmidid_via_deb_github() {
  command -v curl >/dev/null 2>&1 || apt-get install -y curl || true
  if ! command -v curl >/dev/null 2>&1; then
    echo "'curl' não disponível e não foi possível instalar -- não dá pra"
    echo "baixar automaticamente."
    mostrar_instrucoes_manuais
    return 1
  fi

  local arch codename api_url asset_url tmp_deb
  arch=$(dpkg --print-architecture)
  codename=$(. /etc/os-release; echo "$VERSION_CODENAME")

  if [ -z "$codename" ]; then
    echo "Não consegui identificar o codinome do seu Debian/Raspberry Pi OS"
    echo "(VERSION_CODENAME vazio em /etc/os-release)."
    mostrar_instrucoes_manuais
    return 1
  fi

  echo "Procurando um pacote pra Debian '${codename}' (${arch}) nos releases..."
  api_url="https://api.github.com/repos/davidmoreno/rtpmidid/releases/latest"
  asset_url=$(curl -fsSL "$api_url" 2>/dev/null \
    | grep -o "\"browser_download_url\": *\"[^\"]*\"" \
    | sed -E 's/^"browser_download_url": *"(.*)"$/\1/' \
    | grep -i "debian-${codename}-${arch}" \
    | head -n1)

  if [ -z "$asset_url" ]; then
    echo "Não achei um .deb pra 'debian-${codename}-${arch}' nos releases do"
    echo "rtpmidid (pode ser uma combinação de sistema/arquitetura ainda sem"
    echo "pacote pronto, ex: Raspberry Pi OS de 32 bits)."
    mostrar_instrucoes_manuais
    return 1
  fi

  echo "Achei: $asset_url"
  tmp_deb=$(mktemp --suffix=.deb)
  if curl -fsSL -o "$tmp_deb" "$asset_url" && dpkg -i "$tmp_deb" && apt-get -f install -y; then
    echo "rtpmidid instalado via .deb baixado automaticamente do GitHub."
    rm -f "$tmp_deb"
    return 0
  else
    echo "Baixei o arquivo mas a instalação falhou (dpkg/apt) -- veja o erro"
    echo "acima. Instruções manuais como alternativa:"
    mostrar_instrucoes_manuais
    rm -f "$tmp_deb"
    return 1
  fi
}

echo "==> Instalando rtpmidid..."
if apt-get install -y rtpmidid; then
  echo "rtpmidid instalado via apt."
else
  echo
  echo "O pacote 'rtpmidid' não está disponível no apt desta instalação"
  echo "(comum em versões mais antigas do Raspberry Pi OS -- o pacote é"
  echo "relativamente recente no Debian/Raspbian). Tentando baixar o .deb"
  echo "certo automaticamente do GitHub..."
  if ! instalar_rtpmidid_via_deb_github; then
    exit 1
  fi
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

systemctl enable rtpmidid
restart_confirmando rtpmidid   # garante que aplica o .ini mesmo se o servico ja estava rodando de uma execucao anterior
echo "Aguardando o rtpmidid subir..."
sleep 2
systemctl --no-pager status rtpmidid || true

# ---- conecta "ESP32 Drum" -> porta(s) de rede do rtpmidid via aconnect ----
#
# O mesmo problema que já existe pra conectar "ESP32 Drum" no DrumGizmo
# (ver _reconnect_midi em kit_manager.py) se repete aqui: cada vez que um
# dos dois serviços reinicia, o cliente ALSA-seq dele muda de id, entao a
# conexao manual anterior se perde. Por isso instala um servico pequeno,
# à parte, só pra manter essa conexão específica sempre viva -- não mexe
# em nada do que já existe pro drum-backend/DrumGizmo.
#
# IMPORTANTE (descoberto testando com um Windows real conectado via
# rtpMIDI): a porta "Network Export" (porta 0 do client rtpmidid) NÃO leva
# a nota pra rede de verdade -- é só uma porta genérica/administrativa. O
# rtpmidid cria, DINAMICAMENTE, uma porta própria pra CADA sessão remota
# conectada (nomeada com o nome do computador remoto, ex: "DESKTOP-XYZ"),
# e é NESSA porta por-sessão que a gente precisa conectar o "ESP32 Drum"
# pra a nota realmente sair pela rede. Só que o rtpmidid cria DUAS portas
# com esse mesmo nome por sessão (aparentemente um par de/para) -- conectar
# nas duas faz cada nota chegar duplicada do outro lado. Por isso o loop
# abaixo pula a porta 0 e conecta só UMA vez por nome de porta encontrado
# (a primeira), cobrindo também o caso de mais de um músico gravando ao
# mesmo tempo (uma sessão por pessoa).
cat > /usr/local/bin/rtpmidid-connect-loop.sh <<'EOFSCRIPT'
#!/bin/bash
# Mantém "ESP32 Drum" conectado na(s) porta(s) de rede do rtpmidid,
# reconectando sozinho sempre que algum dos dois lados reiniciar ou uma
# nova sessão remota se conectar (ver setup_rtpmidi.sh).
PORT_NAME="__PORT_NAME__"
RTPMIDID_CLIENT_NAME="__NETWORK_PORT_NAME__"
while true; do
  SRC=$(aconnect -l | awk -v p="$PORT_NAME" '
    /^client/ { cid=$2; gsub(":","",cid) }
    index($0, p) { print cid; exit }
  ')
  RTPCID=$(aconnect -l | awk -v p="$RTPMIDID_CLIENT_NAME" '
    /^client/ { cid=$2; gsub(":","",cid) }
    index($0, p) { print cid; exit }
  ')
  if [ -n "$SRC" ] && [ -n "$RTPCID" ]; then
    aconnect -l | awk -v cid="$RTPCID" '
      $0 ~ "^client " cid ":" { inclient=1; next }
      /^client/ { inclient=0 }
      inclient && /^    [0-9]+ / {
        line=$0
        sub(/^ +/, "", line)
        split(line, parts, " ")
        port=parts[1]
        name=line
        sub(/^[0-9]+ +/, "", name)
        if (port != "0" && !(name in seen)) {
          seen[name]=1
          print port
        }
      }
    ' | while read -r p; do
      aconnect "${SRC}:0" "${RTPCID}:${p}" 2>/dev/null || true
    done
  fi
  sleep 5
done
EOFSCRIPT
sed -i "s#__PORT_NAME__#${PORT_NAME}#g; s#__NETWORK_PORT_NAME__#${NETWORK_MATCH_NAME}#g" /usr/local/bin/rtpmidid-connect-loop.sh
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
systemctl enable rtpmidid-connect
restart_confirmando rtpmidid-connect   # garante que aplica o script novo mesmo se o servico ja estava rodando de uma execucao anterior

echo
echo "================================================================"
echo "Pronto (best-effort -- confira o resultado abaixo)."
echo
echo "Confira se a porta '${PORT_NAME}' apareceu e foi conectada no client"
echo "'${NETWORK_MATCH_NAME}' (a porta de rede do rtpmidid pode aparecer com"
echo "o nome padrão 'Network Export', dependendo da versão -- o que importa"
echo "é o client 'rtpmidid'):"
echo "    aconnect -l"
echo
echo "Se a conexão não aparecer sozinha em alguns segundos, veja o log:"
echo "    journalctl -u rtpmidid -f"
echo "    journalctl -u rtpmidid-connect -f"
echo
echo "No Windows:"
echo "  1) Instale o driver 'rtpMIDI' de Tobias Erichsen:"
echo "     https://www.tobias-erichsen.de/software/rtpmidi.html"
echo "  2) O rtpMIDI depende do serviço 'Bonjour' (da Apple) pra descobrir"
echo "     o Raspberry sozinho na rede. Se, ao marcar 'Enabled' numa sessão"
echo "     em 'My Sessions', aparecer o erro 'Bonjour-service-creation"
echo "     failed', instale o Bonjour Print Services (grátis):"
echo "     https://support.apple.com/en-us/106380 -- e reinicie o Windows."
echo "  3) Abra o rtpMIDI, marque 'Enabled' numa sessão em 'My Sessions'"
echo "     (precisa disso ANTES de conseguir conectar em qualquer outra)."
echo "  4) Este Raspberry deve aparecer sozinho na lista 'Directory', com"
echo "     o nome do hostname dele (ex: '$(hostname)') -- selecione e"
echo "     clique 'Connect'. Se não aparecer sozinho, adicione manualmente"
echo "     pelo '+' da lista Directory, com o IP deste Raspberry e porta"
echo "     5004."
echo
echo "Depois de conectado, qualquer DAW no Windows (ex: Reaper) vai ver"
echo "uma porta MIDI de entrada nova -- escolha ela na faixa do"
echo "EZdrummer/Addictive Drums. Pra testar rápido sem DAW nenhum, use o"
echo "midi_monitor.ps1 (nesta mesma pasta do projeto) no PowerShell do"
echo "Windows."
echo "================================================================"

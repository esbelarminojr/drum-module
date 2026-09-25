#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# ================================================================
# setup_lcd_panel.sh -- instala as dependências e registra o serviço
# systemd do painel físico (display LCD 16x2 + 3 botões de navegação).
#
# IMPORTANTE: isso NÃO mexe no botão de liga/desliga (GPIO24) -- esse
# continua sendo tratado à parte pelo setup_shutdown_button.sh, porque
# funciona por um mecanismo completamente diferente (dtoverlay do
# kernel, não um script Python rodando).
#
# Fiação -- ver README.md pra tabela completa com os pinos físicos.
# Resumo (numeração BCM):
#   Display (modo 4 bits): RS=GPIO5  E=GPIO6  D4=GPIO13  D5=GPIO19
#                          D6=GPIO26 D7=GPIO18  (RW -> GND direto)
#   Botões (outra perna no GND): Anterior=GPIO17  OK=GPIO27  Próximo=GPIO23
#
# Uso:
#     sudo ./setup_lcd_panel.sh
# ================================================================

set -e

if [ "$(id -u)" -ne 0 ]; then
  echo "Rode este script com sudo: sudo ./setup_lcd_panel.sh"
  exit 1
fi

DRUM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

MISSING=0
for f in lcd_panel.py lcd-panel.service.template; do
  if [ ! -f "$DRUM_DIR/$f" ]; then
    echo "AVISO: não achei '$f' em $DRUM_DIR"
    MISSING=1
  fi
done
if [ "$MISSING" = "1" ]; then
  echo "Confere se todos os arquivos do projeto estão na mesma pasta que este script."
  exit 1
fi

# ---- descobre qual usuário deve rodar o painel (mesma lógica do install.sh) ----
if [ -n "$1" ]; then
  DRUM_USER="$1"
elif [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
  DRUM_USER="$SUDO_USER"
elif [ -f "$DRUM_DIR/drum_backend.py" ]; then
  DRUM_USER="$(stat -c '%U' "$DRUM_DIR/drum_backend.py")"
fi

if [ -z "$DRUM_USER" ] || [ "$DRUM_USER" = "root" ]; then
  echo "Não consegui descobrir sozinho qual usuário deve rodar o painel."
  echo "Rode de novo assim: sudo ./setup_lcd_panel.sh <usuario>"
  exit 1
fi

if ! id "$DRUM_USER" >/dev/null 2>&1; then
  echo "O usuário '$DRUM_USER' não existe nesta máquina."
  exit 1
fi

DRUM_GROUP="$(id -gn "$DRUM_USER")"

echo "Usuário detectado: $DRUM_USER"
echo

# ---- dependência de sistema (pacote compilado contra o kernel certo,
# melhor que instalar RPi.GPIO via pip) ----
echo "Instalando python3-rpi.gpio via apt..."
apt-get update -qq
apt-get install -y python3-rpi.gpio

# ---- garante que o usuário está no grupo gpio (necessário pra acessar
# /dev/gpiomem sem ser root) ----
if ! id -nG "$DRUM_USER" | grep -qw gpio; then
  echo "Adicionando '$DRUM_USER' ao grupo 'gpio'..."
  usermod -aG gpio "$DRUM_USER"
  echo "(precisa de um logout/login ou reboot pra isso valer de fato)"
fi

# ---- dependências Python (não empacotadas no apt) ----
echo "Instalando RPLCD e websocket-client via pip..."
pip install RPLCD websocket-client --break-system-packages

# ---- gera e ativa o serviço ----
sed \
  -e "s#__DRUM_USER__#$DRUM_USER#g" \
  -e "s#__DRUM_GROUP__#$DRUM_GROUP#g" \
  -e "s#__DRUM_DIR__#$DRUM_DIR#g" \
  "$DRUM_DIR/lcd-panel.service.template" > /etc/systemd/system/lcd-panel.service

echo "Gravado /etc/systemd/system/lcd-panel.service"

systemctl daemon-reload
systemctl enable --now lcd-panel

echo
echo "================================================================"
echo "Pronto. Confira com:"
echo "  systemctl status lcd-panel --no-pager"
echo "  journalctl -u lcd-panel -f"
echo
echo "Se aparecer erro de permissão no GPIO logo depois de instalar,"
echo "é porque o usuário acabou de entrar no grupo 'gpio' agora --"
echo "precisa de 'sudo reboot' uma vez pra valer."
echo "================================================================"

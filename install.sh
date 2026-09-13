#!/bin/bash
# ================================================================
# install.sh -- instala/atualiza o drum-backend.service detectando
# sozinho o usuário e a pasta certos, em vez de vir com "ju" fixo.
#
# Uso normal (rodando de dentro da pasta onde estão os arquivos,
# como o próprio usuário dono, ex: ju):
#
#     sudo ./install.sh
#
# Se quiser forçar um usuário específico (em vez de detectar sozinho):
#
#     sudo ./install.sh nome_do_usuario
# ================================================================

set -e

if [ "$(id -u)" -ne 0 ]; then
  echo "Rode este script com sudo: sudo ./install.sh"
  exit 1
fi

DRUM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# ---- descobre qual usuário deve rodar o backend ----
# ordem de preferência:
#   1) nome passado como argumento (força manual)
#   2) SUDO_USER (quem chamou "sudo ./install.sh")
#   3) dono do arquivo drum_backend.py na pasta deste script
if [ -n "$1" ]; then
  DRUM_USER="$1"
elif [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
  DRUM_USER="$SUDO_USER"
elif [ -f "$DRUM_DIR/drum_backend.py" ]; then
  DRUM_USER="$(stat -c '%U' "$DRUM_DIR/drum_backend.py")"
fi

if [ -z "$DRUM_USER" ] || [ "$DRUM_USER" = "root" ]; then
  echo "Não consegui descobrir sozinho qual usuário deve rodar o backend."
  echo "Rode de novo assim: sudo ./install.sh <usuario>"
  exit 1
fi

if ! id "$DRUM_USER" >/dev/null 2>&1; then
  echo "O usuário '$DRUM_USER' não existe nesta máquina."
  exit 1
fi

DRUM_GROUP="$(id -gn "$DRUM_USER")"
DRUM_HOME="$(getent passwd "$DRUM_USER" | cut -d: -f6)"

echo "Usuário detectado:     $DRUM_USER"
echo "Grupo:                 $DRUM_GROUP"
echo "Pasta pessoal:          $DRUM_HOME"
echo "Pasta dos arquivos:     $DRUM_DIR"
echo

MISSING=0
for f in drum_backend.py kit_manager.py kits.json drum-module-console.html drum-backend.service.template; do
  if [ ! -f "$DRUM_DIR/$f" ]; then
    echo "AVISO: não achei '$f' em $DRUM_DIR"
    MISSING=1
  fi
done
if [ "$MISSING" = "1" ]; then
  echo "Confere se todos os arquivos do backend estão na mesma pasta que este install.sh."
  exit 1
fi

# ---- gera o .service a partir do template, com os valores detectados ----
sed \
  -e "s#__DRUM_USER__#$DRUM_USER#g" \
  -e "s#__DRUM_GROUP__#$DRUM_GROUP#g" \
  -e "s#__DRUM_DIR__#$DRUM_DIR#g" \
  "$DRUM_DIR/drum-backend.service.template" > /etc/systemd/system/drum-backend.service

echo "Gravado /etc/systemd/system/drum-backend.service"

systemctl daemon-reload
systemctl enable --now drum-backend

echo
echo "Pronto. Confira com:"
echo "  systemctl status drum-backend --no-pager"
echo "  journalctl -u drum-backend -f"

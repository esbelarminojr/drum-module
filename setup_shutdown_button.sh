#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# ================================================================
# setup_shutdown_button.sh -- habilita um botão físico de liga/
# desliga seguro no Raspberry, usando o recurso "gpio-shutdown" já
# embutido no Raspberry Pi OS (não precisa de nenhum programa/serviço
# extra rodando).
#
# Fiação (fazer isso na hora de montar a caixa física do módulo):
#   - um botão simples (2 fios) entre o pino GPIO3 (pino físico 5 na
#     régua de 40 pinos) e um pino de GND vizinho (ex: pino físico 6,
#     bem ao lado).
#
# Depois de rodar este script + religar uma vez: apertar o botão
# desliga o sistema com segurança (equivalente a 'sudo shutdown -h
# now') -- e, como é a versão Lite (sem ambiente gráfico), desliga
# direto, sem tela de confirmação. O mesmo botão também LIGA o
# Raspberry de volta se apertado enquanto ele estiver desligado
# (propriedade do próprio pino GPIO3) -- na prática vira um botão
# único de liga/desliga do módulo.
#
# IMPORTANTE: isso desliga o SISTEMA OPERACIONAL com segurança, mas
# não corta a energia física -- ainda precisa tirar da tomada (ou usar
# uma tomada com interruptor) depois, só que aí sem risco de corromper
# nada (esperar o LED verde parar de piscar antes).
#
# Seguro rodar mesmo antes de ligar o botão de verdade -- sem nada
# ligado ao GPIO3, o pino fica só "flutuando puxado pra cima" e nunca
# aciona sozinho.
#
# Uso:
#     sudo ./setup_shutdown_button.sh
# ================================================================

set -e

if [ "$(id -u)" -ne 0 ]; then
  echo "Rode este script com sudo: sudo ./setup_shutdown_button.sh"
  exit 1
fi

# Raspberry Pi OS Bookworm (o usado neste projeto) usa /boot/firmware/config.txt;
# versões mais antigas (Bullseye e anteriores) usam /boot/config.txt direto.
# Confere os dois, na ordem, e usa o primeiro que existir.
if [ -f /boot/firmware/config.txt ]; then
  CONFIG_TXT=/boot/firmware/config.txt
elif [ -f /boot/config.txt ]; then
  CONFIG_TXT=/boot/config.txt
else
  echo "ERRO: não achei nem /boot/firmware/config.txt nem /boot/config.txt --"
  echo "isso não parece um Raspberry Pi OS. Abortando."
  exit 1
fi

echo "Usando: $CONFIG_TXT"

LINE="dtoverlay=gpio-shutdown"

if grep -q "^${LINE}$" "$CONFIG_TXT"; then
  echo "Já está configurado (linha '${LINE}' já existe em $CONFIG_TXT) -- nada a fazer."
else
  cp "$CONFIG_TXT" "$CONFIG_TXT.bak.$(date +%s)"
  echo "" >> "$CONFIG_TXT"
  echo "# Adicionado por setup_shutdown_button.sh -- botão de liga/desliga seguro" >> "$CONFIG_TXT"
  echo "$LINE" >> "$CONFIG_TXT"
  echo "Adicionado '$LINE' em $CONFIG_TXT (backup salvo como $CONFIG_TXT.bak.*)"
fi

echo
echo "================================================================"
echo "Pronto. PRECISA REINICIAR pra valer:"
echo "    sudo reboot"
echo
echo "Fiação: um botão simples entre o pino físico 5 (GPIO3) e o pino"
echo "físico 6 (GND, bem ao lado dele) na régua de 40 pinos."
echo
echo "Depois de reiniciar com o botão ligado: apertar desliga com"
echo "segurança; apertar de novo com o Raspberry desligado, liga."
echo
echo "Se não funcionar direto nesta versão do Raspberry Pi OS, o"
echo "relatado por outros usuários (não confirmado neste projeto) é"
echo "que pode precisar habilitar 'Remote GPIO' em"
echo "'sudo raspi-config' -> Interface Options -- só mexa nisso se o"
echo "botão realmente não funcionar depois do reboot."
echo "================================================================"

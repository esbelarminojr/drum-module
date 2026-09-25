#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
lcd_panel.py -- Painel físico (display LCD 16x2 + 3 botões) pra navegar
e carregar kits de bateria sem precisar abrir o console web.

Pinagem (BCM -- ver README.md pra tabela com os pinos físicos do
header de 40 vias):

    Display (modo 4 bits):
        RS = GPIO5
        E  = GPIO6
        D4 = GPIO13
        D5 = GPIO19
        D6 = GPIO26
        D7 = GPIO18
        RW -> GND direto (fixo, nunca ligado num GPIO -- só escrevemos)

    Botões (a outra perna de cada um vai no GND -- pull-up interno,
    não precisa de resistor externo):
        Anterior (◄) = GPIO17
        OK           = GPIO27
        Próximo (►)  = GPIO23

Dependências (ver README.md pra instruções completas):
    sudo apt install -y python3-rpi.gpio
    pip install RPLCD websocket-client --break-system-packages

Comportamento: espelha o comportamento de navegação do console web --
Anterior/Próximo só passeiam pela lista de kits (não carregam nada
sozinhos, igual os botões #kit-prev/#kit-next do HTML), e só o OK de
fato manda carregar (igual o botão "Carregar kit"). O progresso de
carregamento chega pelo mesmo WebSocket que o HTML usa (/ws), então
aparece igual nos dois lugares ao mesmo tempo -- inclusive se alguém
trocar de kit pelo celular enquanto esse painel está ligado, o painel
acompanha sozinho (não precisa reiniciar nada).
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import RPi.GPIO as GPIO
from RPLCD.gpio import CharLCD
import websocket  # pacote "websocket-client"


# ================================================================
# CONFIGURAÇÃO
# ================================================================

HOST = "127.0.0.1"
HTTP_PORT = 8000
BASE_URL = f"http://{HOST}:{HTTP_PORT}"
WS_URL = f"ws://{HOST}:{HTTP_PORT}/ws"

PIN_RS = 5
PIN_E = 6
PIN_D4 = 13
PIN_D5 = 19
PIN_D6 = 26
PIN_D7 = 18

PIN_BTN_ANTERIOR = 17
PIN_BTN_OK = 27
PIN_BTN_PROXIMO = 23

DEBOUNCE_S = 0.25   # ignora novos cliques por esse tempo depois de um clique
POLL_S = 0.03       # intervalo de checagem dos botões
REFRESH_S = 0.2     # intervalo de atualização da tela fora de eventos
STATUS_MSG_S = 2.0  # quanto tempo "Carregado!"/"Falhou!" fica na tela


# ================================================================
# ESTADO COMPARTILHADO (lido pelo loop principal, escrito pela
# thread do WebSocket -- por isso o lock)
# ================================================================

_lock = threading.Lock()
state = {
    "kits": [],            # lista de nomes, mesma ordem de list_kits()
    "active": None,         # nome do kit realmente carregado agora
    "browse_index": 0,      # posição sendo navegada (não é o carregado necessariamente)
    "loading": False,
    "loading_kit": None,
    "loading_current": 0,
    "loading_total": 0,
    "last_status_msg": None,   # "Carregado!" / "Falhou!"
    "last_status_ts": 0.0,
    "ws_connected": False,
}


def _get_json(path):
    with urllib.request.urlopen(BASE_URL + path, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(path):
    req = urllib.request.Request(
        BASE_URL + path, method="POST", data=b"{}",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def carregar_lista_kits():
    """Busca a lista de kits + qual está ativo agora. Chamado no início
    e continua sendo tentado de novo sozinho se o backend ainda não
    tiver subido (ver loop principal)."""
    try:
        info = _get_json("/api/kits")
    except Exception as e:
        print(f"[lcd_panel] não consegui buscar /api/kits ainda: {e}")
        return False

    with _lock:
        state["kits"] = info.get("kits", [])
        ativo = info.get("active")
        state["active"] = ativo
        if ativo in state["kits"]:
            state["browse_index"] = state["kits"].index(ativo)
    return True


# ================================================================
# WEBSOCKET (progresso de carregamento em tempo real)
# ================================================================

def _on_ws_message(ws, message):
    try:
        msg = json.loads(message)
    except Exception:
        return

    tipo = msg.get("type")
    with _lock:
        if tipo == "kit":
            # alguém (este painel ou o console web) iniciou a troca de kit
            state["loading"] = True
            state["loading_kit"] = msg.get("kit")
            state["loading_current"] = 0
            state["loading_total"] = 0
        elif tipo == "kit_progress":
            state["loading_kit"] = msg.get("kit")
            state["loading_current"] = msg.get("current", 0)
            state["loading_total"] = msg.get("total", 0)
        elif tipo == "kit_status":
            state["loading"] = False
            ok = bool(msg.get("ok"))
            if ok:
                state["active"] = msg.get("kit")
                if state["active"] in state["kits"]:
                    state["browse_index"] = state["kits"].index(state["active"])
            state["last_status_msg"] = "Carregado!" if ok else "Falhou!"
            state["last_status_ts"] = time.time()


def _on_ws_open(ws):
    with _lock:
        state["ws_connected"] = True
    print("[lcd_panel] conectado ao WebSocket do backend.")
    # o backend pode ter trocado de kit enquanto este painel estava
    # desconectado -- revalida a lista/ativo assim que a conexão volta
    carregar_lista_kits()


def _on_ws_close(ws, code, msg):
    with _lock:
        state["ws_connected"] = False
    print("[lcd_panel] WebSocket caiu, tentando de novo...")


def _on_ws_error(ws, error):
    pass  # o run_forever já reconecta sozinho por fora; evita poluir o log


def ws_loop():
    while True:
        try:
            ws = websocket.WebSocketApp(
                WS_URL,
                on_open=_on_ws_open,
                on_message=_on_ws_message,
                on_close=_on_ws_close,
                on_error=_on_ws_error,
            )
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e:
            print(f"[lcd_panel] erro no WebSocket: {e}")
        time.sleep(2)  # espera antes de tentar reconectar


# ================================================================
# DISPLAY
# ================================================================

def criar_lcd():
    return CharLCD(
        pin_rs=PIN_RS, pin_rw=None, pin_e=PIN_E,
        pins_data=[PIN_D4, PIN_D5, PIN_D6, PIN_D7],
        numbering_mode=GPIO.BCM, cols=16, rows=2,
        auto_linebreaks=False,
    )


def _linha(texto, largura=16):
    texto = texto[:largura]
    return texto + (" " * (largura - len(texto)))


def desenhar(lcd):
    with _lock:
        kits = list(state["kits"])
        browse_index = state["browse_index"]
        active = state["active"]
        loading = state["loading"]
        loading_kit = state["loading_kit"]
        loading_current = state["loading_current"]
        loading_total = state["loading_total"]
        last_status_msg = state["last_status_msg"]
        last_status_ts = state["last_status_ts"]
        ws_connected = state["ws_connected"]

    if last_status_msg and (time.time() - last_status_ts) < STATUS_MSG_S:
        # mensagem passageira de resultado (Carregado!/Falhou!)
        linha1 = _linha(last_status_msg)
        linha2 = _linha(loading_kit or "")
    elif loading:
        nome = loading_kit or ""
        linha1 = _linha("Carregando...")
        if loading_total > 0:
            pct = int(loading_current * 100 / loading_total)
            linha2 = _linha(f"{nome} {pct}%")
        else:
            linha2 = _linha(nome)
    elif not ws_connected:
        linha1 = _linha("Conectando...")
        linha2 = _linha("(backend)")
    elif not kits:
        linha1 = _linha("Nenhum kit")
        linha2 = _linha("encontrado")
    else:
        nome = kits[browse_index % len(kits)]
        eh_ativo = (nome == active)
        linha1 = _linha(nome + (" *" if eh_ativo else ""))
        linha2 = _linha("(ativo)" if eh_ativo else "OK: carregar")

    lcd.cursor_pos = (0, 0)
    lcd.write_string(linha1)
    lcd.cursor_pos = (1, 0)
    lcd.write_string(linha2)


# ================================================================
# BOTÕES
# ================================================================

def configurar_botoes():
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    for pino in (PIN_BTN_ANTERIOR, PIN_BTN_OK, PIN_BTN_PROXIMO):
        GPIO.setup(pino, GPIO.IN, pull_up_down=GPIO.PUD_UP)


def _borda_de_descida(pino, ultimo_estado):
    """Detecta transição solto (HIGH) -> pressionado (LOW).
    Devolve (aconteceu_agora, novo_estado)."""
    atual = GPIO.input(pino)
    aconteceu = (ultimo_estado == GPIO.HIGH and atual == GPIO.LOW)
    return aconteceu, atual


def acao_anterior():
    with _lock:
        if not state["kits"]:
            return
        state["browse_index"] = (state["browse_index"] - 1) % len(state["kits"])


def acao_proximo():
    with _lock:
        if not state["kits"]:
            return
        state["browse_index"] = (state["browse_index"] + 1) % len(state["kits"])


def acao_ok():
    with _lock:
        if not state["kits"] or state["loading"]:
            return
        nome = state["kits"][state["browse_index"] % len(state["kits"])]
        if nome == state["active"]:
            return  # já é o kit ativo -- igual o HTML esconde "Carregar kit" nesse caso
    try:
        _post(f"/api/kits/{urllib.parse.quote(nome, safe='')}/load")
    except Exception as e:
        print(f"[lcd_panel] falha ao pedir carregamento de '{nome}': {e}")


# ================================================================
# LOOP PRINCIPAL
# ================================================================

def main():
    configurar_botoes()
    lcd = criar_lcd()

    threading.Thread(target=ws_loop, daemon=True).start()

    # primeira tentativa -- se o backend ainda não subiu, a tela mostra
    # "Conectando..." e o loop abaixo segue tentando sozinho até conseguir
    carregar_lista_kits()

    estados = {
        PIN_BTN_ANTERIOR: GPIO.HIGH,
        PIN_BTN_OK: GPIO.HIGH,
        PIN_BTN_PROXIMO: GPIO.HIGH,
    }
    ultimo_clique = 0.0
    ultima_atualizacao_tela = 0.0
    ultima_tentativa_lista = 0.0

    try:
        while True:
            agora = time.time()

            with _lock:
                tem_kits = bool(state["kits"])
            if not tem_kits and (agora - ultima_tentativa_lista) > 3.0:
                carregar_lista_kits()
                ultima_tentativa_lista = agora

            if (agora - ultimo_clique) > DEBOUNCE_S:
                for pino, acao in (
                    (PIN_BTN_ANTERIOR, acao_anterior),
                    (PIN_BTN_OK, acao_ok),
                    (PIN_BTN_PROXIMO, acao_proximo),
                ):
                    aconteceu, novo_estado = _borda_de_descida(pino, estados[pino])
                    estados[pino] = novo_estado
                    if aconteceu:
                        acao()
                        ultimo_clique = agora
                        break  # só processa um botão por vez, mesmo se dois "colidirem"

            if (agora - ultima_atualizacao_tela) > REFRESH_S:
                desenhar(lcd)
                ultima_atualizacao_tela = agora

            time.sleep(POLL_S)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            lcd.close(clear=True)
        except Exception:
            pass
        GPIO.cleanup()


if __name__ == "__main__":
    main()

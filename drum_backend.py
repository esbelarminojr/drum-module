# SPDX-License-Identifier: GPL-3.0-or-later
"""
================================================================
 DRUM BACKEND -- ponte ESP32 <-> DrumGizmo <-> Console web (HTML)
================================================================

Este arquivo substitui o antigo serial_midi.py. Ele preserva
exatamente a mesma lógica de leitura serial / MIDI / SysEx que já
estava validada (batida chega, vira MIDI, DrumGizmo toca), e troca
o terminal interativo (thread_terminal) por um servidor web:

  ESP32 (ttyACM0)
     |
     |-- bytes MIDI reais (0x80/0x90/...) --> porta ALSA virtual
     |                                        "ESP32 Drum" --> DrumGizmo
     |
     `-- SysEx (F0 7D 'D' 'R' texto F7) ------ protocolo de config
                                                (GET/SET/GLOBAL/HH/...)
                                                       |
                                                       v
                                              este processo Python
                                                       |
                                        REST + WebSocket (Flask)
                                                       |
                                                       v
                                        drum-module-console.html
                                     (aberto em qualquer navegador
                                      na mesma rede do Raspberry)

------------------------------------------------------------------
INSTALAÇÃO (no Raspberry):

    pip install pyserial python-rtmidi flask flask-sock

EXECUÇÃO:

    python3 drum_backend.py

Depois, em qualquer celular/tablet/computador na mesma rede Wi-Fi
do Raspberry, abra:

    http://<ip-do-raspberry>:8000/

------------------------------------------------------------------
PROTOCOLO (igual ao já usado no serial_midi.py / bateria.ino):

Comandos que este processo manda pro ESP32 (texto, uma linha):
    PING
    GET
    SAVE
    RESET
    PAD <idx> <nota> <threshold> <velmax> <lockout> <ativo> <curva>
        -- <idx> aqui é o índice FÍSICO do firmware (0-7, ver
           PAD_ID_TO_FIRMWARE_IDX), já traduzido a partir do id que o
           console web usa -- não confundir os dois.
    GLOBAL <peak> <minvel> <duration> <canal>
    HH <ativo> <tipo> <aberto> <fechado> <filtro> <invertido> [<limiarMeioAberto> <limiarFechado>]
    HHSW <ativo> <invertido> <forcar_fechado>
    CAL <idx>              -- <idx> aqui também é o índice FÍSICO do firmware,
                              já traduzido (ver /api/cal/<idx>/start) -- liga o
                              streaming ao vivo do piezo desse pad (telemetria
                              "CAL,..." abaixo, contínua até um CALOFF)
    CALOFF                 -- desliga o streaming (de qualquer pad)
    HHSTATUS

Respostas do ESP32 (sempre chegam encapsuladas em SysEx):
    PONG
    BEGINCONFIG / ENDCONFIG
    PAD,<idx>,<nota>,<threshold>,<velmax>,<lockout>,<ativo>,<curva>
    GLOBAL,<peak>,<minvel>,<duration>,<canal>
    HH,<ativo>,<tipo>,<aberto>,<fechado>,<filtro>,<invertido>
    HHSW,<ativo>,<invertido>,<forcar_fechado>
    OK,PAD / OK,GLOBAL / OK,HH / OK,HHSW / OK,SAVE / OK,RESET /
    OK,CAL / OK,CALOFF / ERROR,UNKNOWN
    CAL,<idx>,<atual>,<pico>,<threshold>,<velmax>,<lockout>   (telemetria contínua)
    HHSTATUS,<raw>,<posicao>,<switch>,<tipo>

------------------------------------------------------------------
IMPORTANTE -- suposição a validar:

RESOLVIDO (2026-09-18) -- a suposição original era ERRADA: o firmware
físico (config_bateria.h: NOME_PADS/NOTAS_MIDI/GPIO_PADS) usa uma ordem
DIFERENTE da lista PADS do console a partir do índice 5 -- o console
lista Hi-Hat antes de Crash/Ride, mas o firmware físico tem o Hi-Hat
por último (índice 7, GPIO dedicado). Ver PAD_ID_TO_FIRMWARE_IDX logo
abaixo, que traduz entre as duas ordens em toda leitura/escrita de
`pads` -- sem isso, editar "Hi-Hat" pela tela mandaria o comando PAD
pro índice físico do Crash, silenciosamente. Os ids 8-10 do console
(Splash/China/Tom4) não têm pad físico correspondente (o firmware só
tem 8 pads) e continuam sem tradução -- ficam permanentemente como
"pad não sincronizado".
================================================================
"""

import glob
import json
import os
import subprocess
import sys
import threading
import time

import serial
import rtmidi
from flask import Flask, jsonify, request, send_from_directory
from flask_sock import Sock

import kit_manager
import pad_mixer

# ----------------------------------------------------------------
# CONFIGURAÇÃO
# ----------------------------------------------------------------

BAUDRATE = int(os.environ.get("DRUM_BAUDRATE", "115200"))
HTTP_PORT = int(os.environ.get("DRUM_HTTP_PORT", "8000"))
MIDI_PORT_NAME = os.environ.get("DRUM_MIDI_PORT_NAME", "ESP32 Drum")


def resolve_serial_port():
    """
    Descobre em qual /dev a ESP32 está, de um jeito que sobrevive a
    trocar a porta USB física dela. /dev/ttyACM0 (ou ttyUSB0) é só
    "o primeiro dispositivo serial-USB que o kernel viu desta vez" --
    se a ESP32 for pra outra porta, ou se outro dispositivo serial for
    plugado antes dela no boot, o número pode mudar (viraria ttyACM1
    etc) e o backend abriria a porta errada (ou nenhuma).

    /dev/serial/by-id/ resolve isso: o udev cria ali um link com nome
    baseado no fabricante/produto/nº de série USB do dispositivo, que
    é o MESMO não importa em qual porta USB ele seja ligado.

    DRUM_SERIAL_PORT continua funcionando como override manual, se
    precisar forçar uma porta específica.
    """
    explicit = os.environ.get("DRUM_SERIAL_PORT")
    if explicit:
        return explicit

    candidatos = sorted(glob.glob("/dev/serial/by-id/*"))
    if len(candidatos) == 1:
        return candidatos[0]
    if len(candidatos) > 1:
        print(
            "[backend] aviso: mais de um dispositivo em /dev/serial/by-id "
            f"({candidatos}) -- usando o primeiro. Se for o errado, defina "
            "a variável de ambiente DRUM_SERIAL_PORT com o caminho certo."
        )
        return candidatos[0]

    # nenhum link estável achado (ainda não montou, ou uma ESP32 sem
    # número de série USB de verdade) -- cai pro nome "cru" como último
    # recurso, do jeito que era antes.
    return None


SERIAL_PORT = resolve_serial_port() or "/dev/ttyACM0"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HTML_FILE = "drum-module-console.html"

COMMAND_TIMEOUT = 1.2  # segundos de espera por ACK antes de considerar timeout

# ----------------------------------------------------------------
# ESTADO COMPARTILHADO (cache do que o ESP32 já confirmou)
# ----------------------------------------------------------------

pads = {}            # idx FÍSICO do firmware (int) -> {nota, threshold, velmax, lockout, ativo, curva}
global_config = {}   # {peak_time, min_velocity, note_duration, midi_channel}
hh_config = {}       # {ativo, tipo, aberto, fechado, filtro, invertido}
hhsw_config = {}     # {ativo, invertido, forcar_fechado}
hh_live = {}         # {raw, posicao, switch, tipo} -- última HHSTATUS recebida

state_lock = threading.Lock()  # protege os dicts acima

# ----------------------------------------------------------------
# ORDEM DOS PADS -- o console web (lista PADS do HTML) e o firmware
# físico (NOME_PADS/NOTAS_MIDI/GPIO_PADS em config_bateria.h) usam
# ORDENS DIFERENTES a partir do índice 5: o HTML lista Hi-Hat antes de
# Crash/Ride, mas o firmware físico tem Hi-Hat por último (índice 7,
# GPIO dedicado). Confirmado batendo a nota MIDI de cada posição nas
# duas listas (a mesma nota tem que estar no mesmo pad físico):
#
#   id no HTML -> índice físico no firmware
#   0 Bumbo(36)->0   1 Caixa(38)->1   2 Tom1(48)->2   3 Tom2(45)->3
#   4 Tom3(43)->4    5 Hi-Hat(42)->7  6 Crash(49)->5  7 Ride(51)->6
#
# Sem essa tradução, editar "Hi-Hat" pela tela mandaria o comando PAD
# pro índice físico do Crash (e vice-versa) -- o ESP32 aceitaria sem
# reclamar, então o bug ficaria silencioso. Índices 8-10 do HTML
# (Splash/China/Tom4) não têm pad físico correspondente -- ficam sem
# tradução (o backend já trata isso como "pad não sincronizado").
# ----------------------------------------------------------------

PAD_ID_TO_FIRMWARE_IDX = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 7, 6: 5, 7: 6}
FIRMWARE_IDX_TO_PAD_ID = {v: k for k, v in PAD_ID_TO_FIRMWARE_IDX.items()}


def _fw_idx(pad_id):
    """Traduz o id do pad como o console mostra pro índice físico real
    do firmware. ids sem pad físico (8-10) voltam sem tradução -- o
    resto do código já trata esse caso como 'nunca vai sincronizar'."""
    return PAD_ID_TO_FIRMWARE_IDX.get(pad_id, pad_id)

esp32_connected = False  # vira True assim que a serial abre com sucesso

# ----------------------------------------------------------------
# MIDI (idêntico ao serial_midi.py original)
# ----------------------------------------------------------------

midiout = rtmidi.MidiOut()
midiout.open_virtual_port(MIDI_PORT_NAME)


def enviar_midi(msg):
    try:
        midiout.send_message(msg)
    except Exception as e:
        print("[backend] erro MIDI:", e)


def _aplicar_volume_pad(nota, velocity):
    """Só decide se a nota deve ser MUDADA (volume=0%) antes de repassar
    pro DrumGizmo -- o ajuste de volume em si (0% a 300%) agora é feito
    de verdade no próprio arquivo de áudio do instrumento (ver
    apply_pad_gain.py e a rota /api/mixer/<nota>/volume), não mais
    escalando a velocity aqui como antes. Motivo da mudança: o
    DrumGizmo não tem controle de ganho por instrumento (só "power" pra
    escolher qual amostra tocar), e escalar só a velocity dava uma
    diferença pequena demais pra nivelar peças gravadas com volumes bem
    diferentes entre si (ex: um Crash bem mais alto que um Tom).

    O valor ORIGINAL medido pelo ESP32 continua sendo usado pro
    medidor/flash da tela e pra calibração (ver midi_note_event), que
    devem mostrar a força real da pancada, não o volume ajustado.

    IMPORTANTE (2026-09-19): o volume é por KIT agora (ver pad_mixer.py)
    -- por isso olha qual kit está ativo AGORA pra saber qual "mudo"
    vale. Sem nenhum kit ativo (não deveria acontecer em uso normal),
    não muta nada -- deixa a nota passar normal.

    Devolve a velocity sem alteração quando o pad não estiver mudo, ou
    0 quando estiver (0 = "não manda a nota nenhuma", ver os dois
    call-sites) -- essa parte continua instantânea, sem precisar
    recarregar o kit.
    """
    kit_name = kit_manager.get_active_kit()
    if not kit_name:
        return velocity
    fator = pad_mixer.get_volume_factor(nota, kit_name)
    if fator <= 0.0:
        return 0  # mudo de verdade -- chamador não deve enviar a nota
    return velocity


# ----------------------------------------------------------------
# SERIAL
# ----------------------------------------------------------------

def open_serial_with_retry():
    """
    Abre a serial da ESP32, tentando de novo (em vez de derrubar o
    processo inteiro) se ela ainda não estiver lá -- importante pra
    ligar direto no boot do Raspberry, quando este script pode subir
    antes do Linux terminar de enumerar o dispositivo USB. Sem isso,
    `serial.Serial(...)` direto na carga do módulo derrubava o backend
    inteiro de cara se a ESP32 não estivesse plugada/pronta ainda.
    """
    porta = SERIAL_PORT
    tentativa = 0
    while True:
        tentativa += 1
        # tenta resolver de novo a cada rodada -- o link em
        # /dev/serial/by-id pode só aparecer um pouco depois do boot
        porta_resolvida = resolve_serial_port() or porta
        try:
            s = serial.Serial(porta_resolvida, BAUDRATE, timeout=0.01)
            if tentativa > 1:
                print(f"[backend] serial conectada em {porta_resolvida} (após {tentativa} tentativas)")
            return s, porta_resolvida
        except Exception as e:
            if tentativa == 1 or tentativa % 10 == 0:
                print(f"[backend] aguardando a ESP32 aparecer em {porta_resolvida} ({e}) -- tentando de novo...")
            time.sleep(2)


ser, SERIAL_PORT = open_serial_with_retry()

serial_write_lock = threading.Lock()  # só isso já bastaria pra não intercalar bytes de escrita


def enviar_comando(comando):
    with serial_write_lock:
        try:
            ser.write((comando + "\n").encode())
            ser.flush()
            print("[backend] comando enviado:", comando)
        except Exception as e:
            print("[backend] erro enviando comando:", e)


# ----------------------------------------------------------------
# WEBSOCKET -- broadcast pra todos os navegadores conectados
# ----------------------------------------------------------------

_ws_clients = {}  # ws -> threading.Lock (um lock de envio por conexão)
_ws_clients_lock = threading.Lock()


def ws_register(ws):
    with _ws_clients_lock:
        _ws_clients[ws] = threading.Lock()


def ws_unregister(ws):
    with _ws_clients_lock:
        _ws_clients.pop(ws, None)


def broadcast(obj):
    payload = json.dumps(obj, ensure_ascii=False)
    with _ws_clients_lock:
        items = list(_ws_clients.items())
    for ws, lock in items:
        try:
            with lock:
                ws.send(payload)
        except Exception:
            ws_unregister(ws)


# ----------------------------------------------------------------
# ESPERA DE ACK -- um comando por vez (bate com o protocolo, que é
# estritamente pergunta/resposta numa única serial compartilhada)
# ----------------------------------------------------------------

command_lock = threading.Lock()
_ack_event = threading.Event()
_waiting_keys = None
_ack_payload = {"key": None, "text": None}


def _line_key(texto, partes):
    t0 = partes[0].strip().upper() if partes and partes[0] else ""
    if t0 in ("OK", "ERROR"):
        return texto.strip().upper()
    return t0


def send_command_and_wait(cmd, expect_keys, timeout=COMMAND_TIMEOUT):
    """
    Envia `cmd` pro ESP32 e espera até uma linha cuja "chave"
    (ex: "OK,PAD", "ENDCONFIG", "PONG", "HHSTATUS") esteja em
    `expect_keys` chegar de volta. Serializado: só um comando
    "em voo" por vez, exatamente como um operador digitando no
    terminal um comando de cada vez.
    """
    global _waiting_keys

    with command_lock:
        _ack_event.clear()
        _ack_payload["key"] = None
        _ack_payload["text"] = None
        _waiting_keys = set(expect_keys)

        enviar_comando(cmd)

        ok = _ack_event.wait(timeout)

        _waiting_keys = None

        if not ok:
            return {"ok": False, "error": "timeout", "cmd": cmd}

        return {"ok": True, "key": _ack_payload["key"], "text": _ack_payload["text"]}


# ----------------------------------------------------------------
# PROCESSAMENTO DAS LINHAS DE CONFIGURAÇÃO (vindas via SysEx)
# ----------------------------------------------------------------

def handle_line(texto):
    """Atualiza o cache local e dispara broadcasts, pra QUALQUER
    linha recebida -- além disso, se alguém estiver esperando essa
    linha como ACK (ver send_command_and_wait), libera a espera."""

    partes = texto.split(",")
    if not partes or not partes[0]:
        return

    tipo = partes[0].strip().upper()

    # ---- libera quem estiver esperando essa linha como ACK ----
    key = _line_key(texto, partes)
    if _waiting_keys is not None and key in _waiting_keys:
        _ack_payload["key"] = key
        _ack_payload["text"] = texto
        _ack_event.set()

    try:
        if tipo == "PAD" and len(partes) >= 8:
            idx = int(partes[1])
            with state_lock:
                pads[idx] = {
                    "nota": int(partes[2]),
                    "threshold": int(partes[3]),
                    "velmax": int(partes[4]),
                    "lockout": int(partes[5]),
                    "ativo": int(partes[6]),
                    "curva": int(partes[7]),
                }
            return

        if tipo == "GLOBAL" and len(partes) >= 5:
            with state_lock:
                global_config.update({
                    "peak_time": int(partes[1]),
                    "min_velocity": int(partes[2]),
                    "note_duration": int(partes[3]),
                    "midi_channel": int(partes[4]),
                })
            return

        if tipo == "HH" and len(partes) >= 7:
            with state_lock:
                hh_config.update({
                    "ativo": int(partes[1]),
                    "tipo": int(partes[2]),
                    "aberto": int(partes[3]),
                    "fechado": int(partes[4]),
                    "filtro": int(partes[5]),
                    "invertido": int(partes[6]),
                })
                # campos novos (limiares da zona "meio-aberto" do hi-hat,
                # nota 80) -- opcional, pra continuar aceitando um firmware
                # mais antigo que só mande os 6 campos originais.
                if len(partes) >= 9:
                    hh_config["limiar_meio_aberto"] = int(partes[7])
                    hh_config["limiar_fechado"] = int(partes[8])
            return

        if tipo == "HHSW" and len(partes) >= 4:
            with state_lock:
                hhsw_config.update({
                    "ativo": int(partes[1]),
                    "invertido": int(partes[2]),
                    "forcar_fechado": int(partes[3]),
                })
            return

        if tipo == "BEGINCONFIG":
            with state_lock:
                pads.clear()
                global_config.clear()
                hh_config.clear()
                hhsw_config.clear()
            return

        if tipo == "ENDCONFIG":
            broadcast({"type": "state", **snapshot()})
            return

        if tipo == "CAL" and len(partes) >= 7:
            # o firmware manda seu próprio índice físico (0-7) -- traduz de
            # volta pro id que o console web usa (PADS do HTML) antes de
            # repassar, senão o "cal" chega marcado pro pad errado do lado
            # do navegador (mesmo problema que PAD_ID_TO_FIRMWARE_IDX já
            # resolve pras outras rotas).
            fw_idx = int(partes[1])
            pad_id = FIRMWARE_IDX_TO_PAD_ID.get(fw_idx, fw_idx)
            msg = {
                "type": "cal",
                "pad": pad_id,
                "atual": int(partes[2]),
                "pico": int(partes[3]),
                "threshold": int(partes[4]),
                "velmax": int(partes[5]),
                "lockout": int(partes[6]),
            }
            broadcast(msg)
            return

        if tipo == "HHSTATUS" and len(partes) >= 5:
            with state_lock:
                hh_live.update({
                    "raw": int(partes[1]),
                    "posicao": int(partes[2]),
                    "switch": int(partes[3]),
                    "tipo": int(partes[4]),
                })
                # campo novo (leitura do potenciômetro mesmo quando ele não é
                # o sensor ativo, pra calibração na tela) -- opcional, pra
                # continuar aceitando um firmware mais antigo que só mande
                # os 4 campos originais.
                if len(partes) >= 6:
                    hh_live["pot_raw"] = int(partes[5])
            broadcast({"type": "hhstatus", **hh_live})
            return

        if tipo in ("OK", "ERROR", "PONG"):
            broadcast({"type": "ack", "text": texto})
            return

    except (ValueError, IndexError) as e:
        print("[backend] erro processando linha:", texto, "-", e)


def snapshot():
    with state_lock:
        # traduz de volta pro id que o console web usa (ver PAD_ID_TO_FIRMWARE_IDX) --
        # o console não sabe nada da ordem física do firmware, só da lista PADS do HTML.
        pads_por_id_html = {FIRMWARE_IDX_TO_PAD_ID.get(idx, idx): p for idx, p in pads.items()}
        return {
            "pads": pads_por_id_html,
            "global": dict(global_config),
            "hh": dict(hh_config),
            "hhsw": dict(hhsw_config),
        }


def midi_note_event(status, note, velocity):
    tipo = status & 0xF0
    if tipo == 0x90 and velocity > 0:
        broadcast({"type": "hit", "note": note, "velocity": velocity, "ts": time.time()})
        with _learn_lock:
            aprendendo = _learn_state["active"]
            alvo = _learn_state["target_note"]
        if aprendendo:
            # IMPORTANTE: não pode chamar send_command_and_wait() direto
            # daqui -- esta função roda dentro da MESMA thread que lê a
            # serial (serial_reader_loop/processar_midi). send_command_and_wait
            # bloqueia esperando a linha "OK,PAD" chegar, mas quem
            # processaria essa linha e liberaria a espera é exatamente
            # esta thread -- ou seja, chamar direto daria deadlock (trava
            # até dar timeout, sempre). Por isso a reatribuição roda numa
            # thread separada.
            threading.Thread(target=_handle_learn_hit, args=(note, alvo), daemon=True).start()
    elif tipo == 0x80 or (tipo == 0x90 and velocity == 0):
        broadcast({"type": "note_off", "note": note, "ts": time.time()})


# ----------------------------------------------------------------
# DEFINIÇÃO MANUAL DE PAD ("MIDI learn"): configurando uma peça (nota
# `target_note`, ex: 49 = Crash) na tela, o usuário pode em vez de vez
# de mexer na imagem, clicar "definição manual" e bater na peça FÍSICA
# que quer que passe a mandar aquele som -- a próxima pancada física
# recebida tem seu pad de origem descoberto (por qual índice de PAD já
# está configurado com a nota que realmente chegou) e reconfigurado no
# ESP32 pra usar `target_note` dali em diante.
# ----------------------------------------------------------------

_learn_lock = threading.Lock()
_learn_state = {"active": False, "target_note": None}
_learn_generation = 0  # incrementado a cada start/cancel/conclusão, pra o timeout saber se já é "outra rodada"


def _handle_learn_hit(note_fisica, target_note):
    with _learn_lock:
        if not _learn_state["active"] or _learn_state["target_note"] != target_note:
            return  # outra rodada já cancelou/concluiu isso, ou não é a que esperávamos
        _learn_state["active"] = False
        _learn_state["target_note"] = None

    with state_lock:
        idx = next((i for i, p in pads.items() if p.get("nota") == note_fisica), None)
        cached = dict(pads[idx]) if idx is not None else None

    if idx is None or cached is None:
        broadcast({
            "type": "learn_result", "ok": False,
            "error": f"a peça física que bateu manda a nota {note_fisica}, mas ela ainda não está "
                     "sincronizada com o backend -- tente 'Sincronizar agora' na aba SISTEMA e bata de novo.",
        })
        return

    if note_fisica == target_note:
        broadcast({
            "type": "learn_result", "ok": False,
            "error": "essa peça física já manda exatamente essa nota -- nada pra mudar.",
        })
        return

    cached["nota"] = target_note
    cmd = f"PAD {idx} {cached['nota']} {cached['threshold']} {cached['velmax']} {cached['lockout']} {int(cached['ativo'])} {cached['curva']}"
    result = send_command_and_wait(cmd, {"OK,PAD"})

    if not result["ok"]:
        broadcast({"type": "learn_result", "ok": False, "error": "o ESP32 não confirmou a mudança (timeout) -- tente de novo."})
        return

    with state_lock:
        pads[idx] = cached
    broadcast({"type": "state", **snapshot()})
    broadcast({
        "type": "learn_result", "ok": True,
        "pad_idx": FIRMWARE_IDX_TO_PAD_ID.get(idx, idx),  # id do console web, não o índice físico
        "old_note": note_fisica, "new_note": target_note,
    })


# ----------------------------------------------------------------
# LOOP DE LEITURA DA SERIAL (adaptado do serial_midi.py original)
# ----------------------------------------------------------------

running_status = None


def processar_midi(status):
    tipo = status & 0xF0

    if tipo in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
        dados = ser.read(2)
        if len(dados) == 2:
            nota, vel = dados[0], dados[1]
            eh_note_on = (tipo == 0x90 and vel > 0)
            vel_dg = _aplicar_volume_pad(nota, vel) if eh_note_on else vel
            if not (eh_note_on and vel_dg == 0):  # volume 0% dessa peça -- não toca nada mesmo
                enviar_midi([status, nota, vel_dg])
            midi_note_event(status, nota, vel)  # broadcast usa a velocity REAL medida, não a ajustada

    elif tipo in (0xC0, 0xD0):
        dados = ser.read(1)
        if len(dados) == 1:
            enviar_midi([status, dados[0]])


def serial_reader_loop():
    global running_status, esp32_connected

    esp32_connected = True
    print("[backend] leitura serial iniciada em", SERIAL_PORT)

    while True:
        try:
            primeiro = ser.read(1)
            if not primeiro:
                continue

            byte = primeiro[0]

            # ---------------- SysEx (config) ----------------
            if byte == 0xF0:
                sysex = bytearray([byte])
                while True:
                    b = ser.read(1)
                    if not b:
                        break
                    sysex.append(b[0])
                    if b[0] == 0xF7:
                        break

                if sysex and sysex[-1] == 0xF7:
                    try:
                        texto = bytes(sysex[4:-1]).decode("ascii", errors="replace")
                        handle_line(texto)
                    except Exception as e:
                        print("[backend] erro processando SysEx:", e)
                continue

            # ---------------- MIDI realtime ----------------
            if byte >= 0xF8:
                enviar_midi([byte])
                continue

            # ---------------- MIDI status ----------------
            if byte & 0x80:
                tipo = byte & 0xF0
                if tipo in (0x80, 0x90, 0xA0, 0xB0, 0xC0, 0xD0, 0xE0):
                    running_status = byte
                    processar_midi(byte)
                continue

            # ---------------- running status ----------------
            if running_status is not None:
                tipo = running_status & 0xF0
                if tipo in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                    segundo = ser.read(1)
                    if segundo:
                        nota, vel = byte, segundo[0]
                        eh_note_on = (tipo == 0x90 and vel > 0)
                        vel_dg = _aplicar_volume_pad(nota, vel) if eh_note_on else vel
                        if not (eh_note_on and vel_dg == 0):  # volume 0% dessa peça -- não toca nada mesmo
                            enviar_midi([running_status, nota, vel_dg])
                        midi_note_event(running_status, nota, vel)
                elif tipo in (0xC0, 0xD0):
                    enviar_midi([running_status, byte])

        except Exception as e:
            # não deixa um erro pontual de leitura derrubar o processo inteiro
            print("[backend] erro no loop de leitura serial:", e)
            time.sleep(0.2)


def refresh_from_esp32():
    return send_command_and_wait("GET", {"ENDCONFIG"}, timeout=2.0)


# ----------------------------------------------------------------
# FLASK -- REST + WebSocket + arquivo estático (o próprio HTML)
# ----------------------------------------------------------------

app = Flask(__name__)
sock = Sock(app)


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, HTML_FILE)


@app.route("/api/state")
def api_state():
    return jsonify({"connected": esp32_connected, **snapshot(), "hh_live": dict(hh_live)})


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    result = refresh_from_esp32()
    if not result["ok"]:
        return jsonify(result), 504
    return jsonify({"ok": True, **snapshot()})


@app.route("/api/ping")
def api_ping():
    result = send_command_and_wait("PING", {"PONG"}, timeout=1.0)
    return jsonify(result), (200 if result["ok"] else 504)


@app.route("/api/pad/<int:idx>", methods=["POST"])
def api_set_pad(idx):
    # `idx` chega como o id do console web (lista PADS do HTML) -- traduz
    # pro índice físico real do firmware antes de tocar em `pads` ou
    # montar o comando (ver PAD_ID_TO_FIRMWARE_IDX pro porquê).
    fw_idx = _fw_idx(idx)
    body = request.get_json(force=True, silent=True) or {}

    with state_lock:
        cached = pads.get(fw_idx)

    if cached is None:
        refresh_from_esp32()
        with state_lock:
            cached = pads.get(fw_idx)

    if cached is None:
        return jsonify({"ok": False, "error": "pad ainda não sincronizado -- tente /api/refresh"}), 409

    p = dict(cached)
    for campo in ("nota", "threshold", "velmax", "lockout", "ativo", "curva"):
        if campo in body:
            p[campo] = int(body[campo])

    cmd = f"PAD {fw_idx} {p['nota']} {p['threshold']} {p['velmax']} {p['lockout']} {int(p['ativo'])} {p['curva']}"
    result = send_command_and_wait(cmd, {"OK,PAD"})

    if result["ok"]:
        with state_lock:
            pads[fw_idx] = p
        broadcast({"type": "state", **snapshot()})
        return jsonify({"ok": True, "pad": p})

    return jsonify(result), 504


@app.route("/api/global", methods=["POST"])
def api_set_global():
    body = request.get_json(force=True, silent=True) or {}

    with state_lock:
        cached = dict(global_config)

    if not cached:
        refresh_from_esp32()
        with state_lock:
            cached = dict(global_config)

    if not cached:
        return jsonify({"ok": False, "error": "GLOBAL ainda não sincronizado -- tente /api/refresh"}), 409

    cached.update({k: int(v) for k, v in body.items() if k in ("peak_time", "min_velocity", "note_duration", "midi_channel")})

    cmd = f"GLOBAL {cached['peak_time']} {cached['min_velocity']} {cached['note_duration']} {cached['midi_channel']}"
    result = send_command_and_wait(cmd, {"OK,GLOBAL"})

    if result["ok"]:
        with state_lock:
            global_config.update(cached)
        broadcast({"type": "state", **snapshot()})
        return jsonify({"ok": True, "global": cached})

    return jsonify(result), 504


@app.route("/api/hihat", methods=["POST"])
def api_set_hihat():
    body = request.get_json(force=True, silent=True) or {}

    with state_lock:
        cached = dict(hh_config)

    if not cached:
        refresh_from_esp32()
        with state_lock:
            cached = dict(hh_config)

    if not cached:
        return jsonify({"ok": False, "error": "HH ainda não sincronizado -- tente /api/refresh"}), 409

    cached.update({
        k: int(v) for k, v in body.items()
        if k in ("ativo", "tipo", "aberto", "fechado", "filtro", "invertido", "limiar_meio_aberto", "limiar_fechado")
    })

    # Os limiares da zona "meio-aberto" (nota 80) podem nunca ter sido
    # sincronizados ainda (firmware antigo, ou primeira sincronização
    # depois de atualizar) -- usa os mesmos valores padrão de fábrica
    # do firmware (config_bateria.h) como fallback, em vez de mandar
    # um comando incompleto ou quebrar aqui.
    lim_ma = cached.get("limiar_meio_aberto", 40)
    lim_f = cached.get("limiar_fechado", 100)
    if lim_ma >= lim_f:
        lim_ma, lim_f = min(lim_ma, lim_f), max(lim_ma, lim_f)
        if lim_ma == lim_f:
            lim_f = min(127, lim_f + 1)
    cached["limiar_meio_aberto"] = lim_ma
    cached["limiar_fechado"] = lim_f

    cmd = (
        f"HH {cached['ativo']} {cached['tipo']} {cached['aberto']} {cached['fechado']} "
        f"{cached['filtro']} {cached['invertido']} {cached['limiar_meio_aberto']} {cached['limiar_fechado']}"
    )
    result = send_command_and_wait(cmd, {"OK,HH"})

    if result["ok"]:
        with state_lock:
            hh_config.update(cached)
        broadcast({"type": "state", **snapshot()})
        return jsonify({"ok": True, "hh": cached})

    return jsonify(result), 504


@app.route("/api/hihat-switch", methods=["POST"])
def api_set_hihat_switch():
    body = request.get_json(force=True, silent=True) or {}

    with state_lock:
        cached = dict(hhsw_config)

    if not cached:
        refresh_from_esp32()
        with state_lock:
            cached = dict(hhsw_config)

    if not cached:
        return jsonify({"ok": False, "error": "HHSW ainda não sincronizado -- tente /api/refresh"}), 409

    cached.update({k: int(v) for k, v in body.items() if k in ("ativo", "invertido", "forcar_fechado")})

    cmd = f"HHSW {cached['ativo']} {cached['invertido']} {cached['forcar_fechado']}"
    result = send_command_and_wait(cmd, {"OK,HHSW"})

    if result["ok"]:
        with state_lock:
            hhsw_config.update(cached)
        broadcast({"type": "state", **snapshot()})
        return jsonify({"ok": True, "hhsw": cached})

    return jsonify(result), 504


@app.route("/api/save", methods=["POST"])
def api_save():
    result = send_command_and_wait("SAVE", {"OK,SAVE"})
    return jsonify(result), (200 if result["ok"] else 504)


@app.route("/api/reset", methods=["POST"])
def api_reset():
    result = send_command_and_wait("RESET", {"OK,RESET"}, timeout=2.0)
    if result["ok"]:
        refresh_from_esp32()
        return jsonify({"ok": True, **snapshot()})
    return jsonify(result), 504


@app.route("/api/cal/<int:idx>/start", methods=["POST"])
def api_cal_start(idx):
    # `idx` chega como o id do console web (lista PADS do HTML) -- IGUAL
    # à rota /api/pad/<idx>, precisa traduzir pro índice físico real do
    # firmware antes de mandar o comando (ver PAD_ID_TO_FIRMWARE_IDX).
    # Sem isso, calibrar "Hi-Hat" pela tela transmitia o sinal ao vivo do
    # "Crash" no firmware, sem erro nenhum pra avisar.
    if idx not in PAD_ID_TO_FIRMWARE_IDX:
        return jsonify({"ok": False, "error": f"pad {idx!r} não tem entrada física no firmware (Splash/China/Tom4 não têm ADC)"}), 400
    fw_idx = _fw_idx(idx)
    result = send_command_and_wait(f"CAL {fw_idx}", {"OK,CAL"})
    return jsonify(result), (200 if result["ok"] else 504)


@app.route("/api/cal/stop", methods=["POST"])
def api_cal_stop():
    result = send_command_and_wait("CALOFF", {"OK,CALOFF"})
    return jsonify(result), (200 if result["ok"] else 504)


@app.route("/api/hhstatus")
def api_hhstatus():
    result = send_command_and_wait("HHSTATUS", {"HHSTATUS"})
    if result["ok"]:
        return jsonify({"ok": True, **hh_live})
    return jsonify(result), 504


# ---- definição manual de pad ("MIDI learn") ----

LEARN_TIMEOUT_S = 15.0


@app.route("/api/learn/start", methods=["POST"])
def api_learn_start():
    global _learn_generation
    body = request.get_json(force=True, silent=True) or {}
    if "note" not in body:
        return jsonify({"ok": False, "error": "faltou 'note' (a nota MIDI da peça sendo configurada agora)"}), 400
    try:
        target_note = int(body["note"])
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "'note' inválida"}), 400

    with _learn_lock:
        _learn_generation += 1
        minha_geracao = _learn_generation
        _learn_state["active"] = True
        _learn_state["target_note"] = target_note

    def _timeout_watchdog():
        time.sleep(LEARN_TIMEOUT_S)
        with _learn_lock:
            if _learn_generation != minha_geracao or not _learn_state["active"]:
                return  # já foi cancelado/concluído/substituído por outra rodada
            _learn_state["active"] = False
            _learn_state["target_note"] = None
        broadcast({"type": "learn_result", "ok": False, "error": "tempo esgotado esperando uma pancada -- tente de novo."})

    threading.Thread(target=_timeout_watchdog, daemon=True).start()
    return jsonify({"ok": True, "listening_for_note": target_note, "timeout_s": LEARN_TIMEOUT_S})


@app.route("/api/learn/cancel", methods=["POST"])
def api_learn_cancel():
    global _learn_generation
    with _learn_lock:
        _learn_generation += 1
        _learn_state["active"] = False
        _learn_state["target_note"] = None
    return jsonify({"ok": True})


# ---- mixer por pad (volume, guardado no Pi -- ver pad_mixer.py) ----

@app.route("/api/mixer")
def api_mixer_get():
    # volume agora é por kit (ver pad_mixer.py) -- sem kit ativo (não
    # deveria acontecer em uso normal) devolve vazio, não erro.
    return jsonify(pad_mixer.get_all(kit_manager.get_active_kit()))


@app.route("/api/mixer/<int:note>", methods=["GET"])
def api_mixer_get_one(note):
    return jsonify(pad_mixer.get(note, kit_manager.get_active_kit()))


@app.route("/api/mixer/<int:note>/volume", methods=["POST"])
def api_mixer_set_volume(note):
    """
    Volume por pad reprocessa de verdade os arquivos de áudio do
    instrumento correspondente a essa nota NO KIT ATIVO AGORA (ver
    apply_pad_gain.py) e recarrega o DrumGizmo -- por isso demora
    alguns segundos e avisa o resultado via WebSocket
    ({"type": "kit_status"}) em vez de deixar a requisição HTTP
    pendurada esperando.

    (Antes disso era feito escalando a velocity do MIDI na hora, sem
    reprocessar nada -- mas na prática só dava uma diferença pequena,
    insuficiente pra nivelar peças gravadas com volumes bem diferentes
    entre si (ex: Crash bem mais alto que Tom), já que o DrumGizmo não
    tem um controle de ganho de verdade por instrumento, só "power"
    pra escolher qual amostra tocar. Só reprocessar o áudio resolve
    isso de vez.)

    Exceção: volume=0.0 (mudo) continua instantâneo e não mexe em
    nenhum arquivo -- a nota simplesmente deixa de ser mandada pro
    DrumGizmo (ver _aplicar_volume_pad), então não precisa recarregar
    nada pra mutar/desmutar.

    IMPORTANTE (2026-09-19): o volume é salvo POR KIT (ver
    pad_mixer.py) -- essa nota pode ter um volume diferente em cada
    kit, de propósito (cada kit grava as peças com volumes relativos
    diferentes entre si). Trocar de kit não reseta nada: cada kit
    lembra o SEU próprio ajuste, reaplicado sozinho toda vez que ele
    carrega (ver kit_manager._reapply_saved_volumes). Regerar as
    amostras do zero (setup_kits.py --force) também reaplica sozinho.
    """
    body = request.get_json(force=True, silent=True) or {}
    if "value" not in body:
        return jsonify({"ok": False, "error": "faltou 'value' (0.0 a 3.0)"}), 400
    try:
        volume_value = max(0.0, min(3.0, float(body["value"])))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "'value' inválido"}), 400

    active_kit_name = kit_manager.get_active_kit()
    if not active_kit_name:
        return jsonify({"ok": False, "error": "nenhum kit ativo agora -- não dá pra saber em qual kit salvar esse volume"}), 409

    if volume_value <= 0.0:
        # mudo de verdade -- instantâneo, não mexe em arquivo nenhum
        # nem recarrega o kit (ver _aplicar_volume_pad).
        entry = pad_mixer.set_volume(note, 0.0, active_kit_name)
        return jsonify({"ok": True, "note": note, "kit": active_kit_name, "mixer": entry})

    paths = kit_manager.get_active_kit_paths()
    if not paths:
        return jsonify({"ok": False, "error": "kit ativo não usa o formato cwd/kit_xml/midimap -- não dá pra ajustar volume nele"}), 409

    script = os.path.join(BASE_DIR, "apply_pad_gain.py")
    proc = subprocess.run(
        [sys.executable, script, paths["cwd"], paths["kit_xml"], paths["midimap"], str(note), str(volume_value)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        print("[backend] apply_pad_gain.py falhou:", proc.stdout, proc.stderr)
        return jsonify({
            "ok": False,
            "error": "não consegui ajustar o volume nesse instrumento -- veja o motivo",
            "detail": (proc.stdout + "\n" + proc.stderr).strip()[-2000:],
        }), 400

    entry = pad_mixer.set_volume(note, volume_value, active_kit_name)
    kit_name = paths["name"]

    def _on_ready(ok, message):
        broadcast({"type": "kit_status", "kit": kit_name, "ok": ok, "message": message})

    def _on_progress(atual, total):
        broadcast({"type": "kit_progress", "kit": kit_name, "current": atual, "total": total})

    try:
        kit_manager.switch_kit(kit_name, on_ready=_on_ready, on_progress=_on_progress)
    except Exception as e:
        return jsonify({"ok": False, "error": f"volume ajustado, mas falhou ao recarregar o kit: {e}"}), 500

    broadcast({"type": "kit", "kit": kit_name})
    return jsonify({"ok": True, "note": note, "mixer": entry, "loading": True, "detail": proc.stdout.strip()})

# (o balanço/pan por pad foi removido -- ver pad_mixer.py pra explicação;
# o volume, ao contrário do pan, funciona pra qualquer instrumento porque
# o ganho é aplicado igual nos dois canais -- não depende de Out1/Out2
# serem arquivos diferentes.)


# ---- kits (DrumGizmo) ----

@app.route("/api/kits")
def api_kits():
    return jsonify(kit_manager.list_kits())


@app.route("/api/kits/<name>/load", methods=["POST"])
def api_kit_load(name):
    # `ok:true` aqui só confirma que o processo do DrumGizmo subiu sem
    # crashar na hora -- kits grandes (Ludwig/Crocell) continuam
    # carregando em segundo plano por muito mais tempo que essa
    # resposta HTTP leva pra voltar. O aviso de "realmente pronto pra
    # tocar" (ou "não deu certo") chega depois via WebSocket, como
    # {"type": "kit_status", ...} -- é isso que o HTML espera antes de
    # trocar o botão de "Carregando..." pra "Carregado!"/"Falhou".
    def _on_ready(ok, message):
        broadcast({"type": "kit_status", "kit": name, "ok": ok, "message": message})

    # progresso real de carregamento (ex: "1234 de 9660"), o mesmo número
    # que aparece no terminal com `tail -f drumgizmo.log` -- alimenta a
    # barra de progresso no HTML em vez de só um texto de "pode demorar".
    def _on_progress(atual, total):
        broadcast({"type": "kit_progress", "kit": name, "current": atual, "total": total})

    try:
        result = kit_manager.switch_kit(name, on_ready=_on_ready, on_progress=_on_progress)
        broadcast({"type": "kit", "kit": name})
        return jsonify({"ok": True, **result, "loading": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


# ---- sistema (dispositivos) --------------------------------------------
#
# Este sistema foi feito pra ser compartilhado/instalado em outra máquina
# qualquer, então nada aqui pode depender do hardware específico deste
# Raspberry. A porta serial do ESP32 e a porta MIDI virtual já são
# automáticas por natureza (ver comentários abaixo); a única coisa que
# varia de máquina pra máquina é a placa de som USB, e essa a gente tenta
# detectar sozinho -- só cai pra escolha manual (nesta aba) se não der
# pra decidir sozinho (nenhuma placa achada, ou mais de uma ao mesmo
# tempo).

@app.route("/api/system/info")
def api_system_info():
    return jsonify({
        "serial_port": SERIAL_PORT,  # detectado sozinho via /dev/serial/by-id -- não precisa configurar
        "midi_port_name": MIDI_PORT_NAME,  # porta virtual criada por este backend, sempre o mesmo nome
        "audio": kit_manager.get_audio_device_status(),
    })


@app.route("/api/system/audio", methods=["GET"])
def api_system_audio_get():
    return jsonify(kit_manager.get_audio_device_status())


@app.route("/api/system/audio", methods=["POST"])
def api_system_audio_set():
    body = request.get_json(silent=True) or {}
    device = body.get("device", "auto")
    status = kit_manager.set_audio_device(device)
    # só tem efeito de verdade da próxima vez que um kit for (re)carregado
    # -- não mexe no que já está tocando agora, pra não cortar o som no
    # meio de um show sem querer.
    return jsonify({"ok": True, **status})


# ---- WebSocket ----

@sock.route("/ws")
def ws_route(ws):
    ws_register(ws)
    try:
        # manda o snapshot atual assim que conecta
        ws.send(json.dumps({"type": "state", **snapshot()}, ensure_ascii=False))
        while True:
            msg = ws.receive(timeout=30)
            if msg is None:
                continue  # timeout de keepalive, segue esperando
    except Exception:
        pass
    finally:
        ws_unregister(ws)


# ----------------------------------------------------------------
# MAIN
# ----------------------------------------------------------------

if __name__ == "__main__":
    print("======================================")
    print(" DRUM BACKEND")
    print("======================================")
    print("Serial :", SERIAL_PORT)
    print("MIDI   : ESP32 Drum")
    print(f"Web    : http://0.0.0.0:{HTTP_PORT}/")
    print("======================================")

    threading.Thread(target=serial_reader_loop, daemon=True).start()

    # sincroniza a config assim que sobe, pra já ter cache pronto
    #
    # IMPORTANTE (bug corrigido aqui): antes disso era só UMA tentativa,
    # 0.3s depois de abrir a serial. A maioria das placas ESP32 REINICIA
    # sozinha assim que a porta serial é aberta (efeito do DTR, igual ao
    # Arduino) -- ou seja, bem na hora que esta thread tentava mandar o
    # "GET", o ESP32 podia estar no meio do próprio boot e não responder
    # nada. Sem uma segunda chance, pads/global/hi-hat ficavam vazios pro
    # resto da execução (só uma troca de valor pelo HTML disparava um novo
    # refresh, e mesmo assim só daquele campo específico) -- na prática
    # era isso que fazia a aba GLOBAL aparecer com valores errados/vazios
    # depois de religar o Raspberry ou o ESP32.
    #
    # Agora tenta várias vezes, com folga de sobra pro ESP32 terminar de
    # reiniciar e responder, e para assim que a sincronização funcionar.
    def _initial_sync():
        max_tentativas = 12
        for tentativa in range(1, max_tentativas + 1):
            time.sleep(0.3 if tentativa == 1 else 1.5)
            result = refresh_from_esp32()
            if result.get("ok"):
                print(f"[backend] sincronização inicial com o ESP32 OK (tentativa {tentativa}/{max_tentativas})")
                return
        print(
            f"[backend] aviso: não consegui sincronizar com o ESP32 na subida depois de "
            f"{max_tentativas} tentativas -- confira a conexão serial. Os valores mostrados "
            "no console podem ficar incompletos até uma sincronização manual (botão "
            "'Sincronizar agora' na aba SISTEMA, ou /api/refresh)."
        )

    threading.Thread(target=_initial_sync, daemon=True).start()

    # ---- DrumGizmo: sobe já com o kit ativo, pra ser o ÚNICO processo ----
    # Antes disso, o DrumGizmo era iniciado à mão num terminal separado,
    # então trocar de kit pelo HTML não tinha efeito nenhum no som -- o
    # processo manual continuava tocando por fora do kit_manager. Agora
    # o backend sobe o kit ativo sozinho aqui; não é mais preciso (nem
    # deve) rodar `drumgizmo ...` manualmente num terminal à parte.
    def _start_active_kit():
        time.sleep(1.0)  # dá tempo da serial/ALSA-seq do ESP32 assentar antes

        # No boot do Raspberry, o Linux pode terminar de configurar o
        # dispositivo USB de áudio um pouco DEPOIS deste script já ter
        # começado a rodar -- se o DrumGizmo tentar abrir a placa antes
        # dela existir, dá o mesmo erro de "snd_pcm_open failed" que já
        # vimos antes. Espera ela aparecer em /proc/asound/cards (até
        # 30s) antes de tentar.
        kit_manager.wait_for_soundcard(timeout=30)

        try:
            active = kit_manager.get_active_kit()
            if active:
                print("[backend] iniciando DrumGizmo com kit ativo:", active)
                kit_manager.start_kit(active)
        except Exception as e:
            print("[backend] falha ao iniciar o kit ativo do DrumGizmo:", e)

    threading.Thread(target=_start_active_kit, daemon=True).start()

    app.run(host="0.0.0.0", port=HTTP_PORT, threaded=True)

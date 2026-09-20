# SPDX-License-Identifier: GPL-3.0-or-later
"""
kit_manager.py
================================================================
Troca do kit ativo no DrumGizmo (matar qualquer instância antiga
-- inclusive uma iniciada manualmente num terminal --, iniciar a
nova já no diretório certo, com o midimap certo, e reconectar a
porta MIDI virtual "ESP32 Drum" nela).

*** IMPORTANTE: kit_manager agora é o ÚNICO responsável por rodar
o DrumGizmo. Não rode mais `drumgizmo ...` manualmente num
terminal separado -- se dois processos disputarem a mesma porta de
áudio/MIDI, trocar de kit pelo HTML não vai ter efeito nenhum no
som (o processo antigo continua tocando).

O drum_backend.py chama start_kit(active) uma vez, sozinho, ao
subir -- então a partir daí só existe UM DrumGizmo rodando, e ele é
sempre o que o kit_manager está de olho.

Cada kit em kits.json aponta pra uma PASTA (cwd) que contém o .xml
do kit e o midimap.xml correspondente -- exatamente como no comando
que já funcionava rodado à mão:

    cd ~/DrumGizmo/kits/kits/test
    drumgizmo -i alsamidi -I "midimap=midimap.xml" -o alsa test.xml
================================================================
"""

import json
import os
import re
import subprocess
import threading
import time

KITS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kits.json")
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "drumgizmo.log")

_lock = threading.Lock()
_process = None  # subprocess.Popen ativo, ou None


def _load():
    with open(KITS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(data):
    with open(KITS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def list_kits():
    data = _load()
    return {"active": data.get("active"), "kits": sorted(data.get("kits", {}).keys())}


def wait_for_soundcard(timeout=30):
    """
    Espera até algum cartão de som USB aparecer em /proc/asound/cards
    (procura pela string "USB-Audio", que é o driver ALSA padrão pra
    qualquer interface de áudio USB, então não depende do nome exato
    da sua placa). Existe pra evitar a corrida clássica no boot: o
    Raspberry pode terminar de subir este script antes do Linux
    terminar de enumerar a placa de som USB, e o DrumGizmo abrindo o
    ALSA cedo demais dá o mesmo erro de "snd_pcm_open failed" que já
    apareceu antes.

    Best-effort: se não achar dentro do timeout, loga um aviso e
    segue em frente mesmo assim (pode ser só um nome de driver
    diferente do esperado -- não trava o sistema todo por causa
    disso).
    """
    inicio = time.time()
    while time.time() - inicio < timeout:
        try:
            with open("/proc/asound/cards", "r", encoding="utf-8", errors="replace") as f:
                conteudo = f.read()
            if "USB-Audio" in conteudo or "usb-audio" in conteudo.lower():
                return True
        except Exception:
            pass
        time.sleep(0.5)

    print(
        f"[kit_manager] aviso: não vi nenhuma placa 'USB-Audio' em /proc/asound/cards "
        f"depois de {timeout}s -- seguindo mesmo assim (pode já estar lá com outro nome "
        "de driver, ou pode dar erro de ALSA ao abrir o DrumGizmo)."
    )
    return False


_CARD_LINE_RE = re.compile(r"^\s*(\d+)\s*\[([^\]]*?)\s*\]:\s*(.+)$")


def detect_usb_audio_cards():
    """
    Lê /proc/asound/cards e devolve a lista de placas de som USB
    encontradas nesta máquina agora, cada uma como:
        {"index": 2, "id": "Device", "description": "USB-Audio - USB PnP Sound Device"}

    "id" é o nome curto do ALSA (o mesmo que aparece entre colchetes no
    `aplay -l`, e o que se usa em "plughw:<id>") -- por isso essa função
    existe: em vez de fixar esse nome no kits.json (que só serve pra
    ESTA placa, nesta máquina), dá pra descobrir sozinho toda vez que o
    backend sobe, funcionando em qualquer Raspberry/placa USB diferente
    sem editar nada.
    """
    try:
        with open("/proc/asound/cards", "r", encoding="utf-8", errors="replace") as f:
            linhas = f.readlines()
    except Exception:
        return []

    cards = []
    for linha in linhas:
        m = _CARD_LINE_RE.match(linha.rstrip("\n"))
        if not m:
            continue
        idx, card_id, desc = m.groups()
        if "usb" in desc.lower():
            cards.append({"index": int(idx), "id": card_id, "description": desc.strip()})
    return cards


def resolve_alsa_device(data=None):
    """
    Decide o valor real pra usar em '-O dev=...' na hora de abrir o
    DrumGizmo.

    - Se 'alsa_device' em kits.json estiver configurado manualmente
      (qualquer valor que não seja "auto"/vazio), usa exatamente esse
      valor -- é o que a aba Sistema grava quando alguém escolhe a
      placa na mão (último recurso, quando a detecção sozinha não dá
      conta, ex: mais de uma placa USB conectada ao mesmo tempo).
    - Senão (o normal, pra um sistema recém-instalado em qualquer
      Raspberry), tenta detectar sozinho: se existir EXATAMENTE UMA
      placa USB-Audio conectada -- o caso comum --, usa ela
      ("plughw:<id>"), sem precisar de nada fixo no kits.json.
    - Se não achar nenhuma ou achar mais de uma (ambíguo -- não dá pra
      saber qual das duas é a certa sozinho), não força nada: deixa o
      DrumGizmo cair no PCM "default" dele (comportamento de quando
      não existia esse recurso) e avisa no log que dá pra configurar
      manualmente na aba Sistema.
    """
    if data is None:
        data = _load()

    configured = (data.get("alsa_device") or "").strip()
    if configured and configured.lower() != "auto":
        return configured

    cards = detect_usb_audio_cards()
    if len(cards) == 1:
        return f"plughw:{cards[0]['id']}"

    if len(cards) == 0:
        print(
            "[kit_manager] aviso: nenhuma placa USB-Audio detectada em "
            "/proc/asound/cards -- usando o dispositivo ALSA padrão do sistema. "
            "Se não sair som, escolha a placa manualmente na aba Sistema."
        )
    else:
        nomes = ", ".join(f"{c['id']!r} (card {c['index']})" for c in cards)
        print(
            f"[kit_manager] aviso: mais de uma placa USB-Audio detectada ({nomes}) -- "
            "não dá pra escolher sozinho qual usar. Escolha manualmente na aba Sistema."
        )
    return None


def _set_usb_mixer_max():
    """
    Sobe os controles de volume da placa de som USB (PCM/Master/
    Speaker/Headphone/Playback, o que existir) pro máximo, e desmuta
    -- toda vez que um kit é iniciado.

    Por quê: a placa de som USB não tem NVRAM própria com "o volume que
    eu deixei ontem" -- depois de reiniciar o Raspberry, o ALSA volta a
    placa pro que estiver salvo em /var/lib/alsa/asound.state (se
    alguém rodou 'alsactl store' alguma vez) ou, se nunca foi salvo,
    pro padrão de fábrica da placa -- e nenhum dos dois garante estar
    no máximo. Isso já causou volume geral mais baixo depois de um
    reboot, com a interface web mostrando tudo "no máximo" mesmo assim
    -- porque o que a interface/ESP32 controlam é a intensidade do
    toque (velocity), não esse ganho analógico de saída da placa, que
    fica numa camada totalmente separada (ALSA/hardware).

    Em vez de depender de 'alsactl store' ter sido rodado uma vez (e
    ficar torcendo pra continuar valendo depois de trocar de placa/Pi),
    a correção sai daqui: garante o volume máximo sozinho toda vez que
    um kit sobe, em qualquer placa USB, sem precisar configurar nada.
    Melhor esforço só -- se não achar exatamente 1 placa USB, ou o
    'amixer' falhar por qualquer motivo, não impede o kit de iniciar,
    só avisa no log.
    """
    cards = detect_usb_audio_cards()
    if len(cards) != 1:
        return  # 0 ou >1 placas: mesma ambiguidade de resolve_alsa_device, não arrisca adivinhar

    idx = cards[0]["index"]
    try:
        out = subprocess.run(
            ["amixer", "-c", str(idx), "scontrols"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception as e:
        print(f"[kit_manager] aviso: não consegui listar os controles de mixer da placa {idx} ({e}) -- volume de saída não ajustado.")
        return

    nomes = re.findall(r"'([^']+)'", out.stdout or "")
    alvo_substrings = ("pcm", "speaker", "master", "headphone", "playback")
    ajustados = []
    for nome in nomes:
        if not any(s in nome.lower() for s in alvo_substrings):
            continue  # ignora controles que não parecem de volume de saída (ex: switches de captura/ganho de mic)
        try:
            subprocess.run(
                ["amixer", "-c", str(idx), "sset", nome, "100%", "unmute"],
                capture_output=True, text=True, timeout=5,
            )
            ajustados.append(nome)
        except Exception:
            pass  # melhor esforço -- um controle que não aceita esse formato não deve travar o início do kit

    if ajustados:
        print(f"[kit_manager] volume de saída da placa USB (card {idx}) ajustado pro máximo: {', '.join(ajustados)}")
    else:
        print(f"[kit_manager] aviso: nenhum controle de volume reconhecido na placa USB (card {idx}) -- confira com 'alsamixer -c {idx}' se o volume de saída está baixo.")


def get_audio_device_status():
    """Pra tela de Configurações/Sistema: o que está salvo, o que
    realmente vai ser usado agora, e o que foi detectado nesta máquina."""
    data = _load()
    configured = (data.get("alsa_device") or "").strip() or "auto"
    return {
        "configured": configured,
        "effective": resolve_alsa_device(data),
        "detected": detect_usb_audio_cards(),
    }


def set_audio_device(value):
    """
    Salva a escolha manual de placa de som em kits.json.
    `value`: "auto" (volta a detectar sozinho) ou algo tipo
    "plughw:NomeDaPlaca" (força uma placa específica -- último recurso,
    pra quando a detecção automática não dá conta, ex: mais de uma
    placa USB conectada).
    """
    value = (value or "").strip()
    data = _load()
    data["alsa_device"] = value if value and value.lower() != "auto" else "auto"
    _save(data)
    return get_audio_device_status()


def get_active_kit():
    return _load().get("active")


def get_active_kit_paths():
    """Pra quem precisa saber ONDE estão os arquivos do kit tocando agora
    (ex: apply_pad_pan.py, que precisa achar o .xml do instrumento) --
    devolve None se o kit ativo não usar o formato 'cwd/kit_xml/midimap'
    (ex: um kit configurado via 'launch_cmd' na unha)."""
    data = _load()
    name = data.get("active")
    if not name:
        return None
    kit_cfg = data.get("kits", {}).get(name)
    if not kit_cfg or "kit_xml" not in kit_cfg or not kit_cfg.get("midimap"):
        return None
    return {
        "name": name,
        "cwd": os.path.expanduser(os.path.expandvars(kit_cfg.get("cwd") or "")),
        "kit_xml": kit_cfg["kit_xml"],
        "midimap": kit_cfg["midimap"],
    }


def _stop_locked():
    global _process
    if _process is not None and _process.poll() is None:
        try:
            _process.terminate()
            _process.wait(timeout=3)
        except Exception:
            try:
                _process.kill()
            except Exception:
                pass
    _process = None

    # Rede de segurança: mata qualquer OUTRA instância de drumgizmo que
    # esteja rodando por fora do kit_manager (ex: alguém deixou um
    # terminal manual aberto, ou uma instância "órfã" de uma queda
    # anterior). Sem isso, trocar de kit pelo HTML pode simplesmente
    # abrir um SEGUNDO drumgizmo enquanto o antigo continua tocando --
    # e por fora parece que "nada mudou".
    try:
        subprocess.run(["pkill", "-f", "drumgizmo"], timeout=3)
    except Exception as e:
        print("[kit_manager] aviso: não consegui rodar 'pkill -f drumgizmo':", e)
        return

    # Dá um tempo pro processo antigo realmente sumir e o ALSA soltar o
    # dispositivo de áudio -- se o kit novo sobe rápido demais, ele pode
    # achar a placa de som ainda "presa" pelo processo que acabou de
    # morrer (já vimos exatamente esse erro antes: "snd_pcm_open failed").
    # Confere com pgrep em vez de só dormir um tempo fixo: se sumiu rápido,
    # segue na hora; se o DrumGizmo ficar de zumbi (já aconteceu antes) e
    # não morrer com SIGTERM, força com SIGKILL antes de desistir.
    for _ in range(10):  # até ~2s
        still = subprocess.run(["pgrep", "-f", "drumgizmo"], capture_output=True, timeout=2)
        if still.returncode != 0:  # pgrep não achou nada -> já sumiu
            break
        time.sleep(0.2)
    else:
        print("[kit_manager] processo antigo não morreu com SIGTERM -- forçando com SIGKILL")
        try:
            subprocess.run(["pkill", "-9", "-f", "drumgizmo"], timeout=3)
        except Exception as e:
            print("[kit_manager] aviso: falha no SIGKILL de limpeza:", e)
        time.sleep(0.5)  # tempo do ALSA soltar o dispositivo de vez


def stop_kit():
    """Encerra o DrumGizmo em execução, se houver."""
    with _lock:
        _stop_locked()


def _read_log_tail(n=25):
    """Últimas `n` linhas de drumgizmo.log, pra colocar direto na mensagem
    de erro quando o processo cai logo depois de abrir."""
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            linhas = f.readlines()
        return "".join(linhas[-n:]).strip() or "(log vazio)"
    except Exception as e:
        return f"(não consegui ler {LOG_FILE}: {e})"


_PROGRESS_RE = re.compile(r"^\s*(\d+)\s+of\s+(\d+)\s*$", re.IGNORECASE)


def _wait_for_log_marker(start_offset, marker, timeout, on_progress=None):
    """
    Espera até uma linha igual a `marker` (ex: "done") aparecer em
    drumgizmo.log a partir de `start_offset` -- ou seja, só o que foi
    escrito NESTA execução, ignorando "done"s de kits carregados antes.
    Retorna True se achou, False se estourou o timeout.

    De quebra, enquanto espera, reconhece as linhas de progresso que o
    próprio DrumGizmo escreve durante o carregamento ("123 of 9660") e
    chama `on_progress(atual, total)` pra cada uma -- é o mesmo número
    que aparece no terminal com `tail -f drumgizmo.log`, só que
    encaminhado pro backend/HTML em vez de só ficar no arquivo.
    """
    inicio = time.time()
    ultimo_progresso = None
    while time.time() - inicio < timeout:
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
                f.seek(start_offset)
                conteudo = f.read()
            linhas = conteudo.splitlines()

            if on_progress:
                # só interessa a ÚLTIMA linha de progresso vista até agora
                # (as anteriores já ficaram obsoletas) -- evita mandar uma
                # enxurrada de mensagens WebSocket atrasadas de uma vez só.
                for linha in reversed(linhas):
                    m = _PROGRESS_RE.match(linha.strip())
                    if m:
                        atual, total = int(m.group(1)), int(m.group(2))
                        if (atual, total) != ultimo_progresso:
                            ultimo_progresso = (atual, total)
                            try:
                                on_progress(atual, total)
                            except Exception as e:
                                print("[kit_manager] erro no callback on_progress:", e)
                        break

            if any(linha.strip().lower() == marker.lower() for linha in linhas):
                return True
        except Exception:
            pass
        time.sleep(0.3 if on_progress else 1.0)
    return False


def _reconnect_midi(midi_port_name, quiet=False):
    """
    Tenta reconectar a porta ALSA-seq virtual do ESP32 na entrada
    MIDI do DrumGizmo, via `aconnect`. Isso é necessário porque a
    cada vez que o DrumGizmo reinicia, ele cria um cliente ALSA-seq
    novo (id diferente), então a conexão manual anterior se perde.

    Retorna True se conectou (ou já estava conectado), False se os
    dois clientes esperados ainda não apareceram em `aconnect -l`
    (chamador pode tentar de novo mais tarde) -- com `quiet=True` não
    loga esse caso de "ainda não apareceu", só sucesso ou erro real.

    IMPORTANTE: o nome configurado (`midi_port_name`, ex: "ESP32 Drum")
    é o nome da PORTA dentro de um cliente ALSA-seq, não o nome do
    cliente em si -- o rtmidi cria o cliente com um nome genérico
    ("RtMidiOut Client"), e é a porta 0 dentro dele que se chama
    "ESP32 Drum". Por isso o parsing abaixo olha as linhas de PORTA
    (indentadas, tipo "    0 'ESP32 Drum      '"), não as de cliente,
    pra achar a origem -- senão nunca bate com nome nenhum de cliente.

    Se isso não reconectar sozinho na sua instalação, rode
    `aconnect -l` e conecte manualmente pra descobrir o nome real
    do cliente MIDI-in do DrumGizmo (pode não se chamar "DrumGizmo").
    """
    try:
        out = subprocess.run(["aconnect", "-l"], capture_output=True, text=True, timeout=3).stdout
    except Exception as e:
        print("[kit_manager] aviso: não consegui rodar 'aconnect -l':", e)
        return False

    src_client = None
    dst_client = None
    current_client_id = None

    for raw_line in out.splitlines():
        line = raw_line.strip()

        if line.startswith("client "):
            # linha de CLIENTE, ex: "client 128: 'RtMidiOut Client' [type=user,pid=1234]"
            try:
                cid = line.split("client ")[1].split(":")[0].strip()
                client_name = line.split("'")[1] if "'" in line else ""
            except Exception:
                continue

            current_client_id = cid
            if "drumgizmo" in client_name.lower():
                dst_client = cid
            continue

        # linha de PORTA dentro do cliente atual, ex: "0 'ESP32 Drum      '"
        if line and line[0].isdigit() and "'" in line and current_client_id is not None:
            try:
                port_name = line.split("'")[1].strip()
            except Exception:
                continue
            if port_name == midi_port_name:
                src_client = current_client_id

    if src_client and dst_client:
        try:
            subprocess.run(["aconnect", f"{src_client}:0", f"{dst_client}:0"], timeout=3)
            print(f"[kit_manager] MIDI reconectado: {src_client}:0 -> {dst_client}:0")
        except Exception as e:
            print("[kit_manager] falha ao rodar 'aconnect' de conexão:", e)
            return False
        return True

    if not quiet:
        print(
            "[kit_manager] aviso: não encontrei os dois clientes ALSA-seq "
            f"esperados (origem={midi_port_name!r}, destino contendo 'drumgizmo'). "
            f"Veja {LOG_FILE} pra checar se o DrumGizmo terminou de subir sem erro, "
            "ou reconecte manualmente com 'aconnect -l' + 'aconnect <origem> <destino>'."
        )
    return False


def _build_cmd(kit_cfg, alsa_device=None):
    """
    Monta a linha de comando real do DrumGizmo a partir da entrada de
    kits.json. Aceita dois formatos:

    1) formato novo (recomendado) -- pasta + nome dos arquivos:
       {
         "cwd": "/home/<usuario>/DrumGizmo/kits/kits/test",
         "kit_xml": "test.xml",
         "midimap": "midimap.xml"
       }
       -> roda exatamente como testado à mão:
          (cwd) drumgizmo -i alsamidi -I "midimap=midimap.xml" -o alsa
                -O "dev=<alsa_device>" test.xml

    2) formato antigo "launch_cmd" (lista de argv já pronta), pra quem
       quiser montar o comando na unha -- nesse caso 'cwd' também é
       respeitado se vier no mesmo bloco, e 'alsa_device' é ignorado
       (quem usa launch_cmd já está montando tudo na unha).

    `alsa_device`: nome/endereço ALSA da placa de som (ex: "plughw:Device"),
    lido de kits.json (chave "alsa_device"). Sem isso, o DrumGizmo usaria
    o PCM "default" -- que em muitas instalações com PipeWire é
    redirecionado pro PipeWire mesmo com o PipeWire mascarado/parado, e
    o "hw:" puro (sem "plug") exige que o formato/taxa bata exatamente
    com o que a placa aceita nativamente, o que já vimos falhar com
    "snd_pcm_hw_params_set_format failed". "plughw:" resolve os dois
    problemas de uma vez (fala direto com o hardware, com conversão
    automática de formato/taxa quando precisa).
    """
    if "kit_xml" in kit_cfg:
        cwd = kit_cfg.get("cwd")

        # confere os arquivos ANTES de tentar rodar -- assim um
        # kits.json com caminho errado dá um erro claro na hora
        # ("arquivo tal não existe"), em vez do DrumGizmo abrir,
        # reclamar de um jeito genérico e morrer, ou o Popen falhar
        # com um FileNotFoundError críptico apontando pro cwd.
        if cwd and not os.path.isdir(cwd):
            raise ValueError(f"pasta do kit não existe: {cwd!r} (confira 'cwd' em kits.json)")

        for campo in ("kit_xml", "midimap"):
            nome = kit_cfg.get(campo)
            if not nome:
                continue
            caminho = os.path.join(cwd, nome) if cwd else nome
            if not os.path.isfile(caminho):
                raise ValueError(
                    f"arquivo '{campo}' não encontrado: {caminho!r} "
                    f"(confira '{campo}' e 'cwd' em kits.json)"
                )

        cmd = ["drumgizmo", "-i", "alsamidi"]
        if kit_cfg.get("midimap"):
            cmd += ["-I", f"midimap={kit_cfg['midimap']}"]
        cmd += ["-o", "alsa"]
        # IMPORTANTE: sempre fixa 'srate=44100' explicitamente. O
        # 'drumgizmo --help' desta versao (v0.9.20) mostra o default do
        # driver alsa como "srate=441000" (dez vezes 44100, parece bug/typo
        # do proprio binario) -- sem passar isso na mao, o DrumGizmo tenta
        # abrir a placa a 441000Hz, o "plughw" tenta reamostrar um valor
        # absurdo e o resultado e silencio total (sem erro nenhum no log).
        # Passar o valor certo aqui resolve pra qualquer placa de som.
        out_parms = "srate=44100"
        if alsa_device:
            out_parms = f"dev={alsa_device},{out_parms}"
        cmd += ["-O", out_parms]
        cmd += [kit_cfg["kit_xml"]]
        return cmd

    if "launch_cmd" in kit_cfg:
        return list(kit_cfg["launch_cmd"])

    raise ValueError("kit sem 'kit_xml' nem 'launch_cmd' em kits.json")


_ALSA_BUSY_HINTS = ("snd_pcm_open failed", "failed init engine")


def _spawn(name, cmd, cwd):
    """Sobe um novo processo do DrumGizmo (usado tanto na primeira
    tentativa quanto num relançamento automático). Devolve
    (processo, log_offset) -- log_offset marca onde, no log, começa
    a saída DESTA tentativa específica."""
    global _process
    log_fh = open(LOG_FILE, "a", buffering=1)
    log_fh.write(f"\n\n==== iniciando kit {name!r} : {cmd} (cwd={cwd}) ====\n")
    offset = log_fh.tell()
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=log_fh, stderr=subprocess.STDOUT)
    log_fh.close()  # o filho já tem sua própria cópia do fd; não precisa ficar aberto aqui
    _process = proc
    return proc, offset


def _reapply_saved_volumes(name, kit_cfg, cwd):
    """Antes de QUALQUER kit começar a tocar, reaplica nos arquivos de
    áudio dele os volumes por pad já salvos em pad_mixer.json (ver
    pad_mixer.py e apply_pad_gain.py). Sem isso, o ganho só ficava
    valendo enquanto os arquivos daquele kit continuassem exatamente
    como apply_pad_gain.py os deixou -- bastava reiniciar o Raspberry
    numa hora ruim, ou (principalmente) reprocessar as amostras do
    zero (setup_kits.py --force, que sempre parte da fonte original)
    pra "perder" o ajuste, mesmo com o número certo salvo o tempo
    todo. Chamando isso aqui, toda vez que um kit é carregado -- no
    boot, ao trocar de kit pela tela, ou depois de reprocessar as
    amostras -- os volumes voltam sozinhos, sem precisar mexer nos
    sliders de novo.

    IMPORTANTE (2026-09-19): o volume agora é guardado POR KIT (ver
    pad_mixer.py) -- por isso `name` (o nome do kit sendo carregado
    agora, igual aparece em kits.json) é obrigatório aqui: só busca e
    reaplica os ajustes feitos NESSE kit especificamente, nunca os de
    outro. Cada kit tem seu próprio conjunto de instrumentos, então uma
    nota que não existe no midimap desse kit em particular não é erro
    -- só quer dizer que essa peça não está presente nele. Uma falha
    num instrumento nunca deve impedir o kit de carregar; só avisa no
    log e segue pros outros.
    """
    if "kit_xml" not in kit_cfg or not kit_cfg.get("midimap") or not cwd:
        return  # kit configurado via launch_cmd na unha -- não dá pra reaplicar nada

    try:
        import pad_mixer
        import apply_pad_gain as gain_mod
    except Exception as e:
        print("[kit_manager] aviso: não consegui importar pad_mixer/apply_pad_gain:", e)
        return

    entradas = pad_mixer.get_all(name)
    if not entradas:
        return

    kit_xml_path = os.path.join(cwd, kit_cfg["kit_xml"])
    midimap_path = os.path.join(cwd, kit_cfg["midimap"])
    if not os.path.isfile(kit_xml_path) or not os.path.isfile(midimap_path):
        return

    aplicados = 0
    for nota_s, entry in entradas.items():
        volume = entry.get("volume", 1.0)
        if volume <= 0.0:
            continue  # mudo -- não mexe em arquivo nenhum (ver _aplicar_volume_pad)
        try:
            nota = int(nota_s)
            instr_name = gain_mod.find_instrument_name(midimap_path, nota)
            if not instr_name:
                continue  # essa peça não existe nesse kit -- normal, não é erro
            instr_file = gain_mod.find_instrument_file(kit_xml_path, instr_name)
            if not instr_file:
                continue
            instr_xml_path = os.path.join(cwd, instr_file)
            files = gain_mod.collect_all_files(instr_xml_path)
            if not files:
                continue
            gain_mod.apply_gain_to_files(files, volume)
            aplicados += 1
        except Exception as e:
            print(f"[kit_manager] aviso: falha ao reaplicar volume salvo da nota {nota_s}: {e}")

    if aplicados:
        print(f"[kit_manager] volume por pad reaplicado em {aplicados} instrumento(s) antes de carregar o kit {kit_cfg.get('kit_xml')!r}.")


def start_kit(name, on_ready=None, on_progress=None):
    """
    Inicia o DrumGizmo com o kit `name` (deve existir em kits.json).

    `on_ready(ok, message)`, se passado, é chamado numa thread de
    fundo assim que dá pra saber se o kit REALMENTE terminou de
    carregar e reconectar o MIDI (ok=True) ou não (ok=False) -- isso
    pode demorar bem mais que a resposta desta função (que só confirma
    que o processo subiu sem crashar na hora, não que já está pronto
    pra tocar). Kits grandes (Ludwig/Crocell) podem levar dezenas de
    segundos num Pi 3B+; sem esse aviso separado, a interface achava
    que "carregou" antes de estar tocável de verdade.

    `on_progress(atual, total)`, se passado, é chamado repetidamente
    enquanto o kit carrega, com o mesmo número que aparece no terminal
    ("123 of 9660") -- pra alimentar uma barra de progresso na
    interface em vez de só um texto de "pode demorar".
    """
    global _process

    data = _load()
    kits = data.get("kits", {})

    if name not in kits:
        raise ValueError(f"Kit desconhecido: {name!r}. Disponíveis: {sorted(kits.keys())}")

    kit_cfg = dict(kits[name])
    if kit_cfg.get("cwd"):
        # expande "~" (e variáveis tipo $HOME) pra funcionar em qualquer
        # máquina/usuário -- assim o kits.json pode usar "~/DrumGizmo/..."
        # em vez de um caminho fixo tipo "/home/<usuario>/DrumGizmo/...".
        kit_cfg["cwd"] = os.path.expanduser(os.path.expandvars(kit_cfg["cwd"]))
    alsa_device = resolve_alsa_device(data)
    _set_usb_mixer_max()
    cmd = _build_cmd(kit_cfg, alsa_device=alsa_device)
    cwd = kit_cfg.get("cwd") or None

    # garante que os volumes por pad já salvos estejam de fato nos
    # arquivos de áudio ANTES do DrumGizmo os carregar (ver
    # _reapply_saved_volumes) -- é o que faz o ajuste sobreviver a
    # reboot, troca de kit e reprocessamento de amostras sem precisar
    # repetir nada na tela.
    _reapply_saved_volumes(name, kit_cfg, cwd)

    midi_port_name = data.get("midi_port_name", "ESP32 Drum")

    with _lock:
        _stop_locked()

        print("[kit_manager] iniciando kit", name, "->", cmd, "(cwd=%s)" % cwd)
        # a saída do DrumGizmo vai pro log (drumgizmo.log, do lado deste
        # arquivo) em vez de ser descartada -- se ele crashar ou der erro
        # ao subir, dá pra ver o motivo com `tail -40 drumgizmo.log`.
        p, log_offset = _spawn(name, cmd, cwd)

        data["active"] = name
        _save(data)

    # checagem de saúde rápida: se o DrumGizmo crashar logo de cara (ex:
    # kit_xml/midimap errado em kits.json), ele normalmente morre em bem
    # menos de 1s -- sem isso, o HTML mostrava "carregado" mesmo quando
    # na prática não subiu nada e o som parava.
    time.sleep(0.8)
    if p.poll() is not None:
        raise RuntimeError(
            f"DrumGizmo encerrou sozinho logo após abrir (código {p.returncode}). "
            f"Últimas linhas de {LOG_FILE}:\n{_read_log_tail()}"
        )

    # A porta MIDI ALSA-seq do DrumGizmo só existe depois dele terminar
    # de carregar TODAS as amostras do kit -- e isso pode levar bem mais
    # que alguns segundos num Raspberry Pi 3B+ com um kit grande (o
    # Ludwig tem 1332 arquivos: já vimos ele demorar mais que a antiga
    # janela fixa de ~10s, fazendo a reconexão desistir cedo demais,
    # antes da porta sequer existir). Em vez de um tempo fixo, espera
    # aparecer a linha "done" que o próprio DrumGizmo escreve no log
    # quando termina de carregar -- só então começa a tentar conectar.
    #
    # IMPORTANTE: "done" só marca o fim do CARREGAMENTO das amostras --
    # o DrumGizmo só tenta abrir a placa de som DEPOIS disso, e é aí que
    # já vimos ele falhar com "snd_pcm_open failed" (placa ainda presa
    # por outra coisa, ex: PipeWire). Por isso, se crashar bem nesse
    # ponto, tenta de novo sozinho (até 2 vezes) antes de desistir --
    # esse tipo de erro costuma ser passageiro.
    def _notify(ok, message):
        if on_ready:
            try:
                on_ready(ok, message)
            except Exception as e:
                print("[kit_manager] erro no callback on_ready:", e)

    def _delayed_reconnect():
        nonlocal p, log_offset
        max_relancamentos = 2
        relancamentos = 0

        while True:
            if p is not _process:
                return  # outra troca de kit já aconteceu, essa checagem ficou obsoleta

            # 900s (15min) de folga -- kits grandes de verdade (ex: Crocell,
            # ~9660 amostras) podem demorar vários minutos pra carregar num
            # Raspberry Pi 3B+ lendo do cartão SD. 180s já se mostrou curto
            # demais e fazia desistir de esperar antes do kit terminar.
            if not _wait_for_log_marker(log_offset, "done", timeout=900, on_progress=on_progress):
                print(
                    f"[kit_manager] aviso: não vi 'done' aparecer em {LOG_FILE} em até 900s (15min) -- "
                    "kit pode ainda estar carregando (kit muito grande / Pi lento) ou ter travado. "
                    "Vou tentar reconectar o MIDI mesmo assim."
                )

            if p is not _process:
                return

            if p.poll() is None:
                break  # carregou (ou desistimos de esperar) e continua vivo -- segue pra reconexão MIDI

            tail = _read_log_tail()
            parece_placa_ocupada = any(h in tail.lower() for h in _ALSA_BUSY_HINTS)

            if parece_placa_ocupada and relancamentos < max_relancamentos:
                relancamentos += 1
                print(
                    f"[kit_manager] DrumGizmo encerrou (código {p.returncode}) com jeito de "
                    f"placa de som ocupada -- tentando de novo sozinho ({relancamentos}/{max_relancamentos})..."
                )
                time.sleep(2.0)  # dá um respiro pra quem estiver segurando o dispositivo soltar
                with _lock:
                    if p is not _process:
                        return  # trocou de kit de novo enquanto esperava, desiste desta tentativa
                    p, log_offset = _spawn(name, cmd, cwd)
                continue  # volta pro topo do laço e espera esse novo processo

            msg = f"DrumGizmo encerrou sozinho (código {p.returncode}) -- veja {LOG_FILE}."
            print(f"[kit_manager] aviso: {msg}")
            _notify(False, msg)
            return

        # a partir daqui o "done" já apareceu (ou desistimos de esperar) e o
        # processo continua vivo -- tenta reconectar o MIDI a cada 1s por
        # mais ~15s, dando uma folga pra porta ALSA-seq terminar de ser
        # registrada logo depois do "done".
        for tentativa in range(15):
            if p is not _process:
                return
            if _reconnect_midi(midi_port_name, quiet=True):
                _notify(True, "kit carregado e MIDI reconectado")
                return
            time.sleep(1.0)
        # última tentativa: agora sim loga o aviso detalhado se não achou
        if _reconnect_midi(midi_port_name, quiet=False):
            _notify(True, "kit carregado e MIDI reconectado")
        else:
            _notify(False, f"kit carregou mas não consegui reconectar o MIDI -- veja {LOG_FILE} / 'aconnect -l'")

    threading.Thread(target=_delayed_reconnect, daemon=True).start()

    return {"kit": name, "pid": p.pid}


def switch_kit(name, on_ready=None, on_progress=None):
    """Troca pro kit `name`: para o atual (se houver) e inicia o novo."""
    return start_kit(name, on_ready=on_ready, on_progress=on_progress)


def is_running():
    return _process is not None and _process.poll() is None

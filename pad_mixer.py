# SPDX-License-Identifier: GPL-3.0-or-later
"""
pad_mixer.py
================================================================
Volume por pad (peça), guardado neste arquivo no Raspberry -- NÃO no
ESP32. É um conceito diferente da sensibilidade do trigger (threshold/
vel.máxima/curva/etc, já controlada pelo ESP32 -- ver PAD em
drum_backend.py e o protocolo PAD do firmware): aqui é sobre como a
nota que JÁ CHEGOU é tocada, não sobre como o piezo é lido.

IMPORTANTE (2026-09-19): o volume agora é guardado POR KIT, não mais
um valor único global pra cada nota. Motivo: cada kit tem seus
próprios arquivos de áudio gravados com volumes relativos diferentes
entre si -- o Crash de um kit pode já vir bem mais alto que o de
outro -- então faz sentido poder deixar, por exemplo, a Crash a 140%
só na Crocell e a 100% (original) em todos os outros kits, sem que um
ajuste "vaze" pros demais. Estrutura do arquivo agora:

    {
      "<nome do kit>": {
        "<nota MIDI>": {"volume": 1.4},
        ...
      },
      ...
    }

Um kit que nunca teve nenhum ajuste feito simplesmente não aparece
aqui -- toda peça dele vale 100% (original) até alguém mexer,
kit por kit.

Chave de nota: nota MIDI da peça (a mesma que o ESP32 já manda pra ela
hoje, ou a nota resultante de uma "definição manual"/MIDI-learn).

  volume: 0.0 (mudo de verdade -- a nota nem chega a ser tocada, ver
          drum_backend.py) .. 1.0 (100%, padrão) .. 3.0 (300%, reforça
          bastante uma peça fraca).

          IMPORTANTE (2026-09-18): valores diferentes de 0.0 NÃO são
          aplicados escalando a velocity do MIDI na hora -- essa 1a
          versão dava um efeito pequeno demais pra nivelar peças
          gravadas com volumes bem diferentes entre si (ex: um Crash
          bem mais alto que um Tom), porque o DrumGizmo não tem um
          controle de ganho de verdade por instrumento (só "power" pra
          escolher qual amostra tocar, não é volume). O valor aqui só
          é o que fica GUARDADO; quem aplica de fato é
          apply_pad_gain.py, reprocessando os .wav do instrumento com
          um ganho real e recarregando o kit -- por isso, diferente de
          um simples toggle, mudar o volume (exceto pra 0%/mudo)
          demora alguns segundos e não é instantâneo (ver a rota
          /api/mixer/<nota>/volume em drum_backend.py). Essa parte
          continua guardando só o número escolhido, não o áudio
          processado; o áudio processado fica nos próprios arquivos do
          kit correspondente (ver apply_pad_gain.py sobre o backup
          ".vol_original").

          IMPORTANTE (2026-09-18, parte 2): kit_manager.py reaplica
          automaticamente TODOS os volumes salvos aqui (só os DESSE
          kit, ver get_all(kit_name)) nos arquivos dele toda vez que
          esse kit é carregado -- no boot do Raspberry, ao trocar de
          kit pela tela, e depois de reprocessar as amostras do zero
          (setup_kits.py --force) -- ver
          kit_manager._reapply_saved_volumes(). Por isso não precisa
          repetir os ajustes de volume depois de nenhuma dessas coisas;
          só este arquivo (o número escolhido) precisa continuar
          salvo, o resto é automático.

(O balanço/pan esquerda-direita por pad foi removido -- exigia
reprocessar os .wav do instrumento a cada ajuste e, num dos kits reais
testados, o instrumento usava o mesmo arquivo pros dois canais,
tornando o recurso não confiável o suficiente pra manter.)
"""
import json
import os

MIXER_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pad_mixer.json")

_DEFAULT = {"volume": 1.0}


def _load():
    if not os.path.isfile(MIXER_FILE):
        return {}
    try:
        with open(MIXER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[pad_mixer] aviso: não consegui ler {MIXER_FILE} ({e}) -- assumindo vazio")
        return {}


def _save(data):
    with open(MIXER_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_all(kit_name):
    """{nota(str): {volume}, ...} -- só as peças já ajustadas alguma vez
    NESSE kit específico (peças nunca tocadas na aba de mixagem não
    aparecem aqui, mas vale o padrão {volume:1.0} mesmo assim). Um kit
    que nunca teve ajuste nenhum devolve {} (dict vazio), não erro."""
    if not kit_name:
        return {}
    return _load().get(kit_name, {})


def get(note, kit_name):
    if not kit_name:
        return dict(_DEFAULT)
    data = _load()
    entry = data.get(kit_name, {}).get(str(note), {})
    return {**_DEFAULT, **entry}


def get_volume_factor(note, kit_name):
    return get(note, kit_name)["volume"]


def set_volume(note, value, kit_name):
    if not kit_name:
        raise ValueError("set_volume precisa saber de qual kit -- 'kit_name' não pode ser vazio")
    value = max(0.0, min(3.0, float(value)))
    data = _load()
    kit_bucket = data.setdefault(kit_name, {})
    key = str(note)
    entry = {**_DEFAULT, **kit_bucket.get(key, {})}
    entry["volume"] = value
    kit_bucket[key] = entry
    _save(data)
    return entry

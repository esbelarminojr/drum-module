# SPDX-License-Identifier: GPL-3.0-or-later
"""
pad_mixer.py
================================================================
Volume por pad (peça), guardado neste arquivo no Raspberry -- NÃO no
ESP32. É um conceito diferente da sensibilidade do trigger (threshold/
vel.máxima/curva/etc, já controlada pelo ESP32 -- ver PAD em
drum_backend.py e o protocolo PAD do firmware): aqui é sobre como a
nota que JÁ CHEGOU é tocada, não sobre como o piezo é lido. Fica só
aqui no Pi de propósito, assim continua valendo não importa qual kit
do DrumGizmo estiver ativo no momento, sobrevive a religar o
Raspberry, e não precisa reflashar o firmware do ESP32 pra ajustar.

Chave: nota MIDI da peça (a mesma que o ESP32 já manda pra ela hoje,
ou a nota resultante de uma "definição manual"/MIDI-learn).

  volume: 0.0 (mudo de verdade -- a nota nem chega a ser tocada, ver
          drum_backend.py) .. 1.0 (100%, padrão) .. 3.0 (300%, reforça
          bastante uma peça fraca) -- aplicado numa escala da VELOCITY
          do MIDI antes de mandar pro DrumGizmo (ver
          _aplicar_volume_pad em drum_backend.py). Efeito instantâneo,
          não precisa recarregar o kit.

          Faixa ampliada de 0-150% pra 0-300% (2026-09-18) porque
          150% ainda ficava fraco demais numa peça bem baixa -- como a
          velocity final tem teto em 127, esse limite só ajuda
          pancadas que chegam com velocity baixa (uma pancada que já
          chega quase no talo continua sem espaço pra "aumentar mais",
          isso é físico, não dá pra contornar só escalando).

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


def get_all():
    """{nota(str): {volume}, ...} -- só o que já foi ajustado alguma vez
    (peças nunca tocadas na aba de mixagem não aparecem aqui, mas vale
    o padrão {volume:1.0} mesmo assim). Entradas antigas que ainda têm
    uma chave "pan" (de antes do recurso ser removido) não atrapalham
    nada -- só ficam ali sem uso, ignoradas por get()/get_volume_factor()."""
    return _load()


def get(note):
    data = _load()
    entry = data.get(str(note), {})
    return {**_DEFAULT, **entry}


def get_volume_factor(note):
    return get(note)["volume"]


def set_volume(note, value):
    value = max(0.0, min(3.0, float(value)))
    data = _load()
    key = str(note)
    entry = {**_DEFAULT, **data.get(key, {})}
    entry["volume"] = value
    data[key] = entry
    _save(data)
    return entry

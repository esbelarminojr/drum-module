#!/usr/bin/env python3
"""
build_hihat_pedal_kit.py
=========================

Cria uma nova variante do kit Crocell reduzido (o que já usamos, com 12
instrumentos) adicionando SOMENTE o instrumento HihatPedal (nota 44 --
"pedal chick"), copiando o bloco de canais (<channelmap>) diretamente do
kit ORIGINAL/completo (CrocellKit_full.xml / Midimap_full.xml), em vez de
digitar esses dados à mão -- assim não corre risco de erro de transcrição.

Não mexe em nenhum arquivo original. Gera 2 arquivos novos:
  - <orquestra_tiny>2.xml   (ex: CrocellKit_tiny2.xml)
  - <midimap_tiny>2.xml     (ex: Midimap_tiny2.xml)

Depois disso, roda o slim_crocell.py normalmente apontando pra essa nova
orquestra, exatamente como já foi feito antes -- ele vai reduzir canais e
camadas de velocidade do HihatPedal (e reaproveitar os outros 12 já
existentes, sem re-processar nada de diferente neles).

USO:
  python3 build_hihat_pedal_kit.py <pasta_do_kit_full> \\
      [--tiny-orchestra CrocellKit_tiny.xml] \\
      [--full-orchestra CrocellKit_full.xml] \\
      [--tiny-midimap Midimap_tiny.xml] \\
      [--full-midimap Midimap_full.xml] \\
      [--instrumento HihatPedal] \\
      [--nota 44]

Exemplo (valores padrão já são esses, então basta):
  python3 build_hihat_pedal_kit.py ~/DrumGizmo/kits/kits/crocellkit/CrocellKit
"""

import argparse
import os
import sys
import xml.etree.ElementTree as ET


def find_instrument(tree, name):
    for el in tree.getroot().iter("instrument"):
        if el.get("name") == name:
            return el
    return None


def find_instrument_parent(tree):
    root = tree.getroot()
    for parent in root.iter():
        for child in list(parent):
            if child.tag == "instrument":
                return parent
    return None


def detect_map_tag(tree):
    """Descobre o nome da tag usada pra cada mapeamento nota->instrumento
    dentro do midimap (ex: 'map'), olhando os filhos do elemento raiz que
    tenham algum atributo numérico parecido com nota MIDI."""
    root = tree.getroot()
    counts = {}
    for el in root.iter():
        if el is root:
            continue
        counts[el.tag] = counts.get(el.tag, 0) + 1
    if not counts:
        return None
    # a tag mais repetida é quase certamente a de mapeamento nota->instrumento
    return max(counts.items(), key=lambda kv: kv[1])[0]


def find_note_element(tree, tag, note):
    note_str = str(note)
    for el in tree.getroot().iter(tag):
        for _, v in el.attrib.items():
            if v == note_str:
                return el
    return None


def indent(tree):
    try:
        ET.indent(tree, space="  ")
    except AttributeError:
        pass  # Python < 3.9 -- sai sem indentação bonita, mas funciona igual


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kit_dir", help="pasta com os arquivos do kit (CrocellKit_full.xml, CrocellKit_tiny.xml, etc.)")
    ap.add_argument("--tiny-orchestra", default="CrocellKit_tiny.xml")
    ap.add_argument("--full-orchestra", default="CrocellKit_full.xml")
    ap.add_argument("--tiny-midimap", default="Midimap_tiny.xml")
    ap.add_argument("--full-midimap", default="Midimap_full.xml")
    ap.add_argument("--instrumento", default="HihatPedal")
    ap.add_argument("--nota", type=int, default=44)
    args = ap.parse_args()

    kit_dir = os.path.expanduser(args.kit_dir)

    def p(name):
        return os.path.join(kit_dir, name)

    tiny_orch_path = p(args.tiny_orchestra)
    full_orch_path = p(args.full_orchestra)
    tiny_map_path = p(args.tiny_midimap)
    full_map_path = p(args.full_midimap)

    for path in (tiny_orch_path, full_orch_path, tiny_map_path, full_map_path):
        if not os.path.isfile(path):
            sys.exit(f"ERRO: arquivo não encontrado: {path}")

    # ---------------- ORQUESTRA ----------------

    full_orch_tree = ET.parse(full_orch_path)
    tiny_orch_tree = ET.parse(tiny_orch_path)

    instr_el = find_instrument(full_orch_tree, args.instrumento)
    if instr_el is None:
        sys.exit(f"ERRO: instrumento '{args.instrumento}' não encontrado em {full_orch_path}")

    if find_instrument(tiny_orch_tree, args.instrumento) is not None:
        print(f"Aviso: '{args.instrumento}' já existe em {tiny_orch_path} -- não vou duplicar.")
    else:
        parent = find_instrument_parent(tiny_orch_tree)
        if parent is None:
            sys.exit(f"ERRO: não achei nenhum <instrument> em {tiny_orch_path} pra saber onde inserir.")
        import copy
        parent.append(copy.deepcopy(instr_el))

    base, ext = os.path.splitext(args.tiny_orchestra)
    out_orch_name = f"{base}2{ext}"
    out_orch_path = p(out_orch_name)
    indent(tiny_orch_tree)
    tiny_orch_tree.write(out_orch_path, encoding="utf-8", xml_declaration=True)

    # ---------------- MIDIMAP ----------------

    full_map_tree = ET.parse(full_map_path)
    tiny_map_tree = ET.parse(tiny_map_path)

    tag = detect_map_tag(tiny_map_tree)
    if tag is None:
        sys.exit(f"ERRO: não consegui detectar a tag de mapeamento em {tiny_map_path}")

    already = find_note_element(tiny_map_tree, tag, args.nota)
    if already is not None:
        print(f"Aviso: já existe um mapeamento pra nota {args.nota} em {tiny_map_path} -- não vou duplicar.")
    else:
        note_el = find_note_element(full_map_tree, tag, args.nota)
        if note_el is None:
            sys.exit(f"ERRO: não achei elemento <{tag}> com nota {args.nota} em {full_map_path}")
        root = tiny_map_tree.getroot()
        import copy
        root.append(copy.deepcopy(note_el))

    base, ext = os.path.splitext(args.tiny_midimap)
    out_map_name = f"{base}2{ext}"
    out_map_path = p(out_map_name)
    indent(tiny_map_tree)
    tiny_map_tree.write(out_map_path, encoding="utf-8", xml_declaration=True)

    # ---------------- RESUMO ----------------

    print()
    print(f"Orquestra nova: {out_orch_path}")
    nomes = [el.get("name") for el in ET.parse(out_orch_path).getroot().iter("instrument")]
    print(f"  {len(nomes)} instrumentos: {', '.join(nomes)}")

    print()
    print(f"Midimap novo: {out_map_path}")
    for el in ET.parse(out_map_path).getroot().iter(tag):
        print(f"  <{tag} {' '.join(f'{k}={v!r}' for k, v in el.attrib.items())}/>")

    print()
    print("Confira a lista acima -- se tiver 13 instrumentos (os 12 de sempre +")
    print(f"{args.instrumento}) e a nota {args.nota} aparecer no midimap, pode rodar o slim_crocell.py:")
    print()
    print(f"  python3 slim_crocell.py --keep-fraction 0.5 \\")
    print(f"      {kit_dir} \\")
    print(f"      {out_orch_name} \\")
    print(f"      {os.path.join(os.path.dirname(kit_dir), 'CrocellKit_pi3')}")


if __name__ == "__main__":
    main()

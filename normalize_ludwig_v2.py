#!/usr/bin/env python3
"""
normalize_ludwig_v2.py
================================================================
Normaliza o volume dos .wav de um kit DrumGizmo POR INSTRUMENTO
(nao um ganho global unico) -- necessario quando instrumentos
diferentes foram gravados/exportados com niveis bem diferentes
entre si (ex: um Tom quase no talo a -3.6dB, um Hi-Hat a -28dB).

Como funciona:
  1. Le todos os *.xml de instrumento numa pasta (ex: a pasta "Kit/"
     do Ludwig Black Cortex) e descobre, pra cada instrumento, quais
     arquivos .wav (dentro de "samples_dir") pertencem a ele --
     olhando os <audiofile file="..."/> de dentro de cada <sample>.
  2. Pra cada instrumento, mede o pico (max_volume, via ffmpeg
     volumedetect) de TODOS os arquivos dele.
  3. Calcula UM ganho por instrumento = target_db - pico_do_instrumento.
     Isso preserva a dinamica ENTRE as camadas de forca de um mesmo
     instrumento (uma batida fraca continua mais fraca que uma forte),
     mas iguala o volume ENTRE instrumentos diferentes.
  4. Aplica esse ganho em todos os arquivos do instrumento.

Faz backup da pasta de samples original antes de mexer.

Uso:
    python3 normalize_ludwig_v2.py <pasta_instrument_xmls> <pasta_samples> [--target-db -3.0] [--dry-run]

Exemplo (Ludwig Black Cortex):
    python3 normalize_ludwig_v2.py \
        ~/DrumGizmo/kits/kits/ludwig-black-cortex/Kit \
        ~/DrumGizmo/kits/kits/ludwig-black-cortex/Kit/Samples
"""
import os
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

VOL_RE = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")


def get_max_volume_db(path):
    cmd = ["ffmpeg", "-i", path, "-af", "volumedetect", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    m = VOL_RE.search(proc.stderr)
    if not m:
        print(f"aviso: nao consegui ler max_volume de {path!r} -- pulando")
        return None
    return float(m.group(1))


def apply_gain(src_path, dst_path, gain_db):
    cmd = ["ffmpeg", "-y", "-i", src_path, "-af", f"volume={gain_db}dB", dst_path]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"ERRO ao processar {src_path!r}:")
        print(proc.stderr[-2000:])
        return False
    return True


def collect_instrument_files(xml_dir, samples_dir):
    """Devolve {nome_instrumento: [caminho_absoluto_wav, ...]} lendo todos
    os *.xml de instrumento em xml_dir (ignora Drumkit.xml/Midimap.xml,
    que tem estrutura diferente -- so processa arquivos que tem <samples>
    no topo, formato de instrumento)."""
    result = {}
    for fname in sorted(os.listdir(xml_dir)):
        if not fname.lower().endswith(".xml"):
            continue
        path = os.path.join(xml_dir, fname)
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError:
            continue
        if root.tag != "instrument":
            continue  # Drumkit.xml tem tag <drumkit>, midimap tem <midimap>
        instr_name = root.get("name") or os.path.splitext(fname)[0]
        wavs = set()
        for audiofile in root.iter("audiofile"):
            file_attr = audiofile.get("file")
            if not file_attr:
                continue
            base = os.path.basename(file_attr)  # tira "Samples/" ou qualquer prefixo
            full = os.path.join(samples_dir, base)
            wavs.add(full)
        if wavs:
            result[instr_name] = sorted(wavs)
    return result


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    if dry_run:
        args.remove("--dry-run")

    target_db = -3.0
    if "--target-db" in args:
        idx = args.index("--target-db")
        target_db = float(args[idx + 1])
        del args[idx:idx + 2]

    if len(args) != 2:
        print(__doc__)
        sys.exit(1)

    xml_dir = os.path.abspath(os.path.expanduser(args[0]))
    samples_dir = os.path.abspath(os.path.expanduser(args[1]))

    if not os.path.isdir(xml_dir):
        print(f"ERRO: pasta de xml de instrumentos nao existe: {xml_dir!r}")
        sys.exit(1)
    if not os.path.isdir(samples_dir):
        print(f"ERRO: pasta de samples nao existe: {samples_dir!r}")
        sys.exit(1)

    instrumentos = collect_instrument_files(xml_dir, samples_dir)
    if not instrumentos:
        print(f"ERRO: nenhum instrumento encontrado em {xml_dir!r}")
        sys.exit(1)

    total_arquivos = sum(len(v) for v in instrumentos.values())
    print(f"Encontrados {len(instrumentos)} instrumentos, {total_arquivos} arquivos .wav ao todo.")
    print("Passo 1/3: medindo o pico de cada instrumento (pode demorar alguns minutos)...\n")

    ganhos = {}  # nome -> (gain_db, pico_medido, arquivos_faltando)
    contador = 0
    for nome, wavs in instrumentos.items():
        picos = []
        faltando = []
        for w in wavs:
            if not os.path.isfile(w):
                faltando.append(w)
                continue
            p = get_max_volume_db(w)
            if p is not None:
                picos.append(p)
            contador += 1
        if not picos:
            print(f"  aviso: {nome!r} sem nenhum arquivo medido -- pulando")
            continue
        pico_instr = max(picos)
        gain_db = target_db - pico_instr
        ganhos[nome] = (gain_db, pico_instr, faltando)
        aviso_falta = f" ({len(faltando)} arquivo(s) nao encontrado(s))" if faltando else ""
        print(f"  {nome}: pico={pico_instr:+.1f}dB -> ganho={gain_db:+.1f}dB ({len(wavs)} arquivos){aviso_falta}")

    print(f"\n{contador} de {total_arquivos} arquivos analisados no total.")

    if dry_run:
        print("\n--dry-run: nao vou alterar nada. Roda de novo sem --dry-run pra aplicar.")
        return

    backup_dir = samples_dir.rstrip("/") + "_original_backup"
    if os.path.exists(backup_dir):
        print(f"\nAVISO: {backup_dir!r} ja existe -- nao vou sobrescrever, assumindo que o backup ja foi feito.")
    else:
        print(f"\nPasso 2/3: fazendo backup de {samples_dir!r} -> {backup_dir!r} ...")
        shutil.copytree(samples_dir, backup_dir)
        print("Backup concluido.")

    print(f"\nPasso 3/3: aplicando ganho por instrumento...")
    ok_total = 0
    for nome, wavs in instrumentos.items():
        if nome not in ganhos:
            continue
        gain_db, _, _ = ganhos[nome]
        ok = 0
        for w in wavs:
            if not os.path.isfile(w):
                continue
            tmp = w + ".normalizing.wav"
            if apply_gain(w, tmp, gain_db):
                os.replace(tmp, w)
                ok += 1
            elif os.path.exists(tmp):
                os.remove(tmp)
        ok_total += ok
        print(f"  {nome}: {ok}/{len(wavs)} arquivos processados (ganho {gain_db:+.1f}dB)")

    print(f"\nPronto: {ok_total} de {total_arquivos} arquivos normalizados com sucesso.")
    print(f"Backup do original (caso precise desfazer): {backup_dir}")


if __name__ == "__main__":
    main()

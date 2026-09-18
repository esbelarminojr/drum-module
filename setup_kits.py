#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
setup_kits.py
================================================================
Monta os 3 kits (Padrão, Ludwig, Crocell) do zero, baixando as
amostras originais da internet e aplicando por cima TODAS as
correções descobertas na "caçada de bugs" original -- pra nunca mais
precisar repetir esse trabalho manual num Raspberry novo (ou se este
aqui precisar ser reinstalado).

Cada kit vira uma pasta pronta dentro de ~/DrumGizmo/kits/kits/, e no
final o kits.json (do lado deste script, ou em --kits-json) é
atualizado sozinho apontando pra elas.

NÃO rode isto com sudo -- os arquivos ficam com o dono certo (você),
que é quem o drum-backend.service vai rodar como. Precisa de internet
(só nesta etapa; depois de pronto, o Raspberry funciona sem rede).

Uso:
    python3 setup_kits.py                  # monta os 3 (pula os que já existem)
    python3 setup_kits.py --force          # remonta os 3 do zero
    python3 setup_kits.py --only padrao    # só um kit específico
    python3 setup_kits.py --only ludwig,crocell
    python3 setup_kits.py --kits-dir ~/DrumGizmo/kits/kits --kits-json ~/drum-module/kits.json

Pré-requisitos de sistema (o script confere e avisa se faltar):
    sudo apt install -y wget git unzip ffmpeg
================================================================
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
import xml.etree.ElementTree as ET

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_KITS_DIR = "~/DrumGizmo/kits/kits"
DEFAULT_KITS_JSON = os.path.join(SCRIPT_DIR, "kits.json")

SLIM_CROCELL = os.path.join(SCRIPT_DIR, "slim_crocell.py")
BUILD_HIHAT_PEDAL = os.path.join(SCRIPT_DIR, "build_hihat_pedal_kit.py")
NORMALIZE_LUDWIG = os.path.join(SCRIPT_DIR, "normalize_ludwig.py")

TEST_KIT_URL = "https://drumgizmo.org/kits/test-kit.tar.gz"
LUDWIG_REPO_URL = "https://github.com/samuelsantanaoficial/drumgizmo-tchakpoum-ludwig-black-cortex.git"
CROCELL_ZIP_URL = "https://drumgizmo.org/kits/CrocellKit/CrocellKit1_1.zip"

# midimap "definitivo" do kit Padrão, escrito à mão -- não é o midimap.xml
# original do test-kit (que usava notas diferentes das que o firmware do
# ESP32 realmente envia). tom4/crash2 ficam sem uso porque o firmware só
# tem 3 tons e 1 crash; as 3 notas de hihat (fechado/aberto/pedal) caem
# todas no único instrumento "hihat" do kit, que não distingue isso.
PADRAO_MIDIMAP_PI = """<?xml version="1.0" encoding="UTF-8"?>
<midimap>
    <map note="36" instr="kick"/>
    <map note="38" instr="snare"/>
    <map note="42" instr="hihat"/>
    <map note="43" instr="tom3"/>
    <map note="44" instr="hihat"/>
    <map note="45" instr="tom2"/>
    <map note="46" instr="hihat"/>
    <map note="48" instr="tom1"/>
    <map note="49" instr="crash1"/>
    <map note="51" instr="ride"/>
</midimap>
"""

# Descoberto durante o debug original: o Ride do Crocell tem um microfone
# próprio ("Ride") separado dos overheads, mas como RideR/RideRBell têm
# MAIS de 2 canais "main" (overheads inclusos), a detecção automática do
# slim_crocell.py cairia nos overheads por padrão -- por isso este override
# manual é necessário sempre que este kit for reconstruído.
CROCELL_CHANNEL_MAP = {
    "RideR": ["Ride"],
    "RideRBell": ["Ride"],
}


def log(msg):
    print(f"\n=== {msg} ===")


def run(cmd, cwd=None):
    print("$ " + " ".join(str(c) for c in cmd))
    subprocess.run(cmd, cwd=cwd, check=True)


def check_tools():
    faltando = [t for t in ("wget", "git", "unzip", "ffmpeg", "tar") if shutil.which(t) is None]
    if faltando:
        print("ERRO: faltam ferramentas de sistema: " + ", ".join(faltando))
        print("Instale com: sudo apt install -y " + " ".join(faltando))
        sys.exit(1)


def flatten_single_subdir(target_dir, marker_relpath):
    """Se 'marker_relpath' não existir direto dentro de target_dir, mas
    target_dir tiver exatamente UMA subpasta, sobe o conteúdo dela pra
    target_dir (alguns .zip/.tar.gz vêm com uma pasta-wrapper por dentro,
    outros não -- isso faz o script funcionar nos dois casos sem precisar
    adivinhar de antemão qual formato a fonte original vai usar)."""
    if os.path.exists(os.path.join(target_dir, marker_relpath)):
        return
    subdirs = [
        e for e in os.listdir(target_dir)
        if os.path.isdir(os.path.join(target_dir, e))
    ]
    if len(subdirs) != 1:
        return
    wrapper = os.path.join(target_dir, subdirs[0])
    for entry in os.listdir(wrapper):
        shutil.move(os.path.join(wrapper, entry), os.path.join(target_dir, entry))
    os.rmdir(wrapper)


def convert_dir_to_16bit(src_dir, dst_dir):
    """Copia src_dir inteiro pra dst_dir e converte todo .wav encontrado
    (em qualquer subpasta) pra PCM 16 bits via ffmpeg, sobrescrevendo no
    lugar -- os .xml e qualquer outro arquivo são só copiados, intactos."""
    if os.path.exists(dst_dir):
        shutil.rmtree(dst_dir)
    shutil.copytree(src_dir, dst_dir)
    n = 0
    for root, _dirs, files in os.walk(dst_dir):
        for fname in files:
            if not fname.lower().endswith(".wav"):
                continue
            path = os.path.join(root, fname)
            tmp = path + ".16bit_tmp.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-acodec", "pcm_s16le", tmp],
                check=True,
            )
            os.replace(tmp, path)
            n += 1
    print(f"{n} arquivo(s) .wav convertido(s) pra 16 bits em {dst_dir}")


def setup_padrao(kits_dir, force):
    base = os.path.join(kits_dir, "test-kit-oficial")
    final_dir = os.path.join(base, "kits", "test_16bit_pi")
    final_xml = os.path.join(final_dir, "test_pi.xml")
    if os.path.isfile(final_xml) and not force:
        print(f"Padrão já está pronto em {final_dir} (use --force pra refazer). Pulando.")
        return {"cwd": final_dir, "kit_xml": "test_pi.xml", "midimap": "midimap_pi.xml"}

    if os.path.isdir(base) and force:
        shutil.rmtree(base)
    os.makedirs(base, exist_ok=True)

    log("Padrão: baixando test-kit.tar.gz (kit oficial de teste do DrumGizmo)")
    tarball = os.path.join(base, "test-kit.tar.gz")
    run(["wget", "-O", tarball, TEST_KIT_URL])
    with tarfile.open(tarball) as tf:
        tf.extractall(base)
    os.remove(tarball)
    flatten_single_subdir(base, os.path.join("kits", "test"))

    test_dir = os.path.join(base, "kits", "test")
    orchestra = os.path.join(test_dir, "test.xml")
    if not os.path.isfile(orchestra):
        sys.exit(f"ERRO: esperava encontrar {orchestra!r} depois de extrair test-kit.tar.gz -- "
                  "o pacote oficial deve ter mudado de formato, confira manualmente.")

    log("Padrão: convertendo amostras pra PCM 16 bits")
    test16_dir = os.path.join(base, "kits", "test_16bit")
    convert_dir_to_16bit(test_dir, test16_dir)

    log("Padrão: reduzindo canais/camadas com slim_crocell.py")
    run([sys.executable, SLIM_CROCELL, test16_dir, "test.xml", final_dir])

    # slim_crocell.py linka automaticamente o midimap.xml ORIGINAL do
    # test-kit (que usa notas diferentes das que o firmware manda) --
    # substitui pelo midimap definitivo, escrito à mão pra este projeto.
    auto_midimap = os.path.join(final_dir, "midimap.xml")
    if os.path.islink(auto_midimap) or os.path.isfile(auto_midimap):
        os.remove(auto_midimap)
    with open(os.path.join(final_dir, "midimap_pi.xml"), "w", encoding="utf-8") as f:
        f.write(PADRAO_MIDIMAP_PI)

    print(f"Padrão pronto em {final_dir}")
    return {"cwd": final_dir, "kit_xml": "test_pi.xml", "midimap": "midimap_pi.xml"}


def setup_ludwig(kits_dir, force):
    base = os.path.join(kits_dir, "ludwig-black-cortex")
    final_dir = os.path.join(base, "Ludwig_pi")
    final_xml = os.path.join(final_dir, "Drumkit_pi.xml")
    if os.path.isfile(final_xml) and not force:
        print(f"Ludwig já está pronto em {final_dir} (use --force pra refazer). Pulando.")
        return {"cwd": final_dir, "kit_xml": "Drumkit_pi.xml", "midimap": "Midimap.xml"}

    if os.path.isdir(base) and force:
        shutil.rmtree(base)
    if not os.path.isdir(base):
        log("Ludwig: clonando repositório (Ludwig Black Cortex)")
        run(["git", "clone", "--depth", "1", LUDWIG_REPO_URL, base])
        flatten_single_subdir(base, "Drumkit.xml")

    orchestra = os.path.join(base, "Drumkit.xml")
    kit_dir = os.path.join(base, "Kit")
    samples_dir = os.path.join(kit_dir, "Samples")
    if not os.path.isfile(orchestra) or not os.path.isdir(samples_dir):
        sys.exit(f"ERRO: esperava {orchestra!r} e {samples_dir!r} depois de clonar o repositório do "
                  "Ludwig -- confira se a estrutura do repo mudou.")

    backup_dir = samples_dir.rstrip("/") + "_original_backup"
    if os.path.isdir(backup_dir) and not force:
        print("Ludwig: amostras já normalizadas antes (backup existe) -- pulando normalização.")
    else:
        if force and os.path.isdir(backup_dir):
            # restaura o original antes de normalizar de novo, senão o script
            # de normalização mediria o pico de um áudio já normalizado
            shutil.rmtree(samples_dir)
            shutil.copytree(backup_dir, samples_dir)
        log("Ludwig: normalizando volume por instrumento (ffmpeg volumedetect)")
        run([sys.executable, NORMALIZE_LUDWIG, kit_dir, samples_dir])

    log("Ludwig: reduzindo canais/camadas com slim_crocell.py")
    run([sys.executable, SLIM_CROCELL, base, "Drumkit.xml", final_dir])

    print(f"Ludwig pronto em {final_dir}")
    return {"cwd": final_dir, "kit_xml": "Drumkit_pi.xml", "midimap": "Midimap.xml"}


def setup_crocell(kits_dir, force):
    crocell_root = os.path.join(kits_dir, "crocellkit")
    src_dir = os.path.join(crocell_root, "CrocellKit")
    final_dir = os.path.join(crocell_root, "CrocellKit_pi3")
    # nome final depois de encadear DUAS adições ao "tiny" original (ver
    # comentário mais abaixo): tiny -> tiny2 (+HihatPedal) -> tiny22 (+HihatSemiOpen)
    final_xml = os.path.join(final_dir, "CrocellKit_tiny22_pi.xml")
    if os.path.isfile(final_xml) and not force:
        print(f"Crocell já está pronto em {final_dir} (use --force pra refazer). Pulando.")
        return {"cwd": final_dir, "kit_xml": "CrocellKit_tiny22_pi.xml", "midimap": "Midimap_tiny22.xml"}

    if force:
        for d in (src_dir, final_dir):
            if os.path.isdir(d):
                shutil.rmtree(d)
    os.makedirs(crocell_root, exist_ok=True)

    if not os.path.isdir(src_dir):
        log("Crocell: baixando CrocellKit1_1.zip")
        zip_path = os.path.join(crocell_root, "CrocellKit1_1.zip")
        run(["wget", "-O", zip_path, CROCELL_ZIP_URL])
        os.makedirs(src_dir, exist_ok=True)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(src_dir)
        os.remove(zip_path)
        flatten_single_subdir(src_dir, "CrocellKit_full.xml")

    full_orchestra = os.path.join(src_dir, "CrocellKit_full.xml")
    if not os.path.isfile(full_orchestra):
        sys.exit(f"ERRO: esperava {full_orchestra!r} depois de extrair o CrocellKit1_1.zip -- "
                  "confira se a estrutura do pacote oficial mudou.")

    # Encadeia duas passadas de build_hihat_pedal_kit.py (script genérico,
    # recebe --instrumento/--nota como parâmetro -- não é hardcoded só pro
    # HihatPedal): a 1a adiciona HihatPedal (nota 44, "pedal chick") em
    # cima do "tiny" original, gerando "..._tiny2.xml"; a 2a adiciona
    # HihatSemiOpen (nota 80, chimbal meio-aberto -- pedido feito depois
    # de conferir os nomes reais no CrocellKit_full.xml/Midimap_full.xml
    # deste kit) em cima do resultado da 1a, gerando "..._tiny22.xml". Cada
    # passada só copia o instrumento inteiro (com os canais certos) do kit
    # FULL pro tiny -- não inventa nada.
    tiny2_orchestra = os.path.join(src_dir, "CrocellKit_tiny2.xml")
    tiny2_midimap = os.path.join(src_dir, "Midimap_tiny2.xml")
    if not os.path.isfile(tiny2_orchestra) or not os.path.isfile(tiny2_midimap) or force:
        log("Crocell: adicionando instrumento HihatPedal (build_hihat_pedal_kit.py)")
        run([sys.executable, BUILD_HIHAT_PEDAL, src_dir])

    tiny22_orchestra = os.path.join(src_dir, "CrocellKit_tiny22.xml")
    tiny22_midimap = os.path.join(src_dir, "Midimap_tiny22.xml")
    if not os.path.isfile(tiny22_orchestra) or not os.path.isfile(tiny22_midimap) or force:
        log("Crocell: adicionando instrumento HihatSemiOpen (build_hihat_pedal_kit.py)")
        run([
            sys.executable, BUILD_HIHAT_PEDAL, src_dir,
            "--tiny-orchestra", "CrocellKit_tiny2.xml",
            "--tiny-midimap", "Midimap_tiny2.xml",
            "--instrumento", "HihatSemiOpen",
            "--nota", "80",
        ])

    log("Crocell: reduzindo canais/camadas com slim_crocell.py (--keep-fraction 0.5)")
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as tmp:
        json.dump(CROCELL_CHANNEL_MAP, tmp)
        channel_map_path = tmp.name
    try:
        run([
            sys.executable, SLIM_CROCELL,
            "--keep-fraction", "0.5",
            "--channel-map", channel_map_path,
            src_dir, "CrocellKit_tiny22.xml", final_dir,
        ])
    finally:
        os.remove(channel_map_path)

    print(f"Crocell pronto em {final_dir}")
    return {"cwd": final_dir, "kit_xml": "CrocellKit_tiny22_pi.xml", "midimap": "Midimap_tiny22.xml"}


def update_kits_json(path, results):
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {
            "_comentario": "Configuracao dos kits de bateria do DrumGizmo. 'active' e o kit "
                            "atualmente selecionado. 'alsa_device' controla qual placa de som "
                            "usar: deixe 'auto' para deteccao automatica.",
            "active": "Padrão",
            "midi_port_name": "ESP32 Drum",
            "alsa_device": "auto",
            "kits": {},
        }
    data.setdefault("kits", {})
    for name, info in results.items():
        if info is None:
            continue
        data["kits"][name] = info
    if data.get("active") not in data["kits"]:
        data["active"] = next(iter(data["kits"]), data.get("active"))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n{path} atualizado.")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kits-dir", default=DEFAULT_KITS_DIR)
    ap.add_argument("--kits-json", default=DEFAULT_KITS_JSON)
    ap.add_argument("--force", action="store_true", help="reconstroi tudo do zero, mesmo se já existir")
    ap.add_argument("--only", default="", help="lista separada por vírgula: padrao,ludwig,crocell")
    args = ap.parse_args()

    if os.geteuid() == 0:
        sys.exit("ERRO: não rode este script com sudo/root -- rode como o usuário normal "
                  "(o mesmo que vai rodar o drum-backend.service).")

    check_tools()

    kits_dir = os.path.abspath(os.path.expanduser(args.kits_dir))
    os.makedirs(kits_dir, exist_ok=True)

    only = {s.strip().lower() for s in args.only.split(",") if s.strip()}
    wanted = only or {"padrao", "ludwig", "crocell"}

    setters = {
        "padrao": ("Padrão", setup_padrao),
        "ludwig": ("Ludwig", setup_ludwig),
        "crocell": ("Crocell", setup_crocell),
    }

    results = {}
    falhas = []
    for key, (nome, fn) in setters.items():
        if key not in wanted:
            continue
        try:
            results[nome] = fn(kits_dir, args.force)
        except subprocess.CalledProcessError as e:
            print(f"\nERRO: comando falhou montando o kit {nome!r}: {e}")
            falhas.append(nome)
        except SystemExit:
            raise
        except Exception as e:
            print(f"\nERRO inesperado montando o kit {nome!r}: {e}")
            falhas.append(nome)

    if results:
        update_kits_json(os.path.abspath(os.path.expanduser(args.kits_json)), results)

    print()
    if falhas:
        print(f"Kits com problema (confira as mensagens acima): {', '.join(falhas)}")
    ok = [n for n in results if n not in falhas]
    if ok:
        print(f"Kits prontos: {', '.join(ok)}")
    if falhas:
        sys.exit(1)


if __name__ == "__main__":
    main()

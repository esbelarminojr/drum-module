#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
slim_crocell.py
================================================================
Gera uma versao "enxuta" (so 2 canais de saida) de um kit DrumGizmo
multi-microfone, sem tocar em nenhum arquivo de audio original.

Por que isso funciona: em kits como o CrocellKit, cada camada de forca
de cada instrumento e gravada com varios microfones ao mesmo tempo (ex:
15 canais), mas todos os <audiofile> de uma mesma camada apontam pro
MESMO arquivo .wav multi-canal (so muda o "filechannel"). O DrumGizmo
conta cada <audiofile> como um "carregamento" separado no "X of Y" --
entao um kit com 15 canais carrega ~15x mais devagar (e ocupa ~15x mais
RAM) do que precisaria pra uma saida estereo simples (2 canais).

Este script le o XML "orquestra" original (ex: CrocellKit_tiny.xml) e,
pra cada instrumento, mantem so os 2 canais que ja estao marcados como
"main" (principal) no proprio kit -- os mesmos que ja seriam ouvidos
numa mixagem de referencia -- e descarta o resto. Gera uma pasta nova
(nao mexe na original) com:
  - um XML de orquestra novo, com só 2 canais ("Out1"/"Out2")
  - um XML por instrumento, só com os 2 <audiofile> que sobraram
  - link(s) simbolico(s) apontando pras pastas/arquivos de audio
    ORIGINAIS -- os .wav nunca sao copiados nem tocados.

IMPORTANTE (histórico): a primeira versão deste script assumia que todo
kit guarda os .wav numa pasta chamada literalmente "samples" (minúsculo)
do lado de cada XML de instrumento -- isso é como o CrocellKit funciona,
mas NÃO é universal. O kit "Ludwig Black Cortex", por exemplo, guarda
todos os .wav numa única pasta compartilhada "Kit/Samples/" (maiúsculo,
uma pasta só pra todos os instrumentos) e cada XML referencia o arquivo
como "Samples/nome.wav". Com a suposição hardcoded de "samples" minúsculo,
o script gerava um kit "enxuto" cujo XML apontava pra um caminho que não
existia em lugar nenhum -- e o resultado era silêncio total, mesmo sem
nenhum erro aparente (o DrumGizmo carrega o XML normalmente, só não acha
o .wav declarado dentro dele).

A partir desta versão, o script NÃO assume mais nenhum nome de pasta.
Em vez disso, ele lê o atributo "file=" de cada <audiofile> que sobrou
no XML do instrumento (depois de já ter cortado os canais) e descobre
sozinho, pelo próprio caminho relativo escrito ali, se os .wav ficam:
  a) numa subpasta ao lado do XML (ex: "samples/x.wav", "Samples/x.wav,
     "audio/kick/x.wav" -- qualquer nome, em qualquer kit) -> cria um
     link simbólico pra essa subpasta especificamente (usa o nome real
     encontrado no XML, não um nome fixo);
  b) direto ao lado do XML, sem nenhuma subpasta (ex: "x.wav") -> cria
     um link simbólico por ARQUIVO individual (não dá pra linkar a pasta
     inteira do instrumento original, porque lá também mora o próprio
     XML do instrumento, que já foi reescrito à parte).
Isso faz o script se adaptar sozinho à particularidade de cada kit que
for usado nele no futuro, em vez de precisar de um ajuste manual (como
aconteceu com o Ludwig) toda vez que um kit novo guardar os samples de
um jeito diferente.

Regra de escolha de canal por instrumento:
  - 2 canais marcados main -> usa os dois (ex: SnareTop + SnareBottom)
  - 1 canal marcado main -> usa ele duas vezes (mono central, ainda com
    som nos dois lados em vez de só um)
  - mais de 2 canais main (tipico de pratos/chimbal, que often usam os
    overheads como principal) -> prefere um par que pareça estereo
    (overhead/left-right, por nome) se existir entre os "main"; senao
    pega os 2 primeiros
  - 0 canais main -> ESTE KIT NAO USA A CONVENCAO "main" (aconteceu com o
    "test-kit" oficial do DrumGizmo -- nenhum instrumento tem main="true"
    em canto nenhum). Em vez de simplesmente descartar o instrumento, o
    script tenta, nesta ordem:
      1) um canal cujo nome bate com o nome do proprio instrumento (ex:
         instrumento "kick" tem um <channelmap in="kick">) -- comum
         quando o kit nomeia o canal de microfone proprio igual à peça;
      2) um par de canais que pareça estereo pelo nome (ex: "oh-l"/"oh-r",
         "overhead-left"/"overhead-right", "*-l"/"*-r") -- tipico de
         pratos/overheads que nao tem microfone proprio;
      3) ultimo recurso: os 1-2 primeiros canais que o instrumento
         declarar, o que for -- sempre avisando no console que foi um
         "chute" (pra revisar de ouvido se ficou estranho).
  - override manual (--channel-map arquivo.json): se um instrumento
    especifico ficar errado com a deteccao automatica (aconteceu com o
    Ride do Crocell, que "main" apontava pro overhead em vez do microfone
    proprio), passe um JSON tipo {"Ride": ["Ride"]} ou {"NomeInstr":
    ["CanalEsquerdo","CanalDireito"]} -- tem prioridade sobre tudo o
    resto, main incluido.

CORTE 2 -- camadas de velocidade/força (--keep-fraction):
  Além de cortar canais, opcionalmente também reduz quantas "camadas de
  força" (samples de intensidades diferentes, ex: Snare-1 pianissimo até
  Snare-98 fortissimo) cada instrumento guarda. Isso reduz ainda mais o
  total de amostras carregadas (e a RAM usada), a troco de uma resposta
  de intensidade um pouco menos granular -- mas cada golpe ainda soa com
  o volume/timbre certo, só com menos "degraus" entre um extremo e outro.
  As camadas são ordenadas pelo atributo "power" e ficam sempre incluídos
  o mais fraco e o mais forte, distribuindo o resto igualmente no meio.
  Valor 1.0 (padrão) = não corta nenhuma camada, só os canais.

Também linka automaticamente (sem copiar) qualquer outro arquivo solto
que exista na pasta do kit original (ex: o Midimap_tiny.xml) para dentro
da pasta de saída -- assim o kit gerado fica completo e usável sozinho.

CONVERSAO DE ESQUEMA ANTIGO (<velocities>): kits mais antigos do DrumGizmo
(o "test-kit" oficial de 2011, por exemplo) descrevem a intensidade de
cada amostra num bloco <velocities> separado (<velocity lower upper>
<sampleref name="X"/></velocity>), em vez do atributo 'power=' direto em
cada <sample> que o Crocell/Ludwig/Padrão usam. A versao do DrumGizmo
rodando no Raspberry (0.9.20) rejeita esse formato antigo inteiro com
"Failed to load", sem nenhuma mensagem de erro especifica -- foi
descoberto isolando um unico instrumento ate sobrar so essa diferenca.
Agora o script detecta esse caso sozinho (por instrumento) e converte
pro formato novo automaticamente: cada amostra ganha 'power=' igual ao
limite superior da faixa de velocidade em que ela era referenciada (o
bloco <velocities> e removido depois de convertido, e o <instrument>
ganha version="2.0" se nao tiver).

Uso:
    python3 slim_crocell.py <pasta_do_kit_original> <arquivo_orquestra.xml> <pasta_de_saida> [--keep-fraction 0.5] [--channel-map overrides.json]

--channel-map overrides.json: arquivo tipo
    {"Ride": ["Ride"], "Crash1": ["oh-l", "oh-r"]}
força manualmente os canais desses instrumentos (por nome), tem
prioridade sobre 'main' e sobre a deteccao automatica.

Exemplo (rodar no Raspberry, dentro da pasta do kit) -- só cortando canais:
    python3 slim_crocell.py \
        ~/DrumGizmo/kits/kits/crocellkit/CrocellKit \
        CrocellKit_tiny.xml \
        ~/DrumGizmo/kits/kits/crocellkit/CrocellKit_pi

Exemplo -- cortando canais E reduzindo pela metade as camadas de força:
    python3 slim_crocell.py \
        ~/DrumGizmo/kits/kits/crocellkit/CrocellKit \
        CrocellKit_tiny.xml \
        ~/DrumGizmo/kits/kits/crocellkit/CrocellKit_pi2 \
        --keep-fraction 0.5

Depois disso, kits.json aponta o Crocell pra:
    "cwd": "~/DrumGizmo/kits/kits/crocellkit/CrocellKit_pi2"
    "kit_xml": "CrocellKit_tiny_pi.xml"
    "midimap": "Midimap_tiny.xml"   (já vem linkado automaticamente na pasta nova)
================================================================
"""

import copy
import json
import os
import sys
import wave
import xml.etree.ElementTree as ET

OUT_L = "Out1"
OUT_R = "Out2"
PREFERRED_PAIR = ["OHLeft", "OHRight"]

NEW_KIT_NAME_SUFFIX = " (Pi, 2 canais)"


def detect_wav_samplerate(path):
    """Le so o cabecalho do .wav (nao decodifica os frames) pra descobrir a
    taxa de amostragem real. Funciona mesmo em PCM de 24 bits (o modulo
    'wave' do Python consegue ler o cabecalho desses arquivos, só não sabe
    decodificar os frames -- e a gente só precisa do cabecalho aqui)."""
    try:
        with wave.open(path, "rb") as w:
            return w.getframerate()
    except Exception:
        return None


def parse_orchestra(path):
    tree = ET.parse(path)
    root = tree.getroot()
    kit_name = root.get("name", "Kit")
    samplerate_declared = root.get("samplerate")
    samplerate = samplerate_declared  # pode ficar None -- resolvido em main()
    channels_el = root.find("channels")
    total_channels = len(channels_el.findall("channel")) if channels_el is not None else None
    instruments = []
    instruments_el = root.find("instruments")
    if instruments_el is None:
        raise ValueError(f"não achei <instruments> em {path}")
    for instr in instruments_el.findall("instrument"):
        name = instr.get("name")
        file_rel = instr.get("file")
        group = instr.get("group")
        all_channels = [cm.get("in") for cm in instr.findall("channelmap")]
        main_channels = [
            cm.get("in") for cm in instr.findall("channelmap") if cm.get("main") == "true"
        ]
        instruments.append({
            "name": name,
            "file": file_rel,
            "group": group,
            "main_channels": main_channels,
            "all_channels": all_channels,
        })
    return kit_name, samplerate, instruments, total_channels


def thin_velocity_layers(samples_el, keep_fraction):
    """Remove <sample> (camadas de força) do <samples> em excesso, mantendo
    uma seleção uniformemente espaçada pelo valor de 'power' (sempre
    incluindo a mais fraca e a mais forte). keep_fraction=1.0 não mexe em
    nada. Retorna a lista de <sample> que restaram (na ordem original)."""
    samples = list(samples_el.findall("sample"))
    n = len(samples)
    if n <= 1 or keep_fraction >= 1.0:
        return samples

    def get_power(el):
        try:
            return float(el.get("power", "0"))
        except (TypeError, ValueError):
            return 0.0

    order = sorted(range(n), key=lambda i: get_power(samples[i]))
    keep_count = max(1, min(n, round(n * keep_fraction)))
    if keep_count >= n:
        keep_positions = set(range(n))
    elif keep_count == 1:
        keep_positions = {order[0]}
    else:
        keep_positions = set()
        for k in range(keep_count):
            pos = round(k * (n - 1) / (keep_count - 1))
            keep_positions.add(order[pos])

    kept = [samples[i] for i in range(n) if i in keep_positions]
    removed = [samples[i] for i in range(n) if i not in keep_positions]
    for s in removed:
        samples_el.remove(s)
    return kept


def _looks_like_stereo_pair(a, b):
    """Heurística de nome: 'a' e 'b' parecem um par estéreo esquerda/direita
    (ex: 'oh-l'/'oh-r', 'Overhead Left'/'Overhead Right', 'OHLeft'/'OHRight')?
    Não assume nenhuma convenção fixa de um kit só -- tenta várias formas
    comuns de sufixo/prefixo left-right, mais um caso genérico "os dois tem
    'oh'/'overhead' no nome"."""
    la, lb = a.lower(), b.lower()
    suffix_pairs = [
        ("-l", "-r"), ("_l", "_r"), (" l", " r"),
        ("left", "right"), ("l", "r"),
    ]
    for sa, sb in suffix_pairs:
        if la.endswith(sa) and lb.endswith(sb):
            base_a = la[: -len(sa)] if sa else la
            base_b = lb[: -len(sb)] if sb else lb
            if base_a == base_b:
                return True
    if ("oh" in la or "overhead" in la) and ("oh" in lb or "overhead" in lb):
        return True
    return False


def _find_stereo_pair(channels):
    for i, a in enumerate(channels):
        for b in channels[i + 1:]:
            if _looks_like_stereo_pair(a, b):
                return a, b
    return None, None


def pick_channels(instr_name, main_channels, all_channels):
    """Devolve (canal_esquerdo, canal_direito, como_escolheu) a manter, ou
    (None, None, motivo) se não sobrar nenhum canal utilizável."""
    if len(main_channels) == 1:
        return main_channels[0], main_channels[0], "main (1 canal, duplicado)"
    if len(main_channels) == 2:
        return main_channels[0], main_channels[1], "main (2 canais)"
    if len(main_channels) > 2:
        # mais de 2 marcados main -- prefere um par que pareça estereo
        if all(c in main_channels for c in PREFERRED_PAIR):
            return PREFERRED_PAIR[0], PREFERRED_PAIR[1], "main (par preferido)"
        a, b = _find_stereo_pair(main_channels)
        if a:
            return a, b, "main (par estereo por nome)"
        return main_channels[0], main_channels[1], "main (2 primeiros de vários)"

    # nenhum canal marcado 'main' -- este kit não usa essa convenção
    # (ex: o test-kit oficial do DrumGizmo). Tenta se virar sozinho:
    if not all_channels:
        return None, None, "sem nenhum canal declarado"

    for ch in all_channels:
        if ch.lower() == (instr_name or "").lower():
            return ch, ch, f"chute: canal com o mesmo nome do instrumento ({ch!r})"

    a, b = _find_stereo_pair(all_channels)
    if a:
        return a, b, f"chute: par que parece estereo pelo nome ({a!r}+{b!r})"

    if len(all_channels) == 1:
        return all_channels[0], all_channels[0], f"chute: único canal disponível ({all_channels[0]!r})"
    return all_channels[0], all_channels[1], f"chute: 2 primeiros canais declarados ({all_channels[0]!r}+{all_channels[1]!r})"


def convert_old_velocities_schema(root, samples_el):
    """Converte o esquema ANTIGO de velocidade do DrumGizmo pro esquema
    NOVO, se for o caso. Esquema antigo (usado pelo 'test-kit' oficial de
    2011, entre outros kits vintage): cada <sample> só tem 'name', sem
    'power', e existe um bloco <velocities> à parte, tipo:
        <velocities>
          <velocity lower="0" upper="1.0"><sampleref name="kick"/></velocity>
        </velocities>
    Isso faz o DrumGizmo instalado hoje (0.9.20) recusar o kit inteiro
    com "Failed to load", sem nenhuma mensagem de erro mais específica --
    descoberto testando um instrumento sozinho até isolar a causa.

    Esquema novo (usado por Crocell/Ludwig/Padrão -- o único que carrega):
        <sample name="kick" power="1.0"> ... </sample>
    (sem bloco <velocities> nenhum -- a intensidade fica só no atributo
    'power' de cada <sample>.)

    Se o instrumento já usa o esquema novo (sem <velocities>, ou <sample>
    já com 'power'), esta função não mexe em nada. Se detectar o esquema
    antigo, atribui a cada <sample> o 'power' = limite superior ('upper')
    da faixa de velocidade em que ele foi referenciado (várias amostras
    na MESMA faixa = variações "round-robin" na mesma força, todas ficam
    com o mesmo power -- é assim que o esquema novo representa isso em
    outros kits), remove o bloco <velocities> e garante version="2.0" no
    <instrument> (o DrumGizmo também rejeitava kits sem essa versão
    declarada nesse ambiente). Devolve True se converteu algo."""
    velocities_el = root.find("velocities")
    if velocities_el is None:
        return False

    power_by_sample_name = {}
    for vel in velocities_el.findall("velocity"):
        try:
            upper = float(vel.get("upper", "1.0"))
        except (TypeError, ValueError):
            upper = 1.0
        upper = max(0.0, min(1.0, upper))
        for ref in vel.findall("sampleref"):
            ref_name = ref.get("name")
            if ref_name:
                power_by_sample_name[ref_name] = f"{upper:.3f}"

    for sample_el in samples_el.findall("sample"):
        if sample_el.get("power") is None:
            sample_el.set("power", power_by_sample_name.get(sample_el.get("name"), "1.000"))

    root.remove(velocities_el)
    if root.get("version") is None:
        root.set("version", "2.0")
    return True


def slim_instrument_file(src_path, dst_path, left_ch, right_ch, orchestra_name, keep_fraction=1.0):
    """Lê o XML do instrumento original e escreve uma versão nova em
    dst_path, mantendo só os <audiofile> dos canais escolhidos (renomeados
    pra 'Out1'/'Out2') e, se keep_fraction < 1.0, também descartando parte
    das camadas de força (<sample>), mantendo uma seleção espalhada pelo
    valor de 'power'. Não mexe no arquivo .wav referenciado -- só no XML
    que aponta pra ele. Também converte o esquema antigo de <velocities>
    pro esquema novo de 'power=', se for o caso (ver
    convert_old_velocities_schema), e força o atributo 'name' do
    <instrument> a bater com 'orchestra_name' (o nome que a orquestra e o
    midimap usam pra esse instrumento) -- descoberto testando o test-kit
    oficial: o kick.xml original tinha internamente name="kick-l" (sobra
    de alguma convenção do autor original), enquanto a orquestra e o
    midimap usam "kick". O DrumGizmo instalado exige que esses nomes
    batam e falha com "Failed to load" (mensagem genérica, sem dizer o
    motivo) quando não batem -- então agora o script sempre alinha os
    dois, não importa o que o arquivo original do instrumento declarava.
    Devolve (total_samples, converteu_esquema_antigo)."""
    tree = ET.parse(src_path)
    root = tree.getroot()  # <instrument>
    samples_el = root.find("samples")
    if samples_el is None:
        raise ValueError(f"'{src_path}' não tem <samples>")

    if orchestra_name and root.get("name") != orchestra_name:
        root.set("name", orchestra_name)

    converted_old_schema = convert_old_velocities_schema(root, samples_el)

    kept_samples = thin_velocity_layers(samples_el, keep_fraction)

    total_samples = 0
    for sample_el in kept_samples:
        total_samples += 1
        audiofiles = list(sample_el.findall("audiofile"))
        left_af = next((a for a in audiofiles if a.get("channel") == left_ch), None)
        right_af = next((a for a in audiofiles if a.get("channel") == right_ch), None)

        for a in audiofiles:
            sample_el.remove(a)

        if left_af is not None:
            left_af.set("channel", OUT_L)
            sample_el.append(left_af)
        if right_af is not None:
            # se for o mesmo canal (duplicado pra virar "mono no centro"),
            # precisa ser um elemento novo -- não dá pra inserir o MESMO
            # objeto duas vezes na árvore
            if right_ch == left_ch:
                right_af = copy.deepcopy(left_af)
                right_af.set("channel", OUT_L)  # ainda não renomeado
            right_af.set("channel", OUT_R)
            sample_el.append(right_af)

    os.makedirs(os.path.dirname(dst_path), exist_ok=True)
    tree.write(dst_path, encoding="UTF-8", xml_declaration=True)
    return total_samples, converted_old_schema


def collect_referenced_media(instr_xml_path):
    """Lê o XML (já cortado) de um instrumento e devolve dois conjuntos:
    - dirs: nomes de subpasta (1o nível, relativo à pasta do instrumento)
      referenciados por algum 'file=' de <audiofile>, ex: {"Samples"},
      {"samples"}, {"audio"} -- o que estiver escrito de fato no XML;
    - files_at_root: nomes de arquivo referenciados SEM nenhuma subpasta
      (o .wav mora ao lado do próprio XML do instrumento).
    Não faz suposição nenhuma sobre convenção de nome -- só repete o que
    o próprio kit já declara."""
    root = ET.parse(instr_xml_path).getroot()
    dirs = set()
    files_at_root = set()
    for audiofile in root.iter("audiofile"):
        file_attr = audiofile.get("file")
        if not file_attr:
            continue
        # normaliza separador (alguns kits foram feitos no Windows)
        file_attr = file_attr.replace("\\", "/")
        rel_dir = os.path.dirname(file_attr)
        if rel_dir:
            dirs.add(rel_dir.split("/")[0])
        else:
            files_at_root.add(file_attr)
    return dirs, files_at_root


def link_referenced_media(src_instr_dir, dst_instr_dir, dst_instr_xml_path):
    """Cria os links simbólicos necessários pra pasta nova do instrumento
    conseguir achar os mesmos .wav que o XML original referencia -- sem
    supor nenhum nome fixo de subpasta (veja collect_referenced_media).
    Os .wav nunca são copiados nem duplicados no disco."""
    os.makedirs(dst_instr_dir, exist_ok=True)
    dirs, files_at_root = collect_referenced_media(dst_instr_xml_path)

    linked = []
    for dirname in sorted(dirs):
        src_sub = os.path.join(src_instr_dir, dirname)
        dst_sub = os.path.join(dst_instr_dir, dirname)
        if os.path.islink(dst_sub) or os.path.exists(dst_sub):
            continue
        if os.path.isdir(src_sub):
            os.symlink(os.path.abspath(src_sub), dst_sub)
            linked.append(dirname)
        else:
            print(f"aviso: pasta de audio '{dirname}' referenciada pelo XML mas nao encontrada em {src_instr_dir}")

    for fname in sorted(files_at_root):
        src_file = os.path.join(src_instr_dir, fname)
        dst_file = os.path.join(dst_instr_dir, fname)
        if os.path.islink(dst_file) or os.path.exists(dst_file):
            continue
        if os.path.isfile(src_file):
            os.symlink(os.path.abspath(src_file), dst_file)
            linked.append(fname)
        else:
            print(f"aviso: arquivo de audio '{fname}' referenciado pelo XML mas nao encontrado em {src_instr_dir}")
    return linked


def build_new_orchestra(kit_name, samplerate, instruments, dst_orchestra_path):
    lines = []
    lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    lines.append(
        f'<drumkit name="{kit_name}{NEW_KIT_NAME_SUFFIX}" '
        'description="Versao enxuta (2 canais) gerada automaticamente por slim_crocell.py '
        'para caber na RAM de um Raspberry Pi -- os .wav originais nao foram alterados." '
        f'version="2.0" samplerate="{samplerate}">'
    )
    lines.append("  <channels>")
    lines.append(f'   <channel name="{OUT_L}"/>')
    lines.append(f'   <channel name="{OUT_R}"/>')
    lines.append("  </channels>")
    lines.append("  <instruments>")
    for instr in instruments:
        if instr.get("_skip"):
            continue
        group_attr = f' group="{instr["group"]}"' if instr.get("group") else ""
        lines.append(f'    <instrument name="{instr["name"]}"{group_attr} file="{instr["file"]}">')
        lines.append(f'      <channelmap in="{OUT_L}" out="{OUT_L}" main="true"/>')
        lines.append(f'      <channelmap in="{OUT_R}" out="{OUT_R}" main="true"/>')
        lines.append("    </instrument>")
    lines.append("  </instruments>")
    lines.append("</drumkit>")
    os.makedirs(os.path.dirname(dst_orchestra_path), exist_ok=True)
    with open(dst_orchestra_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def link_extra_files(src_dir, dst_dir, orchestra_name):
    """Cria links simbólicos, na pasta de saída, para qualquer arquivo solto
    que exista na pasta do kit original e ainda não tenha sido tratado --
    por exemplo o Midimap_*.xml. Isso evita esquecer arquivos de apoio que
    o DrumGizmo espera encontrar do lado do XML principal do kit."""
    os.makedirs(dst_dir, exist_ok=True)
    for entry in os.listdir(src_dir):
        src_entry_path = os.path.join(src_dir, entry)
        if not os.path.isfile(src_entry_path):
            continue  # pastas de instrumento já são tratadas à parte
        if entry == orchestra_name:
            continue  # o XML de orquestra original não é usado (geramos um novo)
        dst_entry_path = os.path.join(dst_dir, entry)
        if os.path.islink(dst_entry_path) or os.path.exists(dst_entry_path):
            continue
        os.symlink(os.path.abspath(src_entry_path), dst_entry_path)
        print(f"(link) {entry} -> {src_entry_path}")


def main():
    raw = list(sys.argv[1:])
    keep_fraction = 1.0
    if "--keep-fraction" in raw:
        idx = raw.index("--keep-fraction")
        try:
            keep_fraction = float(raw[idx + 1])
        except (IndexError, ValueError):
            print("ERRO: --keep-fraction precisa de um número depois, ex: --keep-fraction 0.5")
            sys.exit(1)
        if not (0 < keep_fraction <= 1.0):
            print("ERRO: --keep-fraction deve ser um número entre 0 (exclusivo) e 1.0")
            sys.exit(1)
        del raw[idx:idx + 2]  # remove a flag e o valor, sobram só os posicionais

    channel_overrides = {}
    if "--channel-map" in raw:
        idx = raw.index("--channel-map")
        try:
            override_path = raw[idx + 1]
        except IndexError:
            print("ERRO: --channel-map precisa de um caminho de arquivo .json depois")
            sys.exit(1)
        with open(os.path.expanduser(override_path), encoding="utf-8") as f:
            channel_overrides = json.load(f)
        del raw[idx:idx + 2]

    args = raw
    if len(args) != 3:
        print(__doc__)
        sys.exit(1)

    src_dir = os.path.abspath(os.path.expanduser(args[0]))
    orchestra_name = args[1]
    dst_dir = os.path.abspath(os.path.expanduser(args[2]))

    src_orchestra_path = os.path.join(src_dir, orchestra_name)
    if not os.path.isfile(src_orchestra_path):
        print(f"ERRO: não achei {src_orchestra_path!r}")
        sys.exit(1)

    kit_name, samplerate, instruments, total_channels_declared = parse_orchestra(src_orchestra_path)

    if not samplerate:
        # o kit original nao declara samplerate no XML de orquestra (ja
        # aconteceu com o test-kit oficial do DrumGizmo) -- em vez de
        # chutar um valor fixo (a v1 deste script chutava 48000, e o
        # test-kit real e gravado a 44100 -- o kit inteiro era rejeitado
        # pelo DrumGizmo por causa desse descompasso), abre de verdade um
        # dos .wav referenciados e le a taxa real do cabecalho.
        detected = None
        for instr in instruments:
            candidate_path = os.path.join(src_dir, instr["file"])
            if not os.path.isfile(candidate_path):
                continue
            try:
                instr_root = ET.parse(candidate_path).getroot()
            except ET.ParseError:
                continue
            for audiofile in instr_root.iter("audiofile"):
                file_attr = audiofile.get("file")
                if not file_attr:
                    continue
                wav_path = os.path.join(os.path.dirname(candidate_path), file_attr.replace("\\", "/"))
                detected = detect_wav_samplerate(wav_path)
                if detected:
                    break
            if detected:
                break
        if detected:
            samplerate = str(detected)
            print(f"aviso: '{orchestra_name}' não declara samplerate -- detectei {detected}Hz lendo um .wav real do kit e vou usar esse valor (em vez de chutar um número fixo).")
        else:
            samplerate = "44100"
            print(f"aviso: '{orchestra_name}' não declara samplerate e não consegui abrir nenhum .wav pra detectar -- usando 44100Hz como último recurso. Confira se bate com os arquivos reais do kit.")

    total_antes = 0
    total_depois = 0

    for instr in instruments:
        override = channel_overrides.get(instr["name"])
        if override:
            left_ch = override[0]
            right_ch = override[1] if len(override) > 1 else override[0]
            how = "override manual (--channel-map)"
        else:
            left_ch, right_ch, how = pick_channels(
                instr["name"], instr["main_channels"], instr["all_channels"]
            )
        if left_ch is None:
            instr["_skip"] = True
            print(f"aviso: instrumento {instr['name']!r} sem nenhum canal utilizável ({how}) -- ficou de fora da versão enxuta")
            continue
        if how.startswith("chute"):
            print(f"aviso: {instr['name']!r} não tem canal 'main' declarado -- {how}. Confira de ouvido; se ficar errado, use --channel-map pra corrigir.")

        src_instr_path = os.path.join(src_dir, instr["file"])
        if not os.path.isfile(src_instr_path):
            print(f"aviso: arquivo do instrumento {instr['name']!r} não encontrado ({src_instr_path}) -- pulando")
            instr["_skip"] = True
            continue

        dst_instr_path = os.path.join(dst_dir, instr["file"])
        n_samples_antes = len(ET.parse(src_instr_path).getroot().find("samples").findall("sample"))
        n_samples, converted_old_schema = slim_instrument_file(src_instr_path, dst_instr_path, left_ch, right_ch, instr["name"], keep_fraction)
        canais_antes = total_channels_declared or 15
        total_antes += n_samples_antes * canais_antes
        total_depois += n_samples * 2

        src_instr_dir = os.path.dirname(src_instr_path)
        dst_instr_dir = os.path.dirname(dst_instr_path)
        linked = link_referenced_media(src_instr_dir, dst_instr_dir, dst_instr_path)

        canais_desc = left_ch if left_ch == right_ch else f"{left_ch} + {right_ch}"
        linked_desc = f" [audio: {', '.join(linked)}]" if linked else ""
        schema_desc = " [convertido de <velocities> antigo pra power=]" if converted_old_schema else ""
        if n_samples != n_samples_antes:
            print(f"{instr['name']}: mantido canal(is) {canais_desc} -- {n_samples} de {n_samples_antes} camadas de força{linked_desc}{schema_desc}")
        else:
            print(f"{instr['name']}: mantido canal(is) {canais_desc} -- {n_samples} camadas de força{linked_desc}{schema_desc}")

    dst_orchestra_path = os.path.join(dst_dir, os.path.splitext(orchestra_name)[0] + "_pi.xml")
    build_new_orchestra(kit_name, samplerate, instruments, dst_orchestra_path)
    link_extra_files(src_dir, dst_dir, orchestra_name)

    print()
    print(f"Kit enxuto gerado em: {dst_dir}")
    print(f"Arquivo de orquestra: {dst_orchestra_path}")
    print(f"(estimativa) amostras carregadas: antes ~{total_antes}, depois ~{total_depois}")


if __name__ == "__main__":
    main()

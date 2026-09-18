# Backlog / próximos passos

Log de desenvolvimento deste projeto -- não é necessário ler isto
pra instalar ou usar o sistema (ver `README.md` pra isso). Fica aqui
como registro histórico de decisões, bugs encontrados e por que
certas coisas funcionam do jeito que funcionam, útil sobretudo pra
quem for continuar o desenvolvimento.

Anotações de coisas pra fazer depois. A maioria dos itens abaixo já
foi implementada (marcado ✅, com a data e onde olhar) -- o texto
original do pedido foi mantido pra não perder o contexto de "o que
exatamente" foi pedido, mesmo depois de feito.

## ✅ IMPLEMENTADO (2026-09-18) -- comandos PAD/GLOBAL no firmware (definição manual + aba GLOBAL + edição de pad passam a funcionar de verdade)

Achado enquanto investigava o firmware pra implementar a lógica de 3
zonas do chimbal: o `.ino` que estava no Git só implementava os
comandos `PING`, `GET`, `HH`, `HHSW`, `HHSTATUS`, `SAVE` e `RESET` --
`PAD` e `GLOBAL` respondiam `ERROR,UNKNOWN`, mesmo o backend e o
console web já terem telas/endpoints inteiros contando com eles (aba
GLOBAL, edição de threshold/velmax/lockout/curva de um pad, e a
"definição manual"/MIDI-learn). Você confirmou que queria essa parte
implementada agora ("sim, os dois agora").

**O que foi feito, no firmware (`.ino` + `config_bateria.h`):**

- Cada um dos 8 pads (threshold, velocidade máxima, bloqueio, nota,
  ativo/desativado, curva de velocidade) agora é uma variável em tempo
  de execução (`padThreshold[]`, `padVelMax[]`, `padLockout[]`,
  `padNota[]`, `padAtivo[]`, `padCurva[]`), carregada da memória
  interna do ESP32 na primeira vez com os mesmos valores que já
  existiam como constantes fixas (`LIMIAR_POR_PINO` etc. continuam
  como o padrão de fábrica) -- e configurável dali pra frente pelo
  comando `PAD <idx> <nota> <threshold> <velmax> <lockout> <ativo>
  <curva>`.
- Pad com `ativo=0` é pulado inteiro em `processarPads()` -- nem lê o
  piezo nem pode disparar nota.
- **Curva de velocidade agora é de verdade** -- antes só existia um
  ajuste fixo hardcoded pro Bumbo/Caixa (nem configurável, nem as
  outras peças tinham nada). Agora são 3 curvas de verdade,
  selecionáveis por pad, batendo com o que a tela já prometia (`CURVES
  = ["Linear","Exponencial","Logarítmica"]`): Linear não mexe em nada;
  Exponencial acentua a diferença fraco/forte (pancada fraca fica mais
  fraca ainda); Logarítmica é o espelho (realça pancadas fracas,
  "achata" as fortes, mais fácil tocar suave e ainda ouvir bem).
- `GLOBAL` também virou configurável em tempo real: tempo de pico,
  velocidade mínima, duração da nota e **canal MIDI** (antes sempre
  fixo no canal 1 -- `0x90` cru -- agora `enviarMIDI()`/a CC do pedal
  de hi-hat calculam `0x90 | (canal-1)` de verdade).
- Tudo isso persiste na memória interna do ESP32 (`Preferences`),
  igual o Hi-Hat -- `SAVE`/`RESET` agora gravam/resetam PAD e GLOBAL
  junto com HH/HHSW. `GET` agora devolve `GLOBAL,...` e 8 linhas
  `PAD,0,...` até `PAD,7,...`, além do que já mandava.
- `CAL`/`CALOFF` (streaming ao vivo do valor bruto do piezo pra
  calibração) continuam **não implementados** -- é um recurso
  diferente (telemetria contínua, não configuração), fora do escopo
  desta rodada.

**Bug sério encontrado e corrigido de brinde:** a lista de peças do
console web (`PADS`, no HTML) lista o Hi-Hat na posição 5 e Crash/Ride
nas posições 6/7 -- mas o firmware físico real (`config_bateria.h`)
tem Crash/Ride nos índices 5/6 e o Hi-Hat por último, no índice 7 (o
GPIO do Hi-Hat é dedicado). As duas ordens **nunca bateram**. Sem
perceber isso, editar "Hi-Hat" pela tela teria mandado o comando `PAD`
pro índice físico do Crash (e vice-versa) -- silenciosamente, sem erro
nenhum, porque o firmware aceita qualquer índice 0-7 sem saber o nome
que o console dá pra ele. Corrigido com uma tabela de tradução
(`PAD_ID_TO_FIRMWARE_IDX` em `drum_backend.py`) aplicada em toda
leitura/escrita de `pads` -- confirmada batendo a nota MIDI de cada
posição nas duas listas (a mesma nota tem que cair no mesmo pad
físico) e testada com uma simulação completa (clique na tela -> comando
`PAD` -> resposta do firmware -> snapshot de volta pro console).

Testado isoladamente (sem hardware real disponível aqui): a decisão de
3 curvas, o `ativo=0` pulando o pad inteiro, o cálculo do canal MIDI, o
parsing dos comandos `PAD`/`GLOBAL` (válidos, incompletos, índice fora
de 0-7, curva fora de 0-2 com correção automática) e a tradução
HTML-id/índice-físico -- tudo com scripts Python que espelham a lógica
do `.ino`/`drum_backend.py` linha por linha. **Precisa reflashar o
ESP32** com o `.ino` atualizado (o mesmo reflash que já é necessário
pro chimbal de 3 zonas, se ainda não fez os dois juntos).

## ✅ IMPLEMENTADO (2026-09-18) -- Bug: configurações globais não aparecem no HTML

As configurações globais (GLOBAL: peak_time, min_velocity,
note_duration, midi_channel -- ver `/api/global` em `drum_backend.py`)
não estavam aparecendo na interface web.

**Causa real, achada depois de investigar:** o backend só tinha UMA
tentativa de sincronizar com o ESP32 ao subir (0.3s depois de abrir a
serial) -- e a maioria das placas ESP32 REINICIA sozinha assim que a
porta serial é aberta (efeito do DTR, igual ao Arduino clássico), então
bem nessa hora ele podia ainda estar rebootando e não responder nada.
Sem uma segunda chance, `global_config` ficava vazio pro resto da
execução (e o HTML mostrava "undefined"/"NaN" nos campos, já que não
tinha nenhuma proteção contra isso). O PAD tab não mostrava esse
sintoma tão visivelmente por acaso (tinha um fallback de curva que
escondia o problema), mas sofria da mesma causa.

**Corrigido em:**
- `drum_backend.py` (`_initial_sync`): agora tenta várias vezes (até
  12x, com folga de sobra) até o ESP32 responder, em vez de desistir
  na primeira.
- `drum-module-console.html` (`applyBackendSnapshot`): não sobrescreve
  mais GLOBAL/HI-HAT com valores vazios/parciais.
- Novo botão **"Sincronizar agora"** (aba SISTEMA, e também direto na
  aba GLOBAL quando ainda não sincronizou) -- pra forçar uma
  ressincronização manual a qualquer momento, sem precisar reiniciar
  o backend.

## ✅ IMPLEMENTADO (2026-09-18, revisado 2026-09-18) -- Feature: volume por pad

Adicionar um controle de volume individual por peça/pad (cada peça
com seu próprio nível de saída, independente das outras).

**Como foi feito:** o DrumGizmo não tem um "volume ao vivo" por
instrumento, mas o backend já intercepta toda nota MIDI antes de
mandar pro DrumGizmo -- então o volume por pad é feito escalando a
VELOCITY da nota nesse ponto, guardado em `pad_mixer.json` (chave =
nota MIDI da peça). Efeito **instantâneo**, não recarrega o kit. Ver
slider "Volume desta peça" na aba PAD, ou
`POST /api/mixer/<nota>/volume {"value": 0.0 a 3.0}`.

**Revisado no mesmo dia**, depois de testar num kit real e sentir o
efeito fraco/o controle grosseiro demais:
- Faixa ampliada de 0-150% pra **0-300%** -- 150% ainda não dava conta
  de reforçar uma peça bem baixa.
- Slider mais fino: passo de 5% pra **1%**.
- **0% agora é mudo de verdade** -- antes, mesmo em 0%, a nota ainda
  saía com velocity 1 (quase inaudível, mas não silêncio real). Agora
  o backend simplesmente não manda a nota nenhuma pro DrumGizmo quando
  o volume está zerado (o medidor da tela continua mostrando a
  pancada normalmente, só o áudio que não sai).
- Vale lembrar o limite físico que nenhuma dessas mudanças contorna:
  como a velocity final tem teto em 127, uma pancada que já chega
  quase no talo não tem muito mais "espaço" pra ficar mais alta —
  aumentar o % ajuda principalmente peças que chegam com velocity
  baixa.

## ❌ REMOVIDO (2026-09-18) -- Feature: balanço (pan) esquerda/direita por pad

Tinha sido implementado reprocessando os `.wav` do instrumento (ver
histórico abaixo), mas foi testado num kit real (Crocell) e o Snare
usava o MESMO arquivo de áudio pros dois canais (Out1/Out2) em todas
as 49 camadas de velocidade -- o script corretamente recusou mexer
(nenhuma alteração foi feita, como projetado), mas isso deixou o
recurso não confiável o suficiente pra manter: não dava pra saber de
antemão em quais peças/kits ele funcionaria ou não. Você decidiu
abandonar o pan e focar no volume. Removido: o slider "Balanço" da
aba PAD, o endpoint `POST /api/mixer/<nota>/pan`, e o arquivo
`apply_pad_pan.py` inteiro. `pad_mixer.json` continua só com `volume`
por nota.

<details>
<summary>Como tinha sido implementado (histórico, pra referência)</summary>

Ao contrário do volume, pan não dava pra fazer só na velocity --
precisava reprocessar de verdade os arquivos de áudio do instrumento,
aplicando um ganho diferente em cada lado (lei de pan de potência
constante), sempre partindo de uma cópia intocada do original e
recarregando o kit depois (por isso demorava alguns segundos, ao
contrário do volume). Tinha uma proteção pro caso do instrumento usar
o MESMO arquivo pros dois canais (recusava mexer em vez de arriscar
quebrar o som) -- esse caso não tinha aparecido nos testes com
fixtures sintéticas, mas apareceu no kit real (Snare do Crocell), e foi
o motivo de abandonar o recurso.
</details>

## ✅ IMPLEMENTADO (2026-09-18) -- Feature: definição manual de pad ("aprender" batendo na peça)

Hoje a configuração de um pad é feita clicando na imagem da peça na
tela (como já funciona). Quero adicionar uma opção "definição manual"
dentro dessa tela de configuração do pad: ao clicar nela, o sistema
entra num modo de escuta; a próxima pancada física que chegar (em
qualquer pad conectado) é reconhecida e associada àquele pad que
estava sendo configurado -- a partir daquele momento, é aquele pad
físico que passa a mandar a nota configurada pra aquela peça (uma
espécie de "MIDI learn": bater na peça física em vez de escolher/
confiar só na imagem/fiação).

**Como foi feito:** botão "Definição manual (bater na peça)" na aba
PAD. Ao clicar, o backend entra em modo de escuta por até 15s
(`POST /api/learn/start`); a próxima pancada física reconhecida tem
seu índice de pad físico descoberto (por qual índice já está
configurado com a nota que realmente chegou) e reconfigurado no ESP32
(comando `PAD`) pra passar a usar a nota da peça que estava sendo
definida. Resultado chega via WebSocket (`{"type":"learn_result"}`).

**Atualização (2026-09-18):** dependia do comando `PAD` no firmware,
que na época ainda não existia (dava timeout no hardware real) -- ver
a seção **"comandos PAD/GLOBAL no firmware"** no topo deste arquivo.
Já implementado; precisa só reflashar o ESP32 com o `.ino` atualizado
pra essa função passar a funcionar de verdade.

## Feature: Raspberry criar sua própria rede Wi-Fi + redirecionar pro HTML sozinho

Hoje o Raspberry só entra como CLIENTE na rede Wi-Fi de casa (é assim
que ele aparece num IP tipo `192.168.24.7` -- não cria rede própria
nenhuma). Quero que ele também crie uma rede Wi-Fi própria (modo
Access Point/hotspot -- ex: rede `MODULO-BATERIA`, Pi sempre num IP
fixo tipo `192.168.4.1`), e que quando um celular conectar nessa rede,
seja direcionado sozinho pro HTML (efeito "portal cativo", tipo Wi-Fi
de aeroporto/hotel que já abre uma tela de login sozinha) -- sem
garantia de funcionar 100% em todo aparelho/versão de Android/iOS, mas
funciona na maioria; nos outros, abrir o navegador e digitar o IP uma
vez resolve.

Decisão já tomada sobre como isso deve coexistir com a rede de casa:
o Raspberry deve conseguir manter a rede própria (hotspot) E a rede de
casa **ao mesmo tempo** -- não é só uma ou só a outra. Como o Wi-Fi do
Pi não dá pra estar em modo cliente e modo Access Point ao mesmo tempo
na mesma interface, isso só funciona de verdade com uma conexão de
apoio cabeada: o Pi ligado à rede de casa por cabo Ethernet, e o Wi-Fi
dele dedicado 100% a ser o hotspot -- assim dá pra continuar entrando
por SSH/rodando `setup_kits.py` pela rede de casa (via cabo) enquanto
o hotspot próprio atende os celulares dos músicos. O Raspberry Pi 3B+
usado neste projeto já tem porta Ethernet embutida, então isso não
deve exigir comprar nenhum adaptador -- só ligar um cabo de rede nele
quando for preciso mexer/atualizar.

## ✅ IMPLEMENTADO (2026-09-18) -- Feature: incluir o chimbal meio-aberto na versão reduzida do Crocell + lógica de 3 zonas no pedal

Hoje a versão reduzida do Crocell (`CrocellKit_pi3`, gerada a partir de
`CrocellKit_tiny2.xml`) não tem uma amostra separada de chimbal
meio-aberto -- só o que já veio no "tiny" original + o `HihatPedal` que
adicionamos depois.

O mecanismo pra adicionar já existe e é genérico -- foi o mesmo usado
pro `HihatPedal`: o `build_hihat_pedal_kit.py` recebe o NOME do
instrumento e a NOTA MIDI como parâmetro (não é hardcoded só pro
`HihatPedal`), copia esse instrumento (com os canais certos) do
`CrocellKit_full.xml` pro reduzido, e copia o mapeamento da nota do
`Midimap_full.xml` junto -- depois só roda o `slim_crocell.py` de
novo.

**Achado no kit real** (você rodou os comandos abaixo e mandou o
resultado): o instrumento se chama `HihatSemiOpen`, nota MIDI **80**.

```
grep -o 'instrument name="[^"]*"' ~/DrumGizmo/kits/kits/crocellkit/CrocellKit/CrocellKit_full.xml
cat ~/DrumGizmo/kits/kits/crocellkit/CrocellKit/Midimap_full.xml
```

**O que foi feito:** `setup_kits.py` agora encadeia uma SEGUNDA
passada do `build_hihat_pedal_kit.py` (`--instrumento HihatSemiOpen
--nota 80`) em cima do resultado da primeira (que já adiciona o
`HihatPedal`), gerando `CrocellKit_tiny22.xml`/`Midimap_tiny22.xml` --
depois `slim_crocell.py` reduz normalmente. Testado de ponta a ponta
com fixtures sintéticas (13 instrumentos, nota 80 mapeada, canais
escolhidos automaticamente sem precisar de `--channel-map`, igual ao
`HihatPedal`). `kits.json` já aponta pro nome novo. **Basta rodar**
`python3 setup_kits.py --only crocell` (ou `--force`) no Raspberry pra
a amostra existir no kit.

**Parte 2 (firmware, 2026-09-18): o pedal agora escolhe a nota 80
sozinho pela posição.** Antes, o firmware só decidia entre 2 notas
pro chimbal (fechado=42/aberto=46, limiar fixo `HIHAT_LIMIAR_FECHADO`
em `config_bateria.h`, não configurável). Agora são 3 zonas -- aberto
(46) / meio-aberto (80) / fechado (42) -- decididas por
`escolherNotaHiHat()` no `.ino`, usando dois limiares configuráveis
pela tela HI-HAT do console (**não são mais constantes fixas no
firmware**, ficam salvos na memória interna do ESP32 igual o resto da
config do chimbal):

- **Início do meio-aberto** e **Início do fechado** -- dois sliders
  novos na aba HI-HAT (posição normalizada 0-127, mesma escala da
  leitura ao vivo), abaixo dos sliders de calibração bruta que já
  existiam. O firmware corrige sozinho se os dois valores vierem
  invertidos (meio-aberto >= fechado).
- Protocolo `HH` estendido de 6 pra 8 parâmetros
  (`HH <ativo> <tipo> <aberto> <fechado> <filtro> <invertido>
  <limiarMeioAberto> <limiarFechado>`), com retrocompatibilidade: um
  firmware mais antigo (só 6 parâmetros) continua funcionando, só sem
  os dois limiares novos.
- Com o **microswitch ativo**, a decisão continua binária (aberto/
  fechado) -- o switch não tem posição intermediária, então a zona
  meio-aberto só existe usando o sensor contínuo (potenciômetro).
- Testado isoladamente (sem hardware real disponível aqui): as 3
  zonas, os casos de segurança (sensor desativado/sem leitura -> força
  fechado), o comportamento com microswitch, a correção automática de
  limiares invertidos/nos extremos (0 e 127), o parsing do comando
  `HH` nos dois formatos (6 e 8 parâmetros) e a resposta `formatarHH()`
  -- tudo validado com um script Python que espelha a lógica do `.ino`
  linha por linha. **Ainda precisa reflashar o ESP32** com o `.ino`
  atualizado pra isso valer de verdade (a lógica em si não foi rodada
  no hardware físico).

## Feature: usar como controlador MIDI USB pra um Windows (ou outro PC)

Ideia: conectar por USB e o módulo funcionar como um controlador MIDI
de verdade pra um computador (Windows, por exemplo), já aplicando as
configurações de velocity/limiar/tudo mais que hoje ficam salvas no
ESP32 -- pra poder usar com outros programas/VSTs de bateria, não só
com o DrumGizmo no próprio Raspberry.

**Descoberta importante (muda o plano):** o Raspberry Pi 3B+ NÃO
consegue fazer isso pelas próprias portas USB dele -- segundo a
documentação oficial da Raspberry Pi Foundation, as portas USB do
3B+ passam por um chip hub que impede o "modo dispositivo" (OTG/gadget
mode); ele só funciona como HOST USB (só enxerga o que é plugado nele,
nunca se apresenta como um dispositivo MIDI pra outro PC). Isso é uma
limitação de hardware do 3B+ especificamente -- só os modelos Zero/
Zero 2 W e os Compute Module conseguem operar em modo dispositivo.

**Caminho real: fazer isso pelo ESP32, não pelo Raspberry.** Faz
sentido também porque as configurações de velocity/limiar já são
calculadas DENTRO do ESP32 antes de qualquer coisa chegar no Pi (ele já
manda "pad tal, nota tal, velocidade tal" pronto pela serial). Dois
sub-caminhos possíveis, dependendo do chip exato usado:

1. Se o ESP32 for uma variante com USB nativo (ESP32-S2/S3 -- tem
   "S2"/"S3" escrito na própria placa) -- dá pra fazer o firmware se
   apresentar direto como um dispositivo MIDI USB "de verdade"
   (plug-and-play no Windows, sem instalar driver nenhum).
2. Se for um ESP32 comum (sem USB nativo, o mais popular) -- precisa
   mudar o firmware pra mandar mensagens MIDI reais pela mesma porta
   serial que já existe, e instalar um programa gratuito no Windows
   (ex: "Hairless MIDI-Serial Bridge") que transforma essa serial numa
   porta MIDI que qualquer DAW/programa de música reconhece.

**Falta confirmar:** qual o modelo exato do ESP32 usado (o texto
escrito na própria placa, ex: "ESP32-WROOM-32", "ESP32-S3", etc.) --
isso decide qual dos dois caminhos seguir.

## ✅ IMPLEMENTADO -- best-effort, não testado num Raspberry real (2026-09-18) -- Feature: gravar no PC (Reaper + EZdrummer/Addictive Drums) via MIDI de rede, sem mexer no hardware do módulo

**Como usar:** rodar `sudo ./setup_rtpmidi.sh` (opcional -- só quem for
gravar precisa). Detalhes completos no README, seção "RTP-MIDI".

**Aviso importante:** este script foi escrito com base na
documentação oficial do rtpmidid (não deu pra testar contra um
Raspberry real neste momento) -- ele avisa e para em vez de adivinhar
se algo não bater (ex: pacote não disponível no apt desta versão do
Raspberry Pi OS). Se algum passo falhar, roda o comando indicado na
mensagem de erro -- se mesmo assim não resolver, abra uma issue no
repositório descrevendo o que apareceu.

Pedido original, mantido pra contexto:

Contexto: a ideia é enclausurar tudo (ESP32 + Raspberry + interface de
áudio) num módulo só, transportável, plug-and-play (liga na tomada,
toca). Mas às vezes vai querer gravar no PC com Reaper usando
EZdrummer/Addictive Drums (que são instrumentos VST -- precisam
receber MIDI, não áudio) -- sem abrir/modificar o módulo de jeito
nenhum.

**Solução: RTP-MIDI ("MIDI de rede"/AppleMIDI)**, usando a rede que o
módulo já tem -- nenhuma mudança de hardware ou firmware:

1. Instalar o `rtpmidid` no Raspberry (um serviço a mais, do mesmo
   jeito que o `drum-backend` já roda) -- ele pega a porta MIDI virtual
   que já existe hoje ("ESP32 Drum", a mesma que o DrumGizmo escuta) e
   disponibiliza ela pela rede.
2. Instalar o driver gratuito **rtpMIDI** (de Tobias Erichsen) no
   Windows -- não vem instalado por padrão, precisa baixar uma vez.
3. Emparelhar os dois uma vez no programa rtpMIDI do Windows
   (adicionar o endereço do Raspberry e clicar "Connect" -- às vezes
   ele acha sozinho por Bonjour, senão adiciona na mão). Depois de
   emparelhado, fica automático: pluga o cabo, abre o Reaper, escolhe
   aquela porta MIDI de entrada na faixa do EZdrummer/Addictive Drums,
   e as batidas chegam sozinhas.
4. Pra gravar (diferente do uso ao vivo, que é por Wi-Fi mesmo, pros
   celulares dos músicos), ligar o Raspberry no PC com Windows por um
   **cabo de rede (Ethernet) direto, sem roteador no meio** -- a
   latência por Wi-Fi é "visivelmente maior" e principalmente mais
   INCONSISTENTE (isso atrapalha mais que um atraso fixo, que o Reaper
   consegue até compensar com "latency offset" de gravação); por cabo
   direto, fica bem baixa e fixa. Recomendado configurar um IP fixo
   simples em cada ponta (ex: Raspberry `192.168.10.1`, Windows
   `192.168.10.2`) pra não depender da auto-negociação de rede, que
   pode demorar uns segundos toda vez que conectar o cabo.

## ✅ IMPLEMENTADO (2026-09-18) -- Feature: botão físico de liga/desliga seguro (sem precisar de tela/teclado)

**Como usar:** rodar `sudo ./setup_shutdown_button.sh` (idempotente,
seguro rodar mesmo antes do botão físico estar ligado) + `sudo reboot`
uma vez. Ligar o botão entre o pino físico 5 (GPIO3) e o pino físico 6
(GND) só na hora de montar a caixa. Detalhes no README.

Pedido original, mantido pra contexto:

Contexto: o módulo vai ficar enclausurado (ESP32 + Raspberry + interface
de áudio), sem tela nem teclado -- então precisa de um jeito de desligar
com segurança (evitar corrupção por tirar da tomada direto, ver o item
do Wi-Fi que sumiu, causado por isso) sem precisar entrar por SSH toda
vez.

**Solução: usar o recurso `gpio-shutdown` já embutido no Raspberry Pi
OS** -- não precisa de nenhum programa/serviço extra:

1. Ligar um botão simples (2 fios) entre o pino **GPIO3** e um pino de
   **GND** vizinho na régua de pinos do Raspberry.
2. Adicionar uma linha no arquivo `/boot/firmware/config.txt`:
   ```
   dtoverlay=gpio-shutdown
   ```
3. Reiniciar uma vez pra aplicar.

A partir daí: apertou o botão, o sistema desliga sozinho com segurança
(equivalente a `sudo shutdown -h now`) -- como é a versão "Lite" (sem
ambiente gráfico), desliga direto, sem tela de confirmação nenhuma.
Bônus: esse mesmo botão também LIGA o Raspberry de volta se apertado
enquanto ele estiver desligado (propriedade do próprio pino GPIO3) --
na prática vira um botão único de liga/desliga do módulo.

**Detalhe importante:** isso desliga o sistema operacional com
segurança, mas não cort a energia física -- ainda precisa tirar da
tomada (ou usar uma tomada com interruptor) depois pra cortar de vez,
só que aí sim sem risco de corromper nada (esperar o LED verde parar
de piscar antes).

**Falta decidir/fazer na hora de montar a caixa física:** onde
posicionar o botão no módulo, e testar se o `dtoverlay=gpio-shutdown`
funciona direto nessa versão do Raspberry Pi OS ou se precisa de algum
ajuste (alguns relatos de usuários do Bookworm mencionam precisar
habilitar "Remote GPIO" nas configurações -- não confirmado se isso se
aplica à versão Lite usada aqui).

---
*(este arquivo é só uma lista de pedidos pra não esquecer -- nenhum
destes itens foi implementado ainda)*

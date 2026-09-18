# drum-module -- Módulo de Bateria Eletrônica (ESP32 + DrumGizmo + Raspberry Pi)

Backend que liga um módulo de bateria eletrônica baseado em ESP32 ao
[DrumGizmo](https://drumgizmo.org/) rodando num Raspberry Pi, com um
console web (`drum-module-console.html`) pra configurar tudo pelo
celular/tablet enquanto o Pi toca o som.

Licenciado sob a [GPL-3.0-or-later](LICENSE) -- veja a seção
"Licença" no final deste README pra detalhes (inclusive sobre as
amostras de áudio de terceiros, que **não** são cobertas por ela).

Este repositório é o instalador "do zero": ele baixa as amostras
originais de cada kit direto da internet e aplica por cima, sozinho,
todas as correções específicas de cada kit que já são conhecidas (ver
`slim_crocell.py` e a seção "O que cada kit precisa" mais abaixo) --
assim, instalar num Raspberry novo (ou recuperar de um cartão SD
corrompido) não exige caçar os mesmos problemas de novo.

## O que tem aqui

```
LICENSE                           -- GPL-3.0-or-later (ver seção "Licença" no final)
drum_backend.py                   -- backend (Flask + WebSocket + MIDI)
kit_manager.py                    -- controla o processo do DrumGizmo
pad_mixer.py                      -- volume por pad (guardado no Pi, não no ESP32 -- ver "Mixer por pad")
drum-module-console.html          -- console web (servido pelo backend)
install.sh                        -- instala o serviço systemd
drum-backend.service.template     -- modelo do serviço (install.sh preenche)
setup_rtpmidi.sh                  -- (opcional) expõe o MIDI pela rede pra gravar no PC -- ver "RTP-MIDI"
setup_shutdown_button.sh          -- (opcional) botão físico de liga/desliga seguro -- ver "Botão de liga/desliga"
kits.json                         -- configuração dos kits (setup_kits.py atualiza sozinho)
pad_mixer.json                    -- volume salvo por pad (criado sozinho no primeiro ajuste)
setup_kits.py                     -- baixa e monta os 3 kits do zero (ver Passo 4)
slim_crocell.py                   -- reduz canais/camadas de um kit DrumGizmo pra caber na RAM do Pi
                                      (generalizado: se adaptar sozinho a qualquer kit novo que
                                      tenha uma "particularidade" parecida com as 3 já resolvidas --
                                      ver o cabeçalho do próprio arquivo pra detalhes)
build_hihat_pedal_kit.py          -- adiciona o instrumento HihatPedal (nota 44) ao Crocell
normalize_ludwig.py               -- normaliza volume por instrumento (necessário só pro Ludwig)
firmware/
  firmware.ino                    -- firmware do ESP32 (abra a PASTA firmware/ no Arduino IDE)
  config_bateria.h                -- configuração dos pinos/notas/limiares
```

As pastas de áudio dos kits (`~/DrumGizmo/kits/kits/...`) **não** ficam
neste repositório -- são vários GB de `.wav` baixados/gerados pelo
`setup_kits.py` na hora da instalação (ver Passo 4). Isso também
significa que qualquer atualização futura no kit oficial upstream (ex:
o DrumGizmo lançar uma versão nova do `test-kit`) já vem automaticamente
na próxima instalação, sem precisar mexer neste repositório.

## 1. Preparar o Raspberry Pi OS Lite

Grava o Raspberry Pi OS **Lite** (64-bit) no cartão SD com o Raspberry
Pi Imager, já habilitando SSH e configurando Wi-Fi/usuário nas opções
avançadas do próprio Imager (ícone de engrenagem antes de gravar) --
assim ele já sobe na rede sem precisar de monitor.

Liga o Pi, descobre o IP dele (pelo roteador, ou `ping raspberrypi.local`)
e entra por SSH:

```
ssh <usuario>@<ip-do-pi>
```

## 2. Instalar as dependências do sistema

```
sudo apt update
sudo apt install -y python3 python3-pip python3-serial python3-rtmidi \
    python3-flask drumgizmo alsa-utils git wget unzip ffmpeg
```

(`drumgizmo` empacotado no apt é o mesmo usado em produção neste
projeto -- versão `0.9.20`; não precisa compilar do código-fonte.
`wget`/`unzip`/`ffmpeg`/`git` são usados pelo `setup_kits.py` no Passo
4, pra baixar e montar os kits.)

O `flask-sock` (WebSocket) normalmente não vem empacotado no apt --
instala via pip (no Raspberry Pi OS atual precisa do `--break-system-packages`,
já que o Python do sistema é "gerenciado" e bloqueia `pip install` direto):

```
pip install flask-sock --break-system-packages
```

Confere que o DrumGizmo instalou certo:

```
drumgizmo --version
```

## 3. Copiar os arquivos deste repositório pro Pi

Se o Pi tiver acesso à internet, o mais simples é clonar direto nele
(por padrão isso já cria a pasta `~/drum-module`, com esse nome, sem
precisar especificar):

```
git clone https://github.com/<seu-usuario>/drum-module.git
cd drum-module
```

Ou, do seu computador, `scp -r` a pasta descompactada (mantendo o
mesmo nome `drum-module`, pra bater com o resto deste guia e com o
que o `install.sh` vai detectar sozinho):

```
scp -r drum-module <usuario>@<ip-do-pi>:~/drum-module
```

## 4. Montar os kits (baixa da internet e aplica as correções sozinho)

Este é o passo que substitui ter que copiar `~/DrumGizmo` manualmente
de um Pi antigo. Rodando **como o usuário normal, sem sudo** (os
arquivos de kit ficam com o dono certo, que é quem o serviço vai
rodar como depois):

```
cd ~/drum-module
python3 setup_kits.py
```

Isso baixa e monta, do zero, os 3 kits usados neste projeto:

- **Padrão** -- o `test-kit` oficial do próprio DrumGizmo (kit
  acústico real, usado como kit de teste/padrão do sistema). O kit
  original é de 2011 e tem 3 particularidades que o DrumGizmo instalado
  hoje rejeita silenciosamente (nenhum canal `main` declarado, taxa de
  amostragem não declarada, esquema antigo de `<velocities>` em vez de
  `power=`) -- o `slim_crocell.py` corrige tudo isso sozinho. Por cima
  disso, o `setup_kits.py` também escreve um `midimap_pi.xml` próprio
  deste projeto (o midimap original do kit usa notas MIDI diferentes
  das que o firmware do ESP32 realmente envia).
- **Ludwig** (Black Cortex) -- clonado do GitHub, depois passa por uma
  normalização de volume por instrumento (`normalize_ludwig.py`,
  necessária porque os instrumentos foram gravados/exportados com
  níveis bem diferentes entre si) antes de ser reduzido.
- **Crocell** -- baixado como `.zip`, primeiro ganha os instrumentos
  `HihatPedal` (nota 44, pedal chick) e `HihatSemiOpen` (nota 80,
  chimbal meio-aberto) que não vêm na variante "tiny" usada de base --
  duas passadas encadeadas de `build_hihat_pedal_kit.py`, cada uma
  copiando o instrumento da variante "full" do próprio kit (gera
  `CrocellKit_tiny2.xml` e depois `CrocellKit_tiny22.xml`) --, depois é
  reduzido com um ajuste manual de canal (`--channel-map`) pro Ride,
  que tem um microfone próprio separado dos overheads e senão a
  detecção automática escolheria o par errado. O `HihatSemiOpen` fica
  disponível no kit/mapeamento, e o firmware do ESP32 já manda essa
  nota sozinho pela posição do pedal (3 zonas -- aberto/meio-aberto/
  fechado, com os dois limiares ajustáveis na aba HI-HAT do console) --
  ver `BACKLOG.md` pra detalhes. **Precisa reflashar o ESP32** com o
  `.ino` atualizado pra essa parte valer de verdade.

No final, `python3 setup_kits.py` atualiza o `kits.json` sozinho
apontando pros 3 kits prontos. Rodar de novo sem `--force` pula
qualquer kit que já esteja montado (útil se um dos 3 falhar por causa
de rede -- só os que faltam são refeitos). Outras opções:

```
python3 setup_kits.py --force                # remonta os 3 do zero
python3 setup_kits.py --only crocell          # só um kit específico
python3 setup_kits.py --only ludwig,crocell
```

Isso demora alguns minutos (depende da internet do Pi) e usa alguns GB
de espaço no cartão SD -- os `.wav` originais de cada kit ficam em
`~/DrumGizmo/kits/kits/...` e nunca são apagados nem sobrescritos pelas
reduções (que só criam pastas novas ao lado, com links simbólicos pro
áudio original).

### Se um kit precisar de um ajuste novo no futuro

Se algum dia um kit novo (ou uma atualização de um destes 3) tiver uma
particularidade que o `slim_crocell.py` ainda não sabe resolver
sozinho, o próprio script tem um mecanismo genérico de escape
(`--channel-map`, ver o `--help`/cabeçalho do arquivo) em vez de
precisar de gambiarra por kit -- e o ideal é generalizar a detecção
automática dentro dele (como já foi feito para os 3 casos acima) em
vez de colocar um `if` específico pro nome de um kit.

## 5. Instalar o serviço

Dentro da pasta copiada no Pi (depois do Passo 4 já ter montado os
kits e atualizado o `kits.json`):

```
cd ~/drum-module
sudo ./install.sh
```

O script detecta sozinho o usuário certo (quem chamou o `sudo`), gera
o `/etc/systemd/system/drum-backend.service` a partir do modelo, e já
deixa o serviço ativo e configurado pra subir sozinho no boot.

Confere que subiu:

```
systemctl status drum-backend --no-pager
journalctl -u drum-backend -f
```

E testa abrindo `http://<ip-do-pi>:8000` no celular.

## 6. Firmware do ESP32

Se o ESP32 já foi gravado com este mesmo firmware antes, não precisa
reflashar. Se for um ESP32 novo (ou se quiser regravar por qualquer
motivo), abre a pasta `firmware/` direto no Arduino IDE (File > Open, aponte pra
pasta -- o nome dela já bate com `firmware.ino` por dentro, então abre
sem precisar reorganizar nada) e grava normalmente. O `config_bateria.h`
é reconhecido automaticamente como uma segunda aba do mesmo sketch.

Pinos usados (ver `config_bateria.h` pra lista completa/ajustável):
- 8 pads piezo: GPIOs 13, 14, 27, 26, 25, 33, 32, 35 (o último é o Hi-Hat)
- Potenciômetro de posição do Hi-Hat: GPIO 34
- Microswitch do Hi-Hat: GPIO 4 (outra perna no GND, sem resistor -- usa pull-up interno)

## 7. Ligando tudo

Ordem recomendada ao ligar o Pi: placa de som USB e ESP32 já
conectados ANTES de ligar o Pi (o serviço espera alguns segundos no
boot e tenta detectar sozinho, mas é mais confiável já estar tudo
plugado). Se precisar reconectar algo depois que o Pi já ligou, um
`sudo systemctl restart drum-backend` resolve.

## Mixer por pad (volume)

Cada peça (identificada pela nota MIDI dela) pode ter um volume
próprio, guardado em `pad_mixer.json` neste Raspberry -- **não** no
ESP32 (a sensibilidade do trigger continua lá; isso aqui é só sobre
como a nota que já chegou é tocada). Ajustável pela aba **PAD** do
console (selecione a peça primeiro):

- **Volume desta peça** (0% a 300%): escala a velocity da nota antes
  de mandar pro DrumGizmo. Efeito **instantâneo** -- não recarrega o
  kit. `POST /api/mixer/<nota>/volume {"value": 0.0 a 3.0}`. Em 0% a
  nota nem chega a ser enviada -- mudo de verdade, não só "bem baixo".
  Acima de 100% reforça uma peça fraca, mas numa pancada que já chega
  perto do máximo não tem muito mais o que aumentar (limite físico da
  velocity MIDI, teto em 127).

(O balanço/pan esquerda-direita por pad foi removido -- ver
`BACKLOG.md` pro motivo: um dos kits reais usava o mesmo arquivo de
áudio pros dois canais numa peça, tornando o recurso não confiável o
suficiente pra manter.)

## Definição manual de pad ("bater pra configurar")

Na aba **PAD**, ao configurar uma peça, o botão **"Definição manual
(bater na peça)"** entra num modo de escuta por até 15 segundos: a
próxima pancada física reconhecida faz aquele pad físico passar a
mandar a nota da peça que estava sendo configurada, a partir de agora
(reconfigura o ESP32 direto, com o mesmo comando `PAD` que a tela já
usa) -- uma alternativa a escolher a peça pela imagem, útil se a
fiação física não bate com a ordem esperada pelo firmware.

## RTP-MIDI -- gravar no PC (Reaper, EZdrummer, Addictive Drums, etc.)

Opcional, só pra quem quiser gravar no computador -- o uso ao vivo (com
os celulares/tablets dos músicos) não precisa disso.

```
sudo ./setup_rtpmidi.sh
```

Isso instala e configura o [rtpmidid](https://github.com/davidmoreno/rtpmidid)
(RTP-MIDI/AppleMIDI pra Linux), expondo a porta MIDI virtual "ESP32
Drum" pela rede. No Windows, instale o driver gratuito **rtpMIDI** (de
Tobias Erichsen) -- este Raspberry deve aparecer sozinho na lista
"Remote Sessions" (via Bonjour/mDNS); clique "Connect". Depois disso,
qualquer DAW no Windows (Reaper, etc.) vai ver uma porta MIDI de
entrada nova pra escolher na faixa do instrumento (EZdrummer/Addictive
Drums).

Recomendado pra **gravar** (diferente do uso ao vivo, que é por Wi-Fi
mesmo): ligar um cabo de rede direto entre o Raspberry e o PC, sem
roteador no meio -- menos latência e bem mais estável que Wi-Fi.

Aviso: este script ainda não foi validado contra hardware real em
todas as versões do Raspberry Pi OS (foi escrito com base na
documentação oficial do rtpmidid) -- por segurança, ele avisa e para
(em vez de adivinhar) se algum passo não bater com o esperado (ex:
pacote não disponível no `apt` desta versão do sistema -- nesse caso
ele mesmo mostra como baixar o `.deb` direto do GitHub). Se algo
falhar, o aviso impresso já indica o comando alternativo pra rodar;
se mesmo assim não resolver, abra uma
[issue](https://github.com/esbelarminojr/drum-module/issues) neste
repositório descrevendo o que apareceu.

## Botão físico de liga/desliga seguro

Pra desligar com segurança sem precisar de tela/teclado/SSH (evita
corromper o cartão SD por tirar da tomada direto -- ver o item do
Wi-Fi que "some" mais abaixo):

```
sudo ./setup_shutdown_button.sh
sudo reboot
```

Fiação (na hora de montar a caixa física): um botão simples entre o
pino físico 5 (GPIO3) e o pino físico 6 (GND, bem ao lado) na régua de
40 pinos. Depois de reiniciar: apertar desliga com segurança (igual
`sudo shutdown -h now`); apertar de novo com o Raspberry desligado,
liga -- na prática um botão único de liga/desliga. Isso desliga o
sistema operacional com segurança, mas não corta a energia física --
ainda precisa tirar da tomada depois (só que aí sem risco, esperando o
LED verde parar de piscar antes).

## Solução de problemas rápida

- `journalctl -u drum-backend -n 50 --no-pager` -- log do backend (Python)
- `tail -40 ~/drumgizmo.log` -- log do próprio DrumGizmo (carregamento do kit, erros de áudio)
- `curl -s http://localhost:8000/api/kits` -- confere se o backend está respondendo e qual kit está ativo
- Se der "dispositivo ocupado" no ALSA logo depois de trocar de kit, espera alguns segundos e tenta de novo -- é a placa de som ainda sendo liberada pelo processo anterior.
- Se o `setup_kits.py` falhar num dos 3 kits (ex: queda de internet no meio do download), roda de novo -- ele pula os kits que já montaram certo e só refaz o que faltou.
- "pasta do kit não existe" ao trocar de kit pelo console: o `setup_kits.py` ainda não rodou (ou falhou) pro kit escolhido -- confere `kits.json` e roda `python3 setup_kits.py --only <kit>`.
- Valores estranhos ("undefined"/vazios) na aba GLOBAL, ou parâmetros que parecem não ter sido salvos de verdade: clique **"Sincronizar agora"** na aba SISTEMA (ou na própria aba GLOBAL) -- força reler tudo do ESP32 agora. O backend já tenta se sincronizar sozinho ao subir (com várias tentativas, pra dar tempo do ESP32 terminar de reiniciar), mas esse botão resolve na hora se algo ainda ficou desatualizado.
- **Raspberry não aparece mais na rede depois de ficar dias desligado** (mas continua acessível ligando um cabo de rede direto nele): provavelmente o Wi-Fi "esqueceu" a rede salva -- causa mais comum é ter desligado tirando o plugue da tomada direto, em vez de um desligamento correto (`sudo shutdown -h now`, esperando o LED verde parar de piscar antes de tirar da tomada), o que pode corromper o arquivo de configuração do Wi-Fi no cartão SD (o do cabo raramente é afetado, por isso só o Wi-Fi some). Pra confirmar e resolver, com o cabo de rede plugado:
  ```
  ip -brief addr           # confirma: eth0 com IP, wlan0 "DOWN" ou sem IP
  nmcli connection show    # confirma: só aparece a conexão do cabo, nenhuma de Wi-Fi
  sudo nmcli device wifi list                                  # confere se a rede de casa aparece por perto
  sudo nmcli device wifi connect "NOME_DA_REDE" password "SENHA"   # cadastra ela de novo
  ip -brief addr           # confirma que wlan0 já tem IP -- aí pode tirar o cabo
  ```

## Alternativa: já tenho outro Raspberry com os kits prontos

Esta seção só se aplica a quem já tem **outra** instalação deste
projeto funcionando -- por exemplo, montando uma segunda unidade, ou
trocando o cartão SD de um Raspberry que já tinha tudo pronto. **Se
esta é a sua primeira instalação, ignore esta seção e siga pro Passo
4 normalmente** (que baixa e monta os kits pela internet).

Nesses casos, pra não depender da internet de novo (ou pra garantir
bit-a-bit a mesma versão que já está funcionando na outra máquina),
dá pra pular o Passo 4 inteiro e só copiar a pasta `~/DrumGizmo` já
pronta de um Raspberry pro outro:

```
# rodando isso NO RASPBERRY QUE JÁ TEM os kits prontos
scp -r ~/DrumGizmo <usuario>@<ip-do-pi-novo>:~/
```

(ou via pendrive, copiando `~/DrumGizmo` pro pendrive e do pendrive pro
Pi novo). Nesse caso o `kits.json` deste repositório já aponta pros
caminhos certos por padrão, então não precisa editar nada -- só pula
direto pro Passo 5.

## Licença

O código deste repositório (backend Python, firmware do ESP32,
scripts de instalação/montagem de kit e o console web) é distribuído
sob a **GNU General Public License v3.0 (ou, à sua escolha, qualquer
versão posterior)** -- veja o arquivo [`LICENSE`](LICENSE) pro texto
completo. Resumindo: qualquer um pode usar, estudar, modificar e
redistribuir esse código, inclusive em versões modificadas, desde que
mantenha a mesma licença e não feche o código de qualquer versão
redistribuída.

**Isso NÃO cobre as amostras de áudio dos kits de bateria** (Padrão/
test-kit oficial do DrumGizmo, Ludwig Black Cortex, Crocell) -- elas
nunca ficam neste repositório (ver "O que tem aqui" no topo) e
continuam sob a licença original de quem as gravou/distribuiu; o
`setup_kits.py` só baixa e reorganiza o que já está disponível
publicamente por cada projeto de origem. Se for redistribuir este
projeto publicamente, confira a licença de cada kit na fonte
original antes de incluir os `.wav` prontos junto (o mais seguro,
que é o que este repositório já faz, é deixar o `setup_kits.py`
baixar tudo na hora da instalação, em vez de empacotar os `.wav`).

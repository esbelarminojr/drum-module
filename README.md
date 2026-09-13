# Módulo de Bateria Eletrônica (ESP32 + DrumGizmo + Raspberry Pi)

Backend que liga um módulo de bateria eletrônica baseado em ESP32 ao
[DrumGizmo](https://drumgizmo.org/) rodando num Raspberry Pi, com um
console web (`drum-module-console.html`) pra configurar tudo pelo
celular/tablet enquanto o Pi toca o som.

Este repositório é o instalador "do zero": ele baixa as amostras
originais de cada kit direto da internet e aplica por cima, sozinho,
todas as correções específicas de cada kit que foram descobertas na
prática (ver `slim_crocell.py` e a seção "O que cada kit precisa" mais
abaixo) -- assim, reinstalar num Raspberry novo (ou recuperar este
depois de um cartão SD corrompido) não exige mais caçar os mesmos bugs
de novo.

## O que tem aqui

```
drum_backend.py                   -- backend (Flask + WebSocket + MIDI)
kit_manager.py                    -- controla o processo do DrumGizmo
drum-module-console.html          -- console web (servido pelo backend)
install.sh                        -- instala o serviço systemd
drum-backend.service.template     -- modelo do serviço (install.sh preenche)
kits.json                         -- configuração dos kits (setup_kits.py atualiza sozinho)
setup_kits.py                     -- baixa e monta os 3 kits do zero (ver Passo 4)
slim_crocell.py                   -- reduz canais/camadas de um kit DrumGizmo pra caber na RAM do Pi
                                      (generalizado: se adaptar sozinho a qualquer kit novo que
                                      tenha uma "particularidade" parecida com as 3 já resolvidas --
                                      ver o cabeçalho do próprio arquivo pra detalhes)
build_hihat_pedal_kit.py          -- adiciona o instrumento HihatPedal (nota 44) ao Crocell
normalize_ludwig_v2.py            -- normaliza volume por instrumento (necessário só pro Ludwig)
firmware/
  bateria_eletronica-03-09.ino    -- firmware do ESP32 (Arduino IDE)
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

Se o Pi tiver acesso à internet, o mais simples é clonar direto nele:

```
git clone <url-do-seu-repositorio-no-github> ~/drum-module
cd ~/drum-module
```

Ou, do seu computador, `scp -r` a pasta descompactada:

```
scp -r drum-module-package <usuario>@<ip-do-pi>:~/drum-module
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
  normalização de volume por instrumento (`normalize_ludwig_v2.py`,
  necessária porque os instrumentos foram gravados/exportados com
  níveis bem diferentes entre si) antes de ser reduzido.
- **Crocell** -- baixado como `.zip`, primeiro ganha o instrumento
  `HihatPedal` (nota 44) que não vem na variante "tiny" usada de base
  (`build_hihat_pedal_kit.py` copia esse instrumento da variante "full"
  do próprio kit), depois é reduzido com um ajuste manual de canal
  (`--channel-map`) pro Ride, que tem um microfone próprio separado dos
  overheads e senão a detecção automática escolheria o par errado.

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

Se for usar o mesmo ESP32 de antes, não precisa reflashar -- ele já
está com o firmware certo. Se for gravar um ESP32 novo (ou reflashar),
abre `firmware/bateria_eletronica-03-09.ino` no Arduino IDE (a pasta
`firmware/` tem que ter esse nome igual ao `.ino` por dentro, senão o
Arduino IDE reclama -- se copiar solto, cria uma pasta com o mesmo nome
do arquivo e coloca os dois dentro) e grava normalmente.

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

## Solução de problemas rápida

- `journalctl -u drum-backend -n 50 --no-pager` -- log do backend (Python)
- `tail -40 ~/drumgizmo.log` -- log do próprio DrumGizmo (carregamento do kit, erros de áudio)
- `curl -s http://localhost:8000/api/kits` -- confere se o backend está respondendo e qual kit está ativo
- Se der "dispositivo ocupado" no ALSA logo depois de trocar de kit, espera alguns segundos e tenta de novo -- é a placa de som ainda sendo liberada pelo processo anterior.
- Se o `setup_kits.py` falhar num dos 3 kits (ex: queda de internet no meio do download), roda de novo -- ele pula os kits que já montaram certo e só refaz o que faltou.
- "pasta do kit não existe" ao trocar de kit pelo console: o `setup_kits.py` ainda não rodou (ou falhou) pro kit escolhido -- confere `kits.json` e roda `python3 setup_kits.py --only <kit>`.

## Alternativa: copiar um `~/DrumGizmo` já pronto de outro Pi

Se preferir não depender da internet na hora da instalação (ou quiser
garantir bit-a-bit a mesma versão que já está tocando em outro Pi), dá
pra pular o Passo 4 inteiro e só copiar a pasta `~/DrumGizmo` já pronta
do Pi antigo pro novo:

```
# rodando isso NO RASPBERRY ATUAL (o de onde os kits estão vindo)
scp -r ~/DrumGizmo <usuario>@<ip-do-pi-novo>:~/
```

(ou via pendrive, copiando `~/DrumGizmo` pro pendrive e do pendrive pro
Pi novo). Nesse caso o `kits.json` deste repositório já aponta pros
caminhos certos por padrão, então não precisa editar nada -- só pula
direto pro Passo 5.

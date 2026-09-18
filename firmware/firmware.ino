// SPDX-License-Identifier: GPL-3.0-or-later
/*
 * ================================================================
 * BATERIA ELETRÔNICA COM ESP32
 * 8 PADS + HI-HAT (POTENCIÔMETRO / MICROSWITCH)
 *
 * ESP32 -> USB Serial -> Python/RtMidi -> DrumGizmo
 *
 * Além de tocar as notas (como antes), este firmware agora também
 * fala o protocolo de configuração que o drum_backend.py (Raspberry)
 * já esperava: comandos de texto chegam em uma linha (ex: "HH 1 1
 * 0 1900 8 0\n"), e as respostas saem encapsuladas em SysEx MIDI
 * (F0 7D 'D' 'R' texto F7) para não se misturar com as notas MIDI
 * de verdade (que continuam sendo bytes 0x90/0x80 crus, como antes).
 *
 * Comandos aceitos agora (CAL/CALOFF -- streaming ao vivo do piezo
 * pra calibração -- ainda não implementado neste firmware):
 *   PING                                        -> PONG
 *   GET                                         -> BEGINCONFIG, GLOBAL,..., HH,..., HHSW,...,
 *                                                   PAD,0,..  PAD,7,.. (um por pad), ENDCONFIG
 *   HH <ativo> <tipo> <aberto> <fechado> <filtro> <invertido> [<limiarMeioAberto> <limiarFechado>]
 *                                                -> os 2 últimos parâmetros são opcionais
 *                                                   (retrocompatível com consoles antigos de 6
 *                                                   parâmetros); se omitidos, os limiares atuais
 *                                                   não mudam.
 *   HHSW <ativo> <invertido> <forcar_fechado>
 *   HHSTATUS                                    -> HHSTATUS,<raw>,<posicao>,<switch>,<tipo>,<potRaw>
 *   PAD <idx 0-7> <nota> <threshold> <velmax> <lockout> <ativo> <curva 0-2>
 *                                                -> configura um pad (threshold/vel.máxima/
 *                                                   bloqueio/ativo/curva de velocidade -- curva:
 *                                                   0=Linear, 1=Exponencial, 2=Logarítmica). A
 *                                                   nota também pode ser trocada aqui -- é como a
 *                                                   "definição manual" (MIDI-learn) do console
 *                                                   reatribui qual peça física manda qual nota.
 *   GLOBAL <peak_time_ms> <min_velocity> <note_duration_ms> <midi_channel 1-16>
 *   SAVE                                        -> grava tudo (HH/HHSW/PAD/GLOBAL) na memória
 *                                                   interna (sobrevive a reiniciar)
 *   RESET                                       -> volta tudo pros valores padrão de fábrica
 *
 * Além disso, o firmware agora detecta sozinho o "pedal chick": toda
 * vez que o hi-hat passa de aberto pra fechado (pelo sensor contínuo
 * ou pelo microswitch, o que estiver ativo), dispara a nota 44 (ver
 * verificarPedalChick()), sem precisar bater no pad com a baqueta.
 *
 * Zonas do sensor contínuo do Hi-Hat (potenciômetro, posição 0-127):
 * agora são 3 zonas em vez de 2 -- ABERTO (nota 46), MEIO-ABERTO
 * (nota 80) e FECHADO (nota 42) -- ver escolherNotaHiHat(). Os dois
 * limiares que dividem essas zonas (hhLimiarMeioAberto/hhLimiarFechado)
 * são configuráveis pelo console web e persistem na memória interna
 * (Preferences), igual o resto da config do Hi-Hat. Com o microswitch
 * ativo, a decisão continua sendo binária (só ABERTO/FECHADO), já que
 * o switch não tem posição intermediária.
 * ================================================================
 */

#include <Preferences.h>

#include "config_bateria.h"


// ================================================================
// VARIÁVEIS DOS PADS (detecção de batida -- igual antes)
// ================================================================

bool detectandoBatida[NUM_PADS] = {false};
unsigned long tempoInicioLeitura[NUM_PADS] = {0};
unsigned long tempoUltimaBatida[NUM_PADS] = {0};
int picoValor[NUM_PADS] = {0};


// ================================================================
// CONFIGURAÇÃO POR PAD (configurável via console web / comando PAD,
// persistida na memória interna do ESP32 -- ver
// carregarConfigPads()/salvarConfigPads()/resetarConfigPads())
// ================================================================

int padNota[NUM_PADS];
int padThreshold[NUM_PADS];
int padVelMax[NUM_PADS];
int padLockout[NUM_PADS];
bool padAtivo[NUM_PADS];
int padCurva[NUM_PADS];  // 0=Linear, 1=Exponencial, 2=Logarítmica


// ================================================================
// CONFIGURAÇÃO GLOBAL (configurável via console web / comando
// GLOBAL, persistida na memória interna do ESP32 -- ver
// carregarConfigGlobal()/salvarConfigGlobal()/resetarConfigGlobal())
// ================================================================

int globalPeakTime = GLOBAL_TEMPO_ESPERA_PICO_MS_PADRAO;
int globalMinVelocity = GLOBAL_VELOCIDADE_MINIMA_PADRAO;
int globalNoteDuration = GLOBAL_NOTE_DURATION_MS_PADRAO;
int globalMidiChannel = GLOBAL_MIDI_CHANNEL_PADRAO;  // 1-16


// ================================================================
// ESTADO DO HI-HAT (configurável via console web / protocolo serial)
// ================================================================

bool hhAtivo = HH_ATIVO_PADRAO;
int hhTipo = HH_TIPO_PADRAO;
int hhAberto = HH_ABERTO_PADRAO;
int hhFechado = HH_FECHADO_PADRAO;
int hhFiltro = HH_FILTRO_PADRAO;
bool hhInvertido = HH_INVERTIDO_PADRAO;

bool hhswAtivo = HHSW_ATIVO_PADRAO;
bool hhswInvertido = HHSW_INVERTIDO_PADRAO;
bool hhswForcarFechado = HHSW_FORCAR_FECHADO_PADRAO;

int hhLimiarMeioAberto = HH_LIMIAR_MEIO_ABERTO_PADRAO;  // posição 0-127 -- início do "meio-aberto"
int hhLimiarFechado = HH_LIMIAR_FECHADO_PADRAO;         // posição 0-127 -- início do "fechado"

int hhRawAtual = -1;        // último valor bruto lido (ADC ou mm) -- -1 = sem leitura
int hhPosicaoAtual = -1;    // 0-127 normalizado (-1 = sem leitura válida)
int hhUltimaPosicaoEnviada = -1;  // última posição mandada por CC4 (evita spam)

bool hihatFechadoAnterior = true;      // estado da última verificação (começa fechado = padrão seguro)
unsigned long tempoUltimoPedalChick = 0;

Preferences prefs;


// ================================================================
// SETUP
// ================================================================

void setup() {

  Serial.begin(SERIAL_BAUDRATE);

  analogReadResolution(12);
  analogSetAttenuation(ADC_11db);

  delay(500);

  pinMode(GPIO_HIHAT_POSICAO, INPUT);
  pinMode(GPIO_HIHAT_SWITCH, INPUT_PULLUP);  // pressionado = LOW

  carregarConfigHiHat();
  carregarConfigPads();
  carregarConfigGlobal();
}


// ================================================================
// CONFIGURAÇÃO POR PAD -- carregar/salvar/resetar (memória interna
// do ESP32). Cada pad usa 6 chaves próprias ("p0n".."p7c", nomes
// curtos porque o Preferences só aceita até 15 caracteres de chave).
// ================================================================

void carregarConfigPads() {
  prefs.begin("pads", true);  // true = somente leitura

  for (int i = 0; i < NUM_PADS; i++) {
    String p = "p" + String(i);
    padNota[i] = prefs.getInt((p + "n").c_str(), NOTAS_MIDI[i]);
    padThreshold[i] = prefs.getInt((p + "t").c_str(), LIMIAR_POR_PINO[i]);
    padVelMax[i] = prefs.getInt((p + "v").c_str(), VELOCIDADE_MAXIMA_POR_PINO[i]);
    padLockout[i] = prefs.getInt((p + "l").c_str(), BLOQUEIO_POR_PINO[i]);
    padAtivo[i] = prefs.getBool((p + "a").c_str(), true);
    padCurva[i] = prefs.getInt((p + "c").c_str(), 0);  // 0 = Linear
  }

  prefs.end();
}

void salvarConfigPads() {
  prefs.begin("pads", false);  // false = leitura/escrita

  for (int i = 0; i < NUM_PADS; i++) {
    String p = "p" + String(i);
    prefs.putInt((p + "n").c_str(), padNota[i]);
    prefs.putInt((p + "t").c_str(), padThreshold[i]);
    prefs.putInt((p + "v").c_str(), padVelMax[i]);
    prefs.putInt((p + "l").c_str(), padLockout[i]);
    prefs.putBool((p + "a").c_str(), padAtivo[i]);
    prefs.putInt((p + "c").c_str(), padCurva[i]);
  }

  prefs.end();
}

void resetarConfigPads() {
  for (int i = 0; i < NUM_PADS; i++) {
    padNota[i] = NOTAS_MIDI[i];
    padThreshold[i] = LIMIAR_POR_PINO[i];
    padVelMax[i] = VELOCIDADE_MAXIMA_POR_PINO[i];
    padLockout[i] = BLOQUEIO_POR_PINO[i];
    padAtivo[i] = true;
    padCurva[i] = 0;
  }

  salvarConfigPads();
}


// ================================================================
// CONFIGURAÇÃO GLOBAL -- carregar/salvar/resetar (memória interna
// do ESP32)
// ================================================================

void carregarConfigGlobal() {
  prefs.begin("global", true);  // true = somente leitura

  globalPeakTime = prefs.getInt("peakTime", GLOBAL_TEMPO_ESPERA_PICO_MS_PADRAO);
  globalMinVelocity = prefs.getInt("minVel", GLOBAL_VELOCIDADE_MINIMA_PADRAO);
  globalNoteDuration = prefs.getInt("noteDur", GLOBAL_NOTE_DURATION_MS_PADRAO);
  globalMidiChannel = prefs.getInt("midiCh", GLOBAL_MIDI_CHANNEL_PADRAO);

  prefs.end();
}

void salvarConfigGlobal() {
  prefs.begin("global", false);  // false = leitura/escrita

  prefs.putInt("peakTime", globalPeakTime);
  prefs.putInt("minVel", globalMinVelocity);
  prefs.putInt("noteDur", globalNoteDuration);
  prefs.putInt("midiCh", globalMidiChannel);

  prefs.end();
}

void resetarConfigGlobal() {
  globalPeakTime = GLOBAL_TEMPO_ESPERA_PICO_MS_PADRAO;
  globalMinVelocity = GLOBAL_VELOCIDADE_MINIMA_PADRAO;
  globalNoteDuration = GLOBAL_NOTE_DURATION_MS_PADRAO;
  globalMidiChannel = GLOBAL_MIDI_CHANNEL_PADRAO;

  salvarConfigGlobal();
}


// ================================================================
// CONFIGURAÇÃO DO HI-HAT -- carregar/salvar/resetar (memória interna
// do ESP32, sobrevive a desligar/religar -- o Raspberry não precisa
// mandar a config de novo toda vez que liga)
// ================================================================

void carregarConfigHiHat() {
  prefs.begin("hihat", true);  // true = somente leitura

  hhAtivo = prefs.getBool("hhAtivo", HH_ATIVO_PADRAO);
  hhTipo = prefs.getInt("hhTipo", HH_TIPO_PADRAO);
  hhAberto = prefs.getInt("hhAberto", HH_ABERTO_PADRAO);
  hhFechado = prefs.getInt("hhFechado", HH_FECHADO_PADRAO);
  hhFiltro = prefs.getInt("hhFiltro", HH_FILTRO_PADRAO);
  hhInvertido = prefs.getBool("hhInv", HH_INVERTIDO_PADRAO);

  hhswAtivo = prefs.getBool("swAtivo", HHSW_ATIVO_PADRAO);
  hhswInvertido = prefs.getBool("swInv", HHSW_INVERTIDO_PADRAO);
  hhswForcarFechado = prefs.getBool("swForcar", HHSW_FORCAR_FECHADO_PADRAO);

  hhLimiarMeioAberto = prefs.getInt("hhLimMA", HH_LIMIAR_MEIO_ABERTO_PADRAO);
  hhLimiarFechado = prefs.getInt("hhLimF", HH_LIMIAR_FECHADO_PADRAO);

  prefs.end();

  corrigirOrdemLimiaresHiHat();
}

void salvarConfigHiHat() {
  prefs.begin("hihat", false);  // false = leitura/escrita

  prefs.putBool("hhAtivo", hhAtivo);
  prefs.putInt("hhTipo", hhTipo);
  prefs.putInt("hhAberto", hhAberto);
  prefs.putInt("hhFechado", hhFechado);
  prefs.putInt("hhFiltro", hhFiltro);
  prefs.putBool("hhInv", hhInvertido);

  prefs.putBool("swAtivo", hhswAtivo);
  prefs.putBool("swInv", hhswInvertido);
  prefs.putBool("swForcar", hhswForcarFechado);

  prefs.putInt("hhLimMA", hhLimiarMeioAberto);
  prefs.putInt("hhLimF", hhLimiarFechado);

  prefs.end();
}

void resetarConfigHiHat() {
  hhAtivo = HH_ATIVO_PADRAO;
  hhTipo = HH_TIPO_PADRAO;
  hhAberto = HH_ABERTO_PADRAO;
  hhFechado = HH_FECHADO_PADRAO;
  hhFiltro = HH_FILTRO_PADRAO;
  hhInvertido = HH_INVERTIDO_PADRAO;

  hhswAtivo = HHSW_ATIVO_PADRAO;
  hhswInvertido = HHSW_INVERTIDO_PADRAO;
  hhswForcarFechado = HHSW_FORCAR_FECHADO_PADRAO;

  hhLimiarMeioAberto = HH_LIMIAR_MEIO_ABERTO_PADRAO;
  hhLimiarFechado = HH_LIMIAR_FECHADO_PADRAO;

  salvarConfigHiHat();
}

// Garante hhLimiarMeioAberto < hhLimiarFechado -- se algum comando HH
// mandar os dois valores invertidos (por engano ou versão antiga do
// console), corrige em vez de deixar o hi-hat com lógica quebrada.
void corrigirOrdemLimiaresHiHat() {
  if (hhLimiarMeioAberto >= hhLimiarFechado) {
    int tmp = hhLimiarMeioAberto;
    hhLimiarMeioAberto = hhLimiarFechado;
    hhLimiarFechado = tmp;
  }
  if (hhLimiarMeioAberto == hhLimiarFechado) {
    // Empatados (ex: os dois em 0, ou os dois em 127) -- abre um espaço
    // mínimo de 1 entre eles, preferindo subir o "fechado"; só desce o
    // "meio-aberto" se o "fechado" já estiver no teto (127).
    if (hhLimiarFechado < 127) {
      hhLimiarFechado++;
    } else {
      hhLimiarMeioAberto--;
    }
  }
}


// ================================================================
// LEITURA BRUTA DO POTENCIÔMETRO
// ================================================================

// Leitura RÁPIDA (1 amostra só, sem delay nenhum) -- é a usada dentro
// do loop principal (ver atualizarHiHat()). A suavização não vem de
// tirar média de várias amostras de uma vez (isso bloquearia o loop
// por ~2ms toda volta, atrapalhando a detecção de pico dos OUTROS
// pads), e sim de uma média móvel calculada ao longo de várias voltas
// do loop em atualizarHiHat() -- custo por volta: só 1 analogRead().
int lerRawPotenciometroInstantaneo() {
  return analogRead(GPIO_HIHAT_POSICAO);
}

// Leitura FILTRADA (várias amostras, com delay entre elas) -- só é
// chamada sob demanda, quando alguém pede HHSTATUS (tela de
// configuração aberta, a cada ~300ms) pra mostrar o valor do
// potenciômetro mesmo quando ele NÃO é o sensor ativo no momento.
// Nunca é chamada a partir do loop principal.
int lerRawPotenciometroMedia() {
  long soma = 0;
  int n = hhFiltro > 0 ? hhFiltro : 1;

  for (int i = 0; i < n; i++) {
    soma += analogRead(GPIO_HIHAT_POSICAO);
    delayMicroseconds(300);
  }

  return soma / n;
}



// ================================================================
// NORMALIZA A LEITURA BRUTA PRA 0-127 (0=aberto, 127=fechado, já
// considerando a inversão configurada)
// ================================================================

int normalizarPosicaoHiHat(int raw) {
  if (raw < 0) {
    return -1;
  }

  long posicao = map(raw, hhAberto, hhFechado, 0, 127);
  posicao = constrain(posicao, 0, 127);

  if (hhInvertido) {
    posicao = 127 - posicao;
  }

  return posicao;
}


// ================================================================
// ATUALIZA A LEITURA DO HI-HAT (chamado uma vez por loop) E MANDA
// CC4 (Foot Controller) se a posição mudou o suficiente
// ================================================================

void atualizarHiHat() {
  if (!hhAtivo) {
    hhRawAtual = -1;
    hhPosicaoAtual = -1;
    return;
  }

  if (hhTipo == HH_TIPO_POTENCIOMETRO) {
    // 1 amostra só (sem delay) + média móvel -- ver
    // lerRawPotenciometroInstantaneo() pra entender por quê.
    int amostra = lerRawPotenciometroInstantaneo();
    if (hhRawAtual < 0) {
      hhRawAtual = amostra;  // primeira leitura -- inicializa direto, sem suavizar
    } else {
      int n = hhFiltro > 0 ? hhFiltro : 1;
      hhRawAtual += (amostra - hhRawAtual) / n;  // média móvel exponencial
    }
  } else {
    hhRawAtual = -1;  // OFF/HALL/IR -- ainda não implementado
  }

  hhPosicaoAtual = normalizarPosicaoHiHat(hhRawAtual);

  if (hhPosicaoAtual < 0) {
    return;  // sem leitura válida -- não manda CC
  }

  if (
    hhUltimaPosicaoEnviada >= 0 &&
    abs(hhPosicaoAtual - hhUltimaPosicaoEnviada) < 3
  ) {
    return;  // mudança pequena demais, evita spam de CC
  }

  hhUltimaPosicaoEnviada = hhPosicaoAtual;

  byte canal = constrain(globalMidiChannel, 1, 16) - 1;  // mesmo canal configurado em GLOBAL
  Serial.write(0xB0 | canal);  // Control Change
  Serial.write(4);             // CC4 - Foot Controller
  Serial.write(hhPosicaoAtual);
}


// ================================================================
// DECIDE SE O HI-HAT ESTÁ "FECHADO" NESTE INSTANTE
// ================================================================
//
// Prioridade: microswitch (se ativo) > sensor contínuo > padrão
// seguro (fechado), exatamente nessa ordem.
//

bool hihatEstaFechado() {

  if (hhswAtivo) {
    if (hhswForcarFechado) {
      return true;
    }
    bool pressionado = (digitalRead(GPIO_HIHAT_SWITCH) == LOW);  // pull-up
    if (hhswInvertido) {
      pressionado = !pressionado;
    }
    return pressionado;
  }

  if (!hhAtivo) {
    return true;  // Hi-Hat desativado -- comportamento simples de antes
  }

  if (hhPosicaoAtual < 0) {
    return true;  // sem leitura válida -- assume fechado por segurança
  }

  return hhPosicaoAtual >= hhLimiarFechado;
}


// ================================================================
// ESCOLHE A NOTA DO HI-HAT QUANDO O PAD É ATINGIDO
// ================================================================
//
// Com microswitch ativo, continua sendo uma decisão binária (usa
// hihatEstaFechado(), igual antes -- o microswitch não tem posição
// intermediária). Com o sensor contínuo (potenciômetro), agora são
// 3 zonas em vez de 2, usando hhLimiarMeioAberto/hhLimiarFechado
// (configuráveis pelo console web):
//
//   posição >= hhLimiarFechado                          -> FECHADO (42)
//   posição >= hhLimiarMeioAberto (e < hhLimiarFechado)  -> MEIO-ABERTO (80)
//   posição <  hhLimiarMeioAberto                        -> ABERTO (46)
//

int escolherNotaHiHat() {

  if (hhswAtivo) {
    return hihatEstaFechado() ? NOTA_HIHAT_FECHADO : NOTA_HIHAT_ABERTO;
  }

  if (!hhAtivo || hhPosicaoAtual < 0) {
    return NOTA_HIHAT_FECHADO;  // sem leitura válida -- padrão seguro (igual hihatEstaFechado())
  }

  if (hhPosicaoAtual >= hhLimiarFechado) {
    return NOTA_HIHAT_FECHADO;
  }

  if (hhPosicaoAtual >= hhLimiarMeioAberto) {
    return NOTA_HIHAT_MEIO_ABERTO;
  }

  return NOTA_HIHAT_ABERTO;
}


// ================================================================
// PEDAL CHICK -- detecta a transição ABERTO -> FECHADO do hi-hat e
// dispara a nota 44 sozinho, sem precisar de batida no pad. Chamado
// uma vez por loop, logo depois de atualizarHiHat()/hihatEstaFechado()
// já refletirem o estado mais recente do sensor/switch.
// ================================================================

void verificarPedalChick() {

  bool fechadoAgora = hihatEstaFechado();

  if (!hihatFechadoAnterior && fechadoAgora) {
    // transição aberto -> fechado
    if (millis() - tempoUltimoPedalChick > PEDAL_CHICK_BLOQUEIO_MS) {
      enviarMIDI(NOTA_HIHAT_PEDAL, VELOCIDADE_PEDAL_CHICK);
      tempoUltimoPedalChick = millis();

      #ifdef DEBUG_ENABLED
      Serial.println("PEDAL CHICK (nota 44)");
      #endif
    }
  }

  hihatFechadoAnterior = fechadoAgora;
}


// ================================================================
// CURVA DE VELOCIDADE -- 3 curvas de verdade agora (configurável por
// pad, comando PAD/campo "curva"), em vez do ajuste fixo que só
// existia pro Bumbo/Caixa antes:
//
//   0 = Linear       -- não mexe na velocidade medida.
//   1 = Exponencial  -- acentua a diferença entre fraco/forte (pancada
//                       fraca fica mais fraca ainda, forte fica perto
//                       do máximo).
//   2 = Logarítmica  -- o contrário: realça pancadas fracas, "achata"
//                       as fortes (mais fácil tocar suave e ainda
//                       ouvir bem).
// ================================================================

int aplicarCurva(int padIndex, int velocidade) {

  int curva = padCurva[padIndex];

  if (curva == 0 || velocidade <= 0) {
    return velocidade;  // Linear -- sem alteração
  }

  float norm = velocidade / 127.0;
  float k = 2.0;  // intensidade da curva

  float resultado = (curva == 1) ? pow(norm, k) : pow(norm, 1.0 / k);

  int v = (int)round(resultado * 127.0);
  return constrain(v, 1, 127);
}


// ================================================================
// ENVIA NOTA MIDI -- respeita o canal MIDI configurável (GLOBAL)
// ================================================================

void enviarMIDI(int nota, int velocidade) {

  if (velocidade < globalMinVelocity) {
    return;
  }

  velocidade = constrain(velocidade, 1, 127);
  byte canal = constrain(globalMidiChannel, 1, 16) - 1;  // 1-16 na tela -> 0-15 de verdade

  Serial.write(0x90 | canal);
  Serial.write(nota);
  Serial.write(velocidade);

  delay(globalNoteDuration);

  Serial.write(0x80 | canal);
  Serial.write(nota);
  Serial.write(0);
}


// ================================================================
// PROCESSA OS PADS (igual antes, só a escolha da nota do Hi-Hat mudou)
// ================================================================

void processarPads() {

  for (int i = 0; i < NUM_PADS; i++) {

    if (!padAtivo[i]) {
      continue;  // pad desativado (comando PAD, campo "ativo") -- nem lê nem dispara
    }

    int valor = analogRead(GPIO_PADS[i]);
    int limiar = padThreshold[i];
    int bloqueioMs = padLockout[i];
    int velMax = padVelMax[i];

    if (!detectandoBatida[i] && valor > limiar) {
      if (millis() - tempoUltimaBatida[i] > bloqueioMs) {
        detectandoBatida[i] = true;
        tempoInicioLeitura[i] = millis();
        picoValor[i] = valor;
      }
    }

    if (detectandoBatida[i]) {

      if (valor > picoValor[i]) {
        picoValor[i] = valor;
      }

      if (millis() - tempoInicioLeitura[i] > globalPeakTime) {

        detectandoBatida[i] = false;
        tempoUltimaBatida[i] = millis();

        if (picoValor[i] > limiar) {

          int velocidade = map(picoValor[i], limiar, velMax, globalMinVelocity, 127);
          velocidade = constrain(velocidade, globalMinVelocity, 127);
          velocidade = aplicarCurva(i, velocidade);

          // ------------------------------------------------
          // ESCOLHE A NOTA (Hi-Hat depende do sensor/switch ativo)
          // ------------------------------------------------

          int nota = padNota[i];

          if (i == HIHAT_PAD_INDEX) {
            nota = escolherNotaHiHat();
          }

          if (velocidade > 0) {

            enviarMIDI(nota, velocidade);

            #ifdef DEBUG_ENABLED
            Serial.print(NOME_PADS[i]);
            Serial.print(" | ADC: ");
            Serial.print(picoValor[i]);
            Serial.print(" | MIDI: ");
            Serial.print(nota);
            Serial.print(" | Vel: ");
            Serial.println(velocidade);
            #endif
          }
        }
      }
    }
  }
}


// ================================================================
// PROTOCOLO DE CONFIGURAÇÃO (SysEx) -- recebe comandos de texto do
// Raspberry, responde encapsulado em SysEx pra não se misturar com
// as notas MIDI reais.
// ================================================================

String bufferComando = "";

void enviarResposta(const String &texto) {
  Serial.write(0xF0);
  Serial.write(0x7D);
  Serial.write('D');
  Serial.write('R');
  Serial.print(texto);
  Serial.write(0xF7);
}

String formatarHH() {
  String s = "HH,";
  s += (hhAtivo ? "1" : "0"); s += ",";
  s += String(hhTipo); s += ",";
  s += String(hhAberto); s += ",";
  s += String(hhFechado); s += ",";
  s += String(hhFiltro); s += ",";
  s += (hhInvertido ? "1" : "0"); s += ",";
  s += String(hhLimiarMeioAberto); s += ",";
  s += String(hhLimiarFechado);
  return s;
}

String formatarHHSW() {
  String s = "HHSW,";
  s += (hhswAtivo ? "1" : "0"); s += ",";
  s += (hhswInvertido ? "1" : "0"); s += ",";
  s += (hhswForcarFechado ? "1" : "0");
  return s;
}

String formatarPad(int idx) {
  String s = "PAD,";
  s += String(idx); s += ",";
  s += String(padNota[idx]); s += ",";
  s += String(padThreshold[idx]); s += ",";
  s += String(padVelMax[idx]); s += ",";
  s += String(padLockout[idx]); s += ",";
  s += (padAtivo[idx] ? "1" : "0"); s += ",";
  s += String(padCurva[idx]);
  return s;
}

String formatarGlobal() {
  String s = "GLOBAL,";
  s += String(globalPeakTime); s += ",";
  s += String(globalMinVelocity); s += ",";
  s += String(globalNoteDuration); s += ",";
  s += String(globalMidiChannel);
  return s;
}

String formatarHHStatus() {
  int switchAtual = (digitalRead(GPIO_HIHAT_SWITCH) == LOW) ? 1 : 0;

  // Se o potenciômetro já é o sensor ativo, reaproveita a leitura que
  // atualizarHiHat() fez neste loop (sem custo extra). Se não for
  // (HH desativado, por exemplo), lê na hora -- só acontece quando
  // alguém pede HHSTATUS (tela de configuração aberta, a cada ~300ms).
  int potRaw = (hhTipo == HH_TIPO_POTENCIOMETRO) ? hhRawAtual : lerRawPotenciometroMedia();

  String s = "HHSTATUS,";
  s += String(hhRawAtual); s += ",";
  s += String(hhPosicaoAtual); s += ",";
  s += String(switchAtual); s += ",";
  s += String(hhTipo); s += ",";
  s += String(potRaw);
  return s;
}

// Conta quantos tokens separados por espaço existem numa String
int contarTokens(const String &linha) {
  int n = 0;
  bool dentro = false;
  for (unsigned int i = 0; i < linha.length(); i++) {
    if (linha[i] == ' ') {
      dentro = false;
    } else if (!dentro) {
      dentro = true;
      n++;
    }
  }
  return n;
}

// Pega o token de índice `idx` (separado por espaço) como inteiro
long tokenInt(const String &linha, int idx) {
  int atual = -1;
  int inicio = -1;

  for (unsigned int i = 0; i <= linha.length(); i++) {
    bool fimDePalavra = (i == linha.length() || linha[i] == ' ');
    bool comecoDePalavra = (i < linha.length() && linha[i] != ' ' && inicio < 0);

    if (comecoDePalavra) {
      inicio = i;
    }
    if (fimDePalavra && inicio >= 0) {
      atual++;
      if (atual == idx) {
        return linha.substring(inicio, i).toInt();
      }
      inicio = -1;
    }
  }
  return 0;
}

void processarComando(String linha) {
  linha.trim();
  if (linha.length() == 0) {
    return;
  }

  String cmd = linha;
  int espaco = linha.indexOf(' ');
  if (espaco >= 0) {
    cmd = linha.substring(0, espaco);
  }
  cmd.toUpperCase();

  if (cmd == "PING") {
    enviarResposta("PONG");
    return;
  }

  if (cmd == "GET") {
    enviarResposta("BEGINCONFIG");
    enviarResposta(formatarGlobal());
    enviarResposta(formatarHH());
    enviarResposta(formatarHHSW());
    for (int i = 0; i < NUM_PADS; i++) {
      enviarResposta(formatarPad(i));
    }
    enviarResposta("ENDCONFIG");
    return;
  }

  if (cmd == "HH") {
    // Formato novo (9 tokens: "HH" + 8 parâmetros) já inclui os dois
    // limiares novos; formato antigo (7 tokens: "HH" + 6 parâmetros,
    // consoles/scripts desatualizados) continua aceito, só sem mexer
    // nos limiares (mantém o que já estava configurado).
    int tokens = contarTokens(linha);
    if (tokens >= 7) {
      hhAtivo = tokenInt(linha, 1) != 0;
      hhTipo = tokenInt(linha, 2);
      hhAberto = tokenInt(linha, 3);
      hhFechado = tokenInt(linha, 4);
      hhFiltro = tokenInt(linha, 5);
      hhInvertido = tokenInt(linha, 6) != 0;

      if (tokens >= 9) {
        hhLimiarMeioAberto = tokenInt(linha, 7);
        hhLimiarFechado = tokenInt(linha, 8);
        corrigirOrdemLimiaresHiHat();
      }

      enviarResposta(formatarHH());
      enviarResposta("OK,HH");
    } else {
      enviarResposta("ERROR,HH");
    }
    return;
  }

  if (cmd == "HHSW") {
    if (contarTokens(linha) >= 4) {
      hhswAtivo = tokenInt(linha, 1) != 0;
      hhswInvertido = tokenInt(linha, 2) != 0;
      hhswForcarFechado = tokenInt(linha, 3) != 0;

      enviarResposta(formatarHHSW());
      enviarResposta("OK,HHSW");
    } else {
      enviarResposta("ERROR,HHSW");
    }
    return;
  }

  if (cmd == "HHSTATUS") {
    enviarResposta(formatarHHStatus());
    return;
  }

  if (cmd == "PAD") {
    // "PAD" + 7 parâmetros (idx nota threshold velmax lockout ativo
    // curva) = 8 tokens.
    if (contarTokens(linha) >= 8) {
      int idx = tokenInt(linha, 1);

      if (idx < 0 || idx >= NUM_PADS) {
        enviarResposta("ERROR,PAD");
        return;
      }

      padNota[idx] = tokenInt(linha, 2);
      padThreshold[idx] = tokenInt(linha, 3);
      padVelMax[idx] = tokenInt(linha, 4);
      padLockout[idx] = tokenInt(linha, 5);
      padAtivo[idx] = tokenInt(linha, 6) != 0;
      padCurva[idx] = constrain((int)tokenInt(linha, 7), 0, 2);

      enviarResposta(formatarPad(idx));
      enviarResposta("OK,PAD");
    } else {
      enviarResposta("ERROR,PAD");
    }
    return;
  }

  if (cmd == "GLOBAL") {
    // "GLOBAL" + 4 parâmetros (peak_time min_velocity note_duration
    // midi_channel) = 5 tokens.
    if (contarTokens(linha) >= 5) {
      globalPeakTime = max(1, (int)tokenInt(linha, 1));
      globalMinVelocity = constrain((int)tokenInt(linha, 2), 0, 127);
      globalNoteDuration = max(1, (int)tokenInt(linha, 3));
      globalMidiChannel = constrain((int)tokenInt(linha, 4), 1, 16);

      enviarResposta(formatarGlobal());
      enviarResposta("OK,GLOBAL");
    } else {
      enviarResposta("ERROR,GLOBAL");
    }
    return;
  }

  if (cmd == "SAVE") {
    salvarConfigHiHat();
    salvarConfigPads();
    salvarConfigGlobal();
    enviarResposta("OK,SAVE");
    return;
  }

  if (cmd == "RESET") {
    resetarConfigHiHat();
    resetarConfigPads();
    resetarConfigGlobal();
    enviarResposta("OK,RESET");
    return;
  }

  // CAL / CALOFF: ainda não implementados neste firmware (streaming
  // ao vivo do valor bruto do piezo pra calibração -- diferente do
  // PAD, que só grava/lê os parâmetros já definidos) -- responde um
  // erro explícito em vez de ficar quieto, pra ficar claro no console
  // que esse comando não teve efeito nenhum.
  enviarResposta("ERROR,UNKNOWN");
}

void lerComandosSerial() {
  while (Serial.available()) {
    char c = Serial.read();

    if (c == '\n') {
      processarComando(bufferComando);
      bufferComando = "";
    } else if (c != '\r' && bufferComando.length() < 120) {
      bufferComando += c;
    }
  }
}


// ================================================================
// LOOP
// ================================================================

void loop() {

  lerComandosSerial();

  atualizarHiHat();

  verificarPedalChick();

  processarPads();

  delayMicroseconds(200);
}

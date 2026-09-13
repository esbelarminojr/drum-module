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
 * Comandos aceitos agora (o resto -- PAD/GLOBAL/CAL -- ainda não
 * está implementado neste firmware; ver aviso no README):
 *   PING                                        -> PONG
 *   GET                                         -> BEGINCONFIG, HH,..., HHSW,..., ENDCONFIG
 *   HH <ativo> <tipo> <aberto> <fechado> <filtro> <invertido>
 *   HHSW <ativo> <invertido> <forcar_fechado>
 *   HHSTATUS                                    -> HHSTATUS,<raw>,<posicao>,<switch>,<tipo>,<potRaw>
 *   SAVE                                        -> grava HH/HHSW na memória interna (sobrevive a reiniciar)
 *   RESET                                       -> volta HH/HHSW pros valores padrão
 *
 * Além disso, o firmware agora detecta sozinho o "pedal chick": toda
 * vez que o hi-hat passa de aberto pra fechado (pelo sensor contínuo
 * ou pelo microswitch, o que estiver ativo), dispara a nota 44 (ver
 * verificarPedalChick()), sem precisar bater no pad com a baqueta.
 * ================================================================
 */

#include <Preferences.h>

#include "config_bateria.h"


// ================================================================
// VARIÁVEIS DOS PADS (igual antes)
// ================================================================

bool detectandoBatida[NUM_PADS] = {false};
unsigned long tempoInicioLeitura[NUM_PADS] = {0};
unsigned long tempoUltimaBatida[NUM_PADS] = {0};
int picoValor[NUM_PADS] = {0};


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

  prefs.end();
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

  salvarConfigHiHat();
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

  Serial.write(0xB0);  // Control Change - canal 1
  Serial.write(4);     // CC4 - Foot Controller
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

  return hhPosicaoAtual >= HIHAT_LIMIAR_FECHADO;
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
// CURVA DE VELOCIDADE (igual antes)
// ================================================================

int aplicarCurva(int padIndex, int velocidade) {

  if (!CURVA_ATIVA_POR_PINO[padIndex]) {
    return velocidade;
  }

  int nota = NOTAS_MIDI[padIndex];

  if (nota == 36) {  // BUMBO
    if (velocidade < 20) {
      return 0;
    }
    return velocidade;
  }

  if (nota == 38) {  // CAIXA
    if (velocidade < 30) {
      return velocidade * 0.9;
    }
    if (velocidade < 80) {
      return velocidade * 1.1;
    }
    return velocidade;
  }

  return velocidade;
}


// ================================================================
// ENVIA NOTA MIDI (igual antes)
// ================================================================

void enviarMIDI(int nota, int velocidade) {

  if (velocidade < VELOCIDADE_MINIMA) {
    return;
  }

  velocidade = constrain(velocidade, 1, 127);

  Serial.write(0x90);
  Serial.write(nota);
  Serial.write(velocidade);

  delay(NOTE_DURATION_MS);

  Serial.write(0x80);
  Serial.write(nota);
  Serial.write(0);
}


// ================================================================
// PROCESSA OS PADS (igual antes, só a escolha da nota do Hi-Hat mudou)
// ================================================================

void processarPads() {

  for (int i = 0; i < NUM_PADS; i++) {

    int valor = analogRead(GPIO_PADS[i]);
    int limiar = LIMIAR_POR_PINO[i];
    int bloqueioMs = BLOQUEIO_POR_PINO[i];
    int velMax = VELOCIDADE_MAXIMA_POR_PINO[i];

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

      if (millis() - tempoInicioLeitura[i] > TEMPO_ESPERA_PICO_MS) {

        detectandoBatida[i] = false;
        tempoUltimaBatida[i] = millis();

        if (picoValor[i] > limiar) {

          int velocidade = map(picoValor[i], limiar, velMax, VELOCIDADE_MINIMA, 127);
          velocidade = constrain(velocidade, VELOCIDADE_MINIMA, 127);
          velocidade = aplicarCurva(i, velocidade);

          // ------------------------------------------------
          // ESCOLHE A NOTA (Hi-Hat depende do sensor/switch ativo)
          // ------------------------------------------------

          int nota = NOTAS_MIDI[i];

          if (i == HIHAT_PAD_INDEX) {
            nota = hihatEstaFechado() ? NOTA_HIHAT_FECHADO : NOTA_HIHAT_ABERTO;
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
  s += (hhInvertido ? "1" : "0");
  return s;
}

String formatarHHSW() {
  String s = "HHSW,";
  s += (hhswAtivo ? "1" : "0"); s += ",";
  s += (hhswInvertido ? "1" : "0"); s += ",";
  s += (hhswForcarFechado ? "1" : "0");
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
    enviarResposta(formatarHH());
    enviarResposta(formatarHHSW());
    enviarResposta("ENDCONFIG");
    return;
  }

  if (cmd == "HH") {
    if (contarTokens(linha) >= 7) {
      hhAtivo = tokenInt(linha, 1) != 0;
      hhTipo = tokenInt(linha, 2);
      hhAberto = tokenInt(linha, 3);
      hhFechado = tokenInt(linha, 4);
      hhFiltro = tokenInt(linha, 5);
      hhInvertido = tokenInt(linha, 6) != 0;

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

  if (cmd == "SAVE") {
    salvarConfigHiHat();
    enviarResposta("OK,SAVE");
    return;
  }

  if (cmd == "RESET") {
    resetarConfigHiHat();
    enviarResposta("OK,RESET");
    return;
  }

  // PAD / GLOBAL / CAL / CALOFF: ainda não implementados neste
  // firmware (só a parte do Hi-Hat foi feita até agora) -- responde
  // um erro explícito em vez de ficar quieto, pra ficar claro no
  // console que esse comando não teve efeito nenhum.
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

/*
 * ================================================================
 * CONFIG_BATERIA.H
 * BATERIA ELETRÔNICA - ESP32 + DRUMGIZMO
 * ================================================================
 */

#ifndef CONFIG_BATERIA_H
#define CONFIG_BATERIA_H


// ================================================================
// QUANTIDADE DE PADS
// ================================================================

#define NUM_PADS 8


// ================================================================
// PINOS DOS PADS
// ================================================================
//
// 13 = Pad 1
// 14 = Pad 2
// 27 = Pad 3
// 26 = Pad 4
// 25 = Pad 5
// 33 = Pad 6
// 32 = Pad 7
// 35 = Hi-Hat
//
// GPIO 34 é reservado para o potenciômetro do Hi-Hat.
//

const int GPIO_PADS[NUM_PADS] = {
  13,    // PAD 0
  14,    // PAD 1
  27,    // PAD 2
  26,    // PAD 3
  25,    // PAD 4
  33,    // PAD 5
  32,    // PAD 6
  35     // PAD 7 - HI-HAT
};


// ================================================================
// NOMES DOS PADS
// ================================================================

const char* NOME_PADS[NUM_PADS] = {
  "Bumbo",
  "Caixa",
  "Tom 1",
  "Tom 2",
  "Tom 3",
  "Crash",
  "Ride",
  "Hi-Hat"
};


// ================================================================
// NOTAS MIDI
// ================================================================
//
// Essas notas serão usadas pelo DrumGizmo.
//
// 36 = Kick
// 38 = Snare
// 48 = Tom 1
// 45 = Tom 2
// 43 = Tom 3
// 49 = Crash
// 51 = Ride
// 42 = Hi-Hat
//

const int NOTAS_MIDI[NUM_PADS] = {
  36,    // Bumbo
  38,    // Caixa
  48,    // Tom 1
  45,    // Tom 2
  43,    // Tom 3
  49,    // Crash
  51,    // Ride
  42     // Hi-Hat
};


// ================================================================
// LIMIAR DE DISPARO
// ================================================================
//
// O piezo precisa passar desse valor para gerar uma batida.
//
// Aumente = menos sensível
// Diminua = mais sensível
//

const int LIMIAR_POR_PINO[NUM_PADS] = {
  80,     // Bumbo
  250,    // Caixa
  250,    // Tom 1
  250,    // Tom 2
  250,    // Tom 3
  280,    // Crash
  250,    // Ride
  160     // Hi-Hat
};


// ================================================================
// VALOR DO PIEZO QUE REPRESENTA VELOCIDADE 127
// ================================================================
//
// Quando o pico do piezo atingir esse valor,
// a velocidade MIDI será aproximadamente 127.
//

const int VELOCIDADE_MAXIMA_POR_PINO[NUM_PADS] = {
  200,     // Bumbo
  1000,    // Caixa
  800,     // Tom 1
  800,     // Tom 2
  800,     // Tom 3
  1000,    // Crash
  1000,    // Ride
  1500     // Hi-Hat
};


// ================================================================
// TEMPO DE BLOQUEIO DE CADA PAD
// ================================================================
//
// Evita disparos múltiplos causados pela vibração do piezo.
//

const int BLOQUEIO_POR_PINO[NUM_PADS] = {
  50,     // Bumbo
  20,     // Caixa
  20,     // Tom 1
  20,     // Tom 2
  20,     // Tom 3
  30,     // Crash
  30,     // Ride
  40      // Hi-Hat
};


// ================================================================
// CURVA DE VELOCIDADE
// ================================================================

const bool CURVA_ATIVA_POR_PINO[NUM_PADS] = {
  true,    // Bumbo
  true,    // Caixa
  false,   // Tom 1
  false,   // Tom 2
  false,   // Tom 3
  false,   // Crash
  false,   // Ride
  false    // Hi-Hat
};


// ================================================================
// HI-HAT
// ================================================================
//
// GPIO 35 = piezo do Hi-Hat (pad em si, dispara a nota)
// GPIO 34 = potenciômetro de posição (modo POTENCIÔMETRO)
// GPIO 4  = microswitch (modo/opção SWITCH, independente do resto)
//

const int GPIO_HIHAT_POSICAO = 34;
const int GPIO_HIHAT_SWITCH = 4;


// ================================================================
// VALORES PADRÃO DO HI-HAT (usados só na primeira vez que o ESP32
// liga, antes de qualquer configuração ser salva -- depois disso,
// tudo isso é configurável pelo console web e fica guardado na
// memória interna do ESP32, sobrevivendo a desligar/religar).
// ================================================================
//
// aberto/fechado: valor bruto do potenciômetro (ADC, 0-4095) que
// corresponde ao chimbal totalmente aberto / totalmente fechado. Pode
// configurar ao contrário (aberto > fechado) sem problema, ou usar
// "Invertido" pra trocar o sentido sem precisar remedir os valores.
//

#define HH_TIPO_OFF           0
#define HH_TIPO_POTENCIOMETRO 1
#define HH_TIPO_HALL          2   // reservado -- sensor ainda não implementado
#define HH_TIPO_IR            3   // reservado -- sensor ainda não implementado

const bool HH_ATIVO_PADRAO = true;
const int HH_TIPO_PADRAO = HH_TIPO_POTENCIOMETRO;
const int HH_ABERTO_PADRAO = 0;
const int HH_FECHADO_PADRAO = 1900;
const int HH_FILTRO_PADRAO = 8;      // quantidade de leituras usadas na média
const bool HH_INVERTIDO_PADRAO = false;

const bool HHSW_ATIVO_PADRAO = false;
const bool HHSW_INVERTIDO_PADRAO = false;
const bool HHSW_FORCAR_FECHADO_PADRAO = false;


// ================================================================
// LIMIAR DE DECISÃO ABERTO/FECHADO (posição normalizada 0-127)
// ================================================================
//
// Quando o pad do Hi-Hat é atingido (e o microswitch não está
// ativo), comparamos a posição normalizada (0=aberto, 127=fechado,
// já considerando "invertido") com esse limiar pra escolher a nota:
//
//   posição >= HIHAT_LIMIAR_FECHADO  -> nota de fechado
//   posição <  HIHAT_LIMIAR_FECHADO  -> nota de aberto
//

const int HIHAT_LIMIAR_FECHADO = 64;


// ================================================================
// NOTAS DO HI-HAT
// ================================================================

const int NOTA_HIHAT_FECHADO = 42;
const int NOTA_HIHAT_ABERTO = 46;


// ================================================================
// PEDAL CHICK (nota 44 -- "Hi-Hat Pedal" no padrão General MIDI)
// ================================================================
//
// Disparado automaticamente pelo firmware sempre que o chimbal passa
// de ABERTO pra FECHADO (seja pelo potenciômetro ou pelo microswitch,
// o que estiver ativo), sem precisar bater o pad com a baqueta. Não
// depende do kit ter ou não uma diferenciação "meio aberto" -- é só a
// transição aberto->fechado.
//
// Isso significa que, tocando com o sensor contínuo (sem microswitch),
// qualquer vaivém do pé perto do limiar (HIHAT_LIMIAR_FECHADO) pode
// gerar chicks extras -- é o comportamento pedido. Se isso incomodar
// na prática, a saída mais simples é ativar o microswitch (HHSW) e
// usá-lo como fonte da decisão fechado/aberto: aí só dispara quando
// o pedal realmente bate no fim de curso.
//

const int NOTA_HIHAT_PEDAL = 44;
const int VELOCIDADE_PEDAL_CHICK = 100;          // 1-127, fixa (não há piezo pra medir força do pisão)
const unsigned long PEDAL_CHICK_BLOQUEIO_MS = 80; // ignora transições novas por esse tempo (bounce do switch/sensor)


// ================================================================
// ÍNDICE DO PAD DO HI-HAT
// ================================================================
//
// Posição do Hi-Hat dentro dos arrays GPIO_PADS / NOME_PADS /
// NOTAS_MIDI (contando a partir de 0). Olhando o array acima,
// "Hi-Hat" é o 8º elemento -> índice 7.
//
// Se um dia mudar a ordem dos pads, só precisa atualizar esse número.
//

#define HIHAT_PAD_INDEX 7


// ================================================================
// VELOCIDADE MÍNIMA
// ================================================================

const int VELOCIDADE_MINIMA = 5;


// ================================================================
// TEMPO PARA ENCONTRAR O PICO DO PIEZO
// ================================================================

const int TEMPO_ESPERA_PICO_MS = 30;


// ================================================================
// DURAÇÃO DA NOTA MIDI
// ================================================================

const int NOTE_DURATION_MS = 3;


// ================================================================
// SERIAL
// ================================================================

const int SERIAL_BAUDRATE = 115200;


// ================================================================
// DEBUG
// ================================================================
//
// IMPORTANTE:
// Não ative DEBUG quando estiver usando o ESP32
// conectado à ponte MIDI.
//
// Serial é usada para transmitir MIDI.
//

// #define DEBUG_ENABLED


#endif
/*
 * IR probe — reference firmware for `sweep --sensors ir --ir-port <port>`
 *
 * Reports two different things over one serial link, because they need two
 * different sensors and only one of them is the interesting one:
 *
 *   coded IR   A 38 kHz demodulating receiver (TSOP38238 / VS1838B). Sees
 *              remote-control protocols. Deaf to un-modulated light by design.
 *
 *   IR flood   A bare photodiode or phototransistor (BPW34, or any 940 nm
 *              receiver) on an analogue pin. Sees *total* infrared, including
 *              the steady un-modulated emission of a night-vision illuminator,
 *              which is the actual hidden-camera tell.
 *
 * A probe with only the TSOP will never find a camera. Fit both.
 *
 * Wiring (ESP32):
 *   TSOP38238  OUT -> GPIO 15, VCC -> 3V3, GND -> GND
 *   BPW34      anode -> GPIO 34 (ADC1_CH6) with a 1M resistor to GND,
 *              cathode -> 3V3   (photoconductive mode, reverse biased)
 *
 * Optional: a visible-light filter (an offcut of exposed photographic film, or
 * a purpose-made IR-pass filter) over the photodiode cuts room lighting and
 * makes the flood threshold far more stable.
 *
 * Libraries: IRremote (https://github.com/Arduino-IRremote/Arduino-IRremote)
 *
 * Output is the newline protocol in sweep/sensors/serialbridge.py:
 *   {"t":"ir","proto":"NEC","addr":"0x4","cmd":"0x8","bits":32,"repeat":false}
 *   {"t":"irlevel","adc":812,"mv":655}
 */

#include <Arduino.h>
#include <IRremote.hpp>

static const uint8_t IR_RECV_PIN  = 15;
static const uint8_t IR_LEVEL_PIN = 34;

// The flood level is reported once a second. sweep does the thresholding and
// the "has it been sustained?" timing — the probe stays dumb on purpose, so
// the detection logic can be changed without reflashing anything.
static const unsigned long LEVEL_INTERVAL_MS = 1000;

// A rolling minimum tracks the dark level, so the probe reports an *excess*
// that is not thrown off by whatever ambient IR the room already had.
static uint16_t darkLevel = 4095;
static unsigned long lastLevel = 0;

void setup() {
  Serial.begin(115200);
  while (!Serial) { delay(10); }

  IrReceiver.begin(IR_RECV_PIN, DISABLE_LED_FEEDBACK);
  analogReadResolution(12);
  analogSetPinAttenuation(IR_LEVEL_PIN, ADC_11db);

  Serial.println(F("# sweep IR probe ready"));
}

void loop() {
  // ---- coded IR ----------------------------------------------------
  if (IrReceiver.decode()) {
    if (IrReceiver.decodedIRData.protocol != UNKNOWN) {
      Serial.print(F("{\"t\":\"ir\",\"proto\":\""));
      Serial.print(getProtocolString(IrReceiver.decodedIRData.protocol));
      Serial.print(F("\",\"addr\":\"0x"));
      Serial.print(IrReceiver.decodedIRData.address, HEX);
      Serial.print(F("\",\"cmd\":\"0x"));
      Serial.print(IrReceiver.decodedIRData.command, HEX);
      Serial.print(F("\",\"bits\":"));
      Serial.print(IrReceiver.decodedIRData.numberOfBits);
      Serial.print(F(",\"repeat\":"));
      Serial.print((IrReceiver.decodedIRData.flags & IRDATA_FLAGS_IS_REPEAT)
                   ? F("true") : F("false"));
      Serial.println(F("}"));
    } else {
      // Unknown protocol still matters: something transmitted infrared.
      Serial.print(F("{\"t\":\"ir\",\"proto\":\"unknown\",\"raw\":\""));
      Serial.print(IrReceiver.decodedIRData.decodedRawData, HEX);
      Serial.println(F("\"}"));
    }
    IrReceiver.resume();
  }

  // ---- flood level -------------------------------------------------
  unsigned long now = millis();
  if (now - lastLevel >= LEVEL_INTERVAL_MS) {
    lastLevel = now;

    // Median of five reads: the ADC on an ESP32 is noisy and a single sample
    // will produce spurious spikes that look like a flood.
    uint16_t s[5];
    for (uint8_t i = 0; i < 5; i++) { s[i] = analogRead(IR_LEVEL_PIN); delay(2); }
    for (uint8_t i = 0; i < 4; i++)
      for (uint8_t j = i + 1; j < 5; j++)
        if (s[j] < s[i]) { uint16_t t = s[i]; s[i] = s[j]; s[j] = t; }
    uint16_t level = s[2];

    if (level < darkLevel) darkLevel = level;
    else darkLevel += (darkLevel < 4095) ? 1 : 0;   // let the floor drift up slowly

    // sweep's default threshold assumes a 10-bit scale, so report both the
    // native 12-bit reading and a scaled one.
    Serial.print(F("{\"t\":\"irlevel\",\"adc\":"));
    Serial.print(level >> 2);
    Serial.print(F(",\"adc12\":"));
    Serial.print(level);
    Serial.print(F(",\"dark\":"));
    Serial.print(darkLevel >> 2);
    Serial.print(F(",\"mv\":"));
    Serial.print((uint32_t)level * 3300 / 4095);
    Serial.println(F("}"));
  }
}

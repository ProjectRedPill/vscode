/*
 * Broadband RF power probe — reference firmware for
 * `sweep --sensors rf-power --rf-port <port>`
 *
 * This is the RFHunter idea (ESP32 + AD8317) reduced to a sensor: the board
 * does no thresholding, no display and no alerting, it just streams calibrated
 * dBm. All the judgement lives in sweep, where it can be changed without a
 * soldering iron.
 *
 * Why a log detector rather than an SDR: it responds to *any* carrier from
 * ~1 MHz to ~10 GHz with no tuning, no demodulation and no protocol support.
 * It cannot tell you what a device is — but it also cannot be defeated by
 * encryption, by a protocol nobody has written a decoder for, or by an
 * analogue video transmitter with no digital identity at all. It is the sensor
 * of last resort, and it is the one that finds things the others miss.
 *
 * Wiring (ESP32):
 *   AD8317 VOUT -> GPIO 34 (ADC1_CH6)
 *   AD8317 VCC  -> 5V (module regulates), GND -> GND
 *   Antenna     -> a 2-5 cm whip. Deliberately short: you want near-field
 *                  sensitivity for sweeping surfaces, not distant reception.
 *
 * Transfer function: the AD8317 is an inverting log amp, roughly
 *     dBm = (V_INTERCEPT - V_out) / SLOPE_V_PER_DB
 * with a nominal slope of -22 mV/dB. SLOPE and INTERCEPT below should be
 * calibrated against a known source (a phone transmitting at a known distance
 * is enough for relative work). Uncalibrated, the *changes* are still correct
 * even when the absolute numbers are not — and changes are what locating uses.
 *
 * Output (see sweep/sensors/serialbridge.py):
 *   {"t":"rf","dbm":-42.5,"adc":1873,"detector":"AD8317"}
 */

#include <Arduino.h>

static const uint8_t RF_PIN = 34;

// Calibration. Replace with your own board's values if you have a reference.
static const float SLOPE_V_PER_DB = -0.022f;   // AD8317 nominal
static const float V_INTERCEPT    = 0.500f;    // volts at 0 dBm
static const float DB_OFFSET      = -40.0f;    // whip + cable loss

static const unsigned long SAMPLE_INTERVAL_MS = 200;   // 5 Hz — fast enough to sweep by hand

static unsigned long lastSample = 0;

void setup() {
  Serial.begin(115200);
  while (!Serial) { delay(10); }
  analogReadResolution(12);
  analogSetPinAttenuation(RF_PIN, ADC_11db);
  Serial.println(F("# sweep RF power probe ready"));
}

void loop() {
  unsigned long now = millis();
  if (now - lastSample < SAMPLE_INTERVAL_MS) return;
  lastSample = now;

  // Average 16 samples. The detector output is a slowly-varying envelope, so
  // averaging costs nothing in responsiveness and removes most of the ADC noise.
  uint32_t total = 0;
  for (uint8_t i = 0; i < 16; i++) total += analogRead(RF_PIN);
  uint16_t adc = total / 16;

  float volts = (float)adc * 3.3f / 4095.0f;
  float dbm = (V_INTERCEPT - volts) / -SLOPE_V_PER_DB + DB_OFFSET;

  Serial.print(F("{\"t\":\"rf\",\"dbm\":"));
  Serial.print(dbm, 1);
  Serial.print(F(",\"adc\":"));
  Serial.print(adc);
  Serial.print(F(",\"mv\":"));
  Serial.print((uint32_t)(volts * 1000.0f));
  Serial.println(F(",\"detector\":\"AD8317\"}"));
}

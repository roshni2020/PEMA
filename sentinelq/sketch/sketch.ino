// SentinelQ - STM32U585 side (real-time brain)
//
// Exposes hard, bounded actuator functions to Python over the Bridge and streams
// sensor readings back once a second. Nothing here trusts the LLM: every function
// clamps its inputs, and the over-temperature interlock runs locally even if Linux dies.

#include "Arduino_RouterBridge.h"
#include "Arduino_LED_Matrix.h"
// The UNO Q Zephyr core has no Servo library yet; the "servo" action drives a PWM pin
// (0-180 deg mapped to 0-255 duty). Use an external servo driver or a dimmable LED there.

// ---- wiring (change to match your build) ------------------------------------
const int PIN_BUZZER = 8;      // passive buzzer, or piezo
const int PIN_RELAY  = 7;      // relay module IN
const int PIN_SERVO  = 9;      // hobby servo signal
const int PIN_DOOR   = 2;      // reed switch / button to GND (INPUT_PULLUP)
const int PIN_TEMP   = A0;     // TMP36 or similar analog temp sensor (optional)
const int PIN_LDR    = A1;     // light-dependent resistor divider (optional)
const int PIN_LED_R  = 3, PIN_LED_G = 5, PIN_LED_B = 6; // RGB LED (optional)

const float TEMP_HARD_LIMIT_C = 55.0;   // local interlock, independent of Linux

// Set to true once the sensor is physically wired. An unconnected analog pin floats and would
// otherwise report nonsense (about -15 C / random light), so absent sensors are not reported.
const bool HAS_TEMP_SENSOR  = false;    // TMP36 on A0
const bool HAS_LIGHT_SENSOR = false;    // LDR divider on A1
const bool HAS_DOOR_SENSOR  = false;    // reed switch / button on D2

Arduino_LED_Matrix matrix;

unsigned long lastReport = 0;
bool relayState = false;

// ---- 8x13 icons for the UNO Q LED matrix (row-major bits, 1 = on) ----------
// Kept tiny on purpose; replace with your own art.
uint8_t ICON_IDLE[8][13] = {
  {0,0,0,0,0,0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0,0,0,0,0,0},
  {0,0,0,0,0,0,1,0,0,0,0,0,0},{0,0,0,0,0,0,1,0,0,0,0,0,0},{0,0,0,0,0,0,0,0,0,0,0,0,0},
  {0,0,0,0,0,0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0,0,0,0,0,0}};
uint8_t ICON_EYE[8][13] = {
  {0,0,0,0,0,0,0,0,0,0,0,0,0},{0,0,0,0,1,1,1,1,1,0,0,0,0},{0,0,0,1,0,0,0,0,0,1,0,0,0},
  {0,0,1,0,0,1,1,1,0,0,1,0,0},{0,0,1,0,0,1,1,1,0,0,1,0,0},{0,0,0,1,0,0,0,0,0,1,0,0,0},
  {0,0,0,0,1,1,1,1,1,0,0,0,0},{0,0,0,0,0,0,0,0,0,0,0,0,0}};
uint8_t ICON_WARNING[8][13] = {
  {0,0,0,0,0,0,1,0,0,0,0,0,0},{0,0,0,0,0,0,1,0,0,0,0,0,0},{0,0,0,0,0,0,1,0,0,0,0,0,0},
  {0,0,0,0,0,0,1,0,0,0,0,0,0},{0,0,0,0,0,0,1,0,0,0,0,0,0},{0,0,0,0,0,0,0,0,0,0,0,0,0},
  {0,0,0,0,0,0,1,0,0,0,0,0,0},{0,0,0,0,0,0,0,0,0,0,0,0,0}};
uint8_t ICON_CHECK[8][13] = {
  {0,0,0,0,0,0,0,0,0,0,0,0,0},{0,0,0,0,0,0,0,0,0,0,0,1,0},{0,0,0,0,0,0,0,0,0,0,1,0,0},
  {0,0,0,0,0,0,0,0,0,1,0,0,0},{0,0,1,0,0,0,0,0,1,0,0,0,0},{0,0,0,1,0,0,0,1,0,0,0,0,0},
  {0,0,0,0,1,0,1,0,0,0,0,0,0},{0,0,0,0,0,1,0,0,0,0,0,0,0}};
uint8_t ICON_HEART[8][13] = {
  {0,0,0,0,0,0,0,0,0,0,0,0,0},{0,0,0,1,1,0,0,0,1,1,0,0,0},{0,0,1,1,1,1,0,1,1,1,1,0,0},
  {0,0,1,1,1,1,1,1,1,1,1,0,0},{0,0,0,1,1,1,1,1,1,1,0,0,0},{0,0,0,0,1,1,1,1,1,0,0,0,0},
  {0,0,0,0,0,1,1,1,0,0,0,0,0},{0,0,0,0,0,0,1,0,0,0,0,0,0}};
uint8_t ICON_OFF[8][13] = {{0}};

uint8_t ICON_FULL[8][13];          // all-on frame used as a visual "beep"
String currentIcon = "idle";

// ---- functions Python can call --------------------------------------------
void set_matrix(String icon) {
  currentIcon = icon;
  if      (icon == "eye")     matrix.renderBitmap(ICON_EYE, 8, 13);
  else if (icon == "warning") matrix.renderBitmap(ICON_WARNING, 8, 13);
  else if (icon == "check")   matrix.renderBitmap(ICON_CHECK, 8, 13);
  else if (icon == "heart")   matrix.renderBitmap(ICON_HEART, 8, 13);
  else if (icon == "off")     matrix.renderBitmap(ICON_OFF, 8, 13);
  else                        matrix.renderBitmap(ICON_IDLE, 8, 13);
}

void buzz(String pattern) {
  if (pattern == "off") { noTone(PIN_BUZZER); digitalWrite(LED_BUILTIN, LOW); return; }
  int reps = (pattern == "double") ? 2 : 1;
  int len  = (pattern == "long") ? 700 : 150;
  for (int i = 0; i < reps; i++) {
    // audible on a passive buzzer wired D8 -> GND, and always visible: matrix + LED flash
    tone(PIN_BUZZER, 2000, len);
    digitalWrite(LED_BUILTIN, HIGH);
    matrix.renderBitmap(ICON_FULL, 8, 13);
    delay(len);
    digitalWrite(LED_BUILTIN, LOW);
    set_matrix(currentIcon);
    delay(100);
  }
}

void set_relay(int on) {
  relayState = (on != 0);
  digitalWrite(PIN_RELAY, relayState ? HIGH : LOW);
}

void set_servo(int angle) {
  angle = constrain(angle, 0, 180);
  analogWrite(PIN_SERVO, map(angle, 0, 180, 0, 255));
}

void set_led(int r, int g, int b) {
  analogWrite(PIN_LED_R, constrain(r, 0, 255));
  analogWrite(PIN_LED_G, constrain(g, 0, 255));
  analogWrite(PIN_LED_B, constrain(b, 0, 255));
}

// Readback for verification: Python calls this after every action and compares.
String get_state() {
  return "relay=" + String(relayState ? 1 : 0) + ";icon=" + currentIcon + ";uptime_s=" + String(millis() / 1000);
}

// ---- sensors --------------------------------------------------------------
float readTempC() {
  // TMP36: 10 mV/degC with 500 mV offset, 3.3 V reference, 12-bit ADC on STM32U5
  int raw = analogRead(PIN_TEMP);
  float volts = raw * (3.3f / 4095.0f);
  return (volts - 0.5f) * 100.0f;
}

void setup() {
  pinMode(PIN_BUZZER, OUTPUT);
  pinMode(PIN_RELAY, OUTPUT);
  pinMode(PIN_DOOR, INPUT_PULLUP);
  pinMode(PIN_LED_R, OUTPUT); pinMode(PIN_LED_G, OUTPUT); pinMode(PIN_LED_B, OUTPUT);
  analogReadResolution(12);
  pinMode(PIN_SERVO, OUTPUT);
  pinMode(LED_BUILTIN, OUTPUT);
  for (int r = 0; r < 8; r++) for (int c = 0; c < 13; c++) ICON_FULL[r][c] = 1;
  matrix.begin();
  set_matrix("idle");

  Bridge.begin();
  Bridge.provide("set_matrix", set_matrix);
  Bridge.provide("buzz", buzz);
  Bridge.provide("set_relay", set_relay);
  Bridge.provide("set_servo", set_servo);
  Bridge.provide("set_led", set_led);
  Bridge.provide("get_state", get_state);
}

void loop() {
  unsigned long now = millis();
  if (now - lastReport >= 1000) {
    lastReport = now;
    String payload = "relay=" + String(relayState ? 1 : 0) + ";uptime_s=" + String(now / 1000);

    if (HAS_TEMP_SENSOR) {
      float t = readTempC();
      // Local interlock: never depends on Linux or the model.
      if (t >= TEMP_HARD_LIMIT_C && relayState) {
        set_relay(0);
        set_matrix("warning");
      }
      payload += ";temp_c=" + String(t, 1);
    }
    if (HAS_LIGHT_SENSOR) payload += ";light=" + String(analogRead(PIN_LDR));
    if (HAS_DOOR_SENSOR)  payload += ";door=" + String(digitalRead(PIN_DOOR) == LOW ? 1 : 0);

    Bridge.notify("on_sensors", payload);
  }
}

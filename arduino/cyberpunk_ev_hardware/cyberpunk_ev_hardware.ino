/*
 * cyberpunk_ev_hardware.ino — EVSE-01 physical node (Arduino Uno)
 *
 *  OUT  DIST:<cm>\n      every REPORT_MS (999.0 = no echo / sensor cut → treated as tamper)
 *  IN   CMD:ALARM_ON\n   LED on + buzzer pulsing     (State 3: compromise)
 *       CMD:LED_ON\n     LED solid, buzzer off        (State 2: enclosure tamper)
 *       CMD:ALARM_OFF\n  LED off, buzzer off          (State 0/1)
 *
 * Wiring: HC-SR04 TRIG=D10 ECHO=D11 · active buzzer D6 · red LED D12 (via 220Ω)
 * The Pi is the state authority; this sketch only measures and obeys.
 */

// ── Pins ─────────────────────────────────────────────────────────────────────
const uint8_t TRIG_PIN   = 10;
const uint8_t ECHO_PIN   = 11;   // PB3 / PCINT3 → pin-change interrupt, no pulseIn()
const uint8_t BUZZER_PIN = 6;
const uint8_t LED_PIN    = 12;

// ── Calibration knobs ────────────────────────────────────────────────────────
const unsigned long SAMPLE_MS       = 60;     // HC-SR04 needs ≥60 ms between pings
const unsigned long REPORT_MS       = 100;
const unsigned long ECHO_TIMEOUT_US = 30000;  // ~5 m round trip
const unsigned long BEEP_MS         = 250;    // buzzer on/off half-period
const float         US_PER_CM       = 58.0;   // tweak if your readings are off by a few %
const float         MAX_CM          = 400.0;
const float         NO_ECHO_CM      = 999.0;

// ── Echo capture (ISR) ───────────────────────────────────────────────────────
volatile unsigned long echoRise  = 0;
volatile unsigned long echoWidth = 0;
volatile bool          echoDone  = false;

ISR(PCINT0_vect) {
  unsigned long now = micros();
  if (PINB & _BV(PB3)) {
    echoRise = now;
  } else {
    echoWidth = now - echoRise;
    echoDone = true;
  }
}

// ── State ────────────────────────────────────────────────────────────────────
enum Mode : uint8_t { MODE_OFF, MODE_LED, MODE_ALARM };
Mode mode = MODE_OFF;

float samples[3] = {NO_ECHO_CM, NO_ECHO_CM, NO_ECHO_CM};
uint8_t sampleIdx = 0;

bool waitingEcho = false;
unsigned long trigAtUs = 0, lastTrigMs = 0, lastReportMs = 0, lastBeepMs = 0;
bool beepOn = false;

char cmdBuf[24];
uint8_t cmdLen = 0;

void pushSample(float cm) {
  samples[sampleIdx] = cm;
  sampleIdx = (sampleIdx + 1) % 3;
}

float median3() {
  float a = samples[0], b = samples[1], c = samples[2];
  if (a > b) { float t = a; a = b; b = t; }
  if (b > c) { b = c; }
  return a > b ? a : b;
}

// uint8_t, not Mode: the IDE's auto-generated prototypes land above the enum
void applyMode(uint8_t m) {
  mode = (Mode)m;
  digitalWrite(LED_PIN, m == MODE_OFF ? LOW : HIGH);
  beepOn = (m == MODE_ALARM);
  digitalWrite(BUZZER_PIN, beepOn ? HIGH : LOW);
  lastBeepMs = millis();
}

void handleCommand(const char *cmd) {
  if      (strcmp(cmd, "CMD:ALARM_ON")  == 0) applyMode(MODE_ALARM);
  else if (strcmp(cmd, "CMD:LED_ON")    == 0) applyMode(MODE_LED);
  else if (strcmp(cmd, "CMD:ALARM_OFF") == 0) applyMode(MODE_OFF);
}

unsigned long lastRxMs = 0;

void flushCommand() {
  cmdBuf[cmdLen] = '\0';
  if (cmdLen) handleCommand(cmdBuf);
  cmdLen = 0;
}

void readCommands() {
  while (Serial.available()) {
    char ch = Serial.read();
    lastRxMs = millis();
    if (ch == '\n' || ch == '\r') {          // accept LF, CR or CRLF
      flushCommand();
    } else if (cmdLen < sizeof(cmdBuf) - 1) {
      cmdBuf[cmdLen++] = ch;
    } else {
      cmdLen = 0;  // overflow → drop garbage line
    }
  }
  // Serial Monitor set to "No line ending": run the command after a short pause
  if (cmdLen && millis() - lastRxMs > 50) flushCommand();
}

// Power-on self-test: LED + buzzer flash twice so wiring can be checked at a glance
void selfTest() {
  for (uint8_t k = 0; k < 2; k++) {
    digitalWrite(LED_PIN, HIGH);
    digitalWrite(BUZZER_PIN, HIGH);
    delay(150);
    digitalWrite(LED_PIN, LOW);
    digitalWrite(BUZZER_PIN, LOW);
    delay(150);
  }
}

void updateUltrasonic(unsigned long nowMs) {
  if (waitingEcho) {
    if (echoDone) {
      noInterrupts();
      unsigned long w = echoWidth;
      interrupts();
      float cm = w / US_PER_CM;
      pushSample(cm > MAX_CM ? NO_ECHO_CM : cm);
      waitingEcho = false;
    } else if (micros() - trigAtUs > ECHO_TIMEOUT_US) {
      pushSample(NO_ECHO_CM);  // no echo or wire cut → fail-secure
      waitingEcho = false;
    }
  }
  if (!waitingEcho && nowMs - lastTrigMs >= SAMPLE_MS) {
    lastTrigMs = nowMs;
    echoDone = false;
    digitalWrite(TRIG_PIN, HIGH);
    delayMicroseconds(10);  // the only delay: 10 µs trigger pulse
    digitalWrite(TRIG_PIN, LOW);
    trigAtUs = micros();
    waitingEcho = true;
  }
}

void updateBuzzer(unsigned long nowMs) {
  if (mode != MODE_ALARM || nowMs - lastBeepMs < BEEP_MS) return;
  lastBeepMs = nowMs;
  beepOn = !beepOn;
  digitalWrite(BUZZER_PIN, beepOn ? HIGH : LOW);
}

void setup() {
  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(LED_PIN, OUTPUT);
  selfTest();
  applyMode(MODE_OFF);

  PCICR  |= _BV(PCIE0);    // enable pin-change interrupts for PORTB (D8–D13)
  PCMSK0 |= _BV(PCINT3);   // ...on D11 only

  Serial.begin(9600);
}

void loop() {
  unsigned long nowMs = millis();
  readCommands();
  updateUltrasonic(nowMs);
  updateBuzzer(nowMs);

  if (nowMs - lastReportMs >= REPORT_MS) {
    lastReportMs = nowMs;
    Serial.print("DIST:");
    Serial.println(median3(), 1);
  }
}

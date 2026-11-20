#include <Arduino.h>
#include <Preferences.h>
#include <SPI.h>
#include <TFT_eSPI.h>
#include <XPT2046_Touchscreen.h>

namespace {
constexpr int kTouchCs = 33;
constexpr int kTouchIrq = 36;
constexpr int kTouchSck = 25;
constexpr int kTouchMiso = 39;
constexpr int kTouchMosi = 32;

constexpr int kTouchRotation = 3;
constexpr int kArtX = 8;
constexpr int kArtY = 20;
constexpr int kArtSize = 200;
constexpr uint16_t kBackground = 0x0863;
constexpr uint16_t kSurface = 0x1947;
constexpr uint16_t kAccent = 0x36CE;
constexpr uint16_t kDiscord = 0x7B9F;
constexpr uint16_t kText = 0xE73C;
constexpr int kCalibrationX[] = {25, 295, 295, 25};
constexpr int kCalibrationY[] = {25, 25, 215, 215};

TFT_eSPI display;
SPIClass touchSpi(VSPI);
XPT2046_Touchscreen touch(kTouchCs, kTouchIrq);
Preferences preferences;

struct TouchCalibration {
  int16_t x[4];
  int16_t y[4];
};
TouchCalibration calibration;
bool calibrating = false;
int calibrationPoint = 0;
bool wasTouched = false;

enum class Icon { Previous, PlayPause, Next, Microphone };

struct Button {
  int x;
  int y;
  int radius;
  uint16_t color;
  Icon icon;
  const char* command;
};

const Button buttons[] = {
    {264, 32, 24, kSurface, Icon::Previous, "PREV"},
    {264, 88, 24, kAccent, Icon::PlayPause, "PLAY"},
    {264, 144, 24, kSurface, Icon::Next, "NEXT"},
    {264, 200, 24, kDiscord, Icon::Microphone, "MUTE"},
};

void drawButton(const Button& button, bool pressed = false) {
  const int x = button.x;
  const int y = button.y;
  display.fillCircle(x, y, button.radius, pressed ? TFT_WHITE : button.color);
  const uint16_t ink = pressed || button.icon == Icon::PlayPause
                           ? kBackground : kText;
  switch (button.icon) {
    case Icon::Previous:
      display.fillRect(x - 12, y - 10, 3, 21, ink);
      display.fillTriangle(x - 7, y, x + 10, y - 10, x + 10, y + 10, ink);
      break;
    case Icon::Next:
      display.fillTriangle(x + 7, y, x - 10, y - 10, x - 10, y + 10, ink);
      display.fillRect(x + 10, y - 10, 3, 21, ink);
      break;
    case Icon::PlayPause:
      display.fillTriangle(x - 12, y - 10, x - 12, y + 10, x + 1, y, ink);
      display.fillRoundRect(x + 5, y - 9, 3, 19, 1, ink);
      display.fillRoundRect(x + 11, y - 9, 3, 19, 1, ink);
      break;
    case Icon::Microphone:
      display.fillRoundRect(x - 5, y - 13, 11, 19, 5, ink);
      display.drawSmoothArc(x, y + 1, 11, 9, 270, 90, ink,
                            pressed ? TFT_WHITE : button.color, true);
      display.fillRect(x - 1, y + 11, 3, 5, ink);
      display.fillRoundRect(x - 7, y + 15, 15, 3, 1, ink);
      break;
  }
}

void clearArtwork() {
  display.fillRect(kArtX, kArtY, kArtSize, kArtSize, kSurface);
  display.setTextColor(kText, kSurface);
  display.setTextDatum(MC_DATUM);
  display.drawString("No artwork", kArtX + kArtSize / 2,
                     kArtY + kArtSize / 2);
}

void drawInterface() {
  display.fillScreen(kBackground);
  display.setFreeFont(&FreeSans9pt7b);
  display.setTextColor(kText, kBackground);
  display.setTextDatum(MC_DATUM);
  display.drawString("Spotify", 108, 230);
  display.drawString("Discord", 264, 230);
  display.drawFastVLine(224, 20, 200, kSurface);
  clearArtwork();
  for (const Button& button : buttons) {
    drawButton(button);
  }
}

bool mapTouch(const TouchCalibration& data, int rawX, int rawY, float& x, float& y) {
  const float cx = (data.x[0] + data.x[1] + data.x[2] + data.x[3]) / 4.0f;
  const float cy = (data.y[0] + data.y[1] + data.y[2] + data.y[3]) / 4.0f;
  const float hx = (data.x[1] + data.x[2] - data.x[0] - data.x[3]) / 4.0f;
  const float hy = (data.y[1] + data.y[2] - data.y[0] - data.y[3]) / 4.0f;
  const float vx = (data.x[2] + data.x[3] - data.x[0] - data.x[1]) / 4.0f;
  const float vy = (data.y[2] + data.y[3] - data.y[0] - data.y[1]) / 4.0f;
  const float determinant = hx * vy - hy * vx;
  if (fabsf(determinant) < 100000.0f) {
    return false;
  }
  const float dx = rawX - cx;
  const float dy = rawY - cy;
  x = 160.0f + 135.0f * (dx * vy - dy * vx) / determinant;
  y = 120.0f + 95.0f * (dy * hx - dx * hy) / determinant;
  return true;
}

bool validCalibration(const TouchCalibration& data) {
  for (int i = 0; i < 4; ++i) {
    float x, y;
    if (!mapTouch(data, data.x[i], data.y[i], x, y) ||
        fabsf(x - kCalibrationX[i]) > 35 ||
        fabsf(y - kCalibrationY[i]) > 35) {
      return false;
    }
  }
  return true;
}

void drawCalibrationTarget() {
  display.fillScreen(TFT_BLACK);
  display.setTextColor(TFT_WHITE, TFT_BLACK);
  display.setTextDatum(MC_DATUM);
  display.drawString("Tap the crosshair", 160, 110, 2);
  display.drawString("Release after each tap", 160, 130, 1);
  int x = kCalibrationX[calibrationPoint];
  int y = kCalibrationY[calibrationPoint];
  display.drawCircle(x, y, 10, TFT_YELLOW);
  display.drawLine(x - 15, y, x + 15, y, TFT_YELLOW);
  display.drawLine(x, y - 15, x, y + 15, TFT_YELLOW);
}

void startCalibration() {
  calibrating = true;
  calibrationPoint = 0;
  drawCalibrationTarget();
  Serial.println("CALIBRATING");
}

void calibrationTouch(const TS_Point& point) {
  calibration.x[calibrationPoint] = point.x;
  calibration.y[calibrationPoint] = point.y;
  ++calibrationPoint;
  if (calibrationPoint < 4) {
    drawCalibrationTarget();
    return;
  }
  if (!validCalibration(calibration)) {
    Serial.println("ERROR: invalid calibration; retrying");
    calibrationPoint = 0;
    drawCalibrationTarget();
    return;
  }
  if (preferences.putBytes("touch", &calibration, sizeof(calibration)) != sizeof(calibration)) {
    Serial.println("ERROR: calibration could not be saved");
  }
  calibrating = false;
  drawInterface();
  Serial.println("CAL_DONE");
}

void receiveArtwork() {
  uint8_t bytes[kArtSize * 2];
  uint16_t pixels[kArtSize];
  Serial.println("READY");
  for (int y = 0; y < kArtSize; ++y) {
    if (Serial.readBytes(bytes, sizeof(bytes)) != sizeof(bytes)) {
      clearArtwork();
      Serial.println("ERROR: artwork transfer timed out");
      return;
    }
    for (int x = 0; x < kArtSize; ++x) {
      pixels[x] = (static_cast<uint16_t>(bytes[x * 2]) << 8) | bytes[x * 2 + 1];
    }
    display.pushImage(kArtX, kArtY + y, kArtSize, 1, pixels);
    Serial.println("ROW");
  }
}
}  // namespace

void setup() {
  Serial.begin(115200);
  pinMode(TFT_BL, OUTPUT);
  digitalWrite(TFT_BL, HIGH);
  display.init();
  display.setRotation(1);
  display.setSwapBytes(true);
  Serial.setTimeout(2000);
  touchSpi.begin(kTouchSck, kTouchMiso, kTouchMosi, kTouchCs);
  if (!touch.begin(touchSpi)) {
    display.fillScreen(TFT_BLACK);
    display.setTextColor(TFT_WHITE, TFT_BLACK);
    display.setTextDatum(MC_DATUM);
    display.drawString("Touch init failed", 160, 230, 1);
    Serial.println("ERROR: touch init failed");
    return;
  }
  touch.setRotation(kTouchRotation);
  if (!preferences.begin("touch-cal", false)) {
    Serial.println("ERROR: calibration storage unavailable");
  }
  if (preferences.getBytesLength("touch") != sizeof(calibration) ||
      preferences.getBytes("touch", &calibration, sizeof(calibration)) != sizeof(calibration) ||
      !validCalibration(calibration)) {
    startCalibration();
  } else {
    drawInterface();
  }
  Serial.println("STARTED");
}

void loop() {
  if (Serial.available()) {
    String command = Serial.readStringUntil('\n');
    command.trim();
    if (command == "PING") {
      Serial.println(calibrating ? "STATUS_CAL" : "STATUS_READY");
    } else if (command == "CAL") {
      startCalibration();
    } else if (calibrating) {
      Serial.println("ERROR: complete touch calibration first");
    } else if (command == "ART") {
      receiveArtwork();
    } else if (command == "CLEAR") {
      clearArtwork();
      Serial.println("CLEARED");
    } else {
      Serial.println("ERROR: unknown command");
    }
  }
  const bool isTouched = touch.touched();
  if (isTouched && !wasTouched) {
    TS_Point point = touch.getPoint();
    if (calibrating) {
      calibrationTouch(point);
    } else {
      float x, y;
      if (mapTouch(calibration, point.x, point.y, x, y)) {
        for (const Button& button : buttons) {
          const float dx = x - button.x;
          const float dy = y - button.y;
          if (dx * dx + dy * dy <= button.radius * button.radius) {
            Serial.println(button.command);
            drawButton(button, true);
            break;
          }
        }
      }
    }
  }
  if (!isTouched && wasTouched && !calibrating) {
    for (const Button& button : buttons) {
      drawButton(button);
    }
  }
  wasTouched = isTouched;
  delay(30);
}

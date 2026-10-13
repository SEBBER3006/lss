#include <Arduino.h>
#include <microcontroller.h>

#define PWMPIN 9
#define MOTIONPIN A2
#define LUXPIN A0

Microcontroller controller(MOTIONPIN, PWMPIN, LUXPIN);
bool manualOverride = false;
bool autoMode = false;
bool powerOn = false;
unsigned long lastSensorPrint = 0;
unsigned long lastAutoUpdate = 0;
bool lastMotionState = false;
bool motionDetectedSinceLastUpdate = false;
unsigned long lastMotionTime = 0;
const unsigned long AUTO_OFF_DELAY = 10000; // 10 seconds without motion (adjustable timer)
BrightnessLevel currentManualLevel = _MEDIUM; // Track manual brightness level

void setup() {
  Serial.begin(9600);
  delay(2000); // Wait for serial to initialize
  Serial.println("INIT OK");
  Serial.println("System ready - waiting for POWER ON");
  controller.resetCounter();
}

void loop() {
  static char cmdBuf[64];
  static uint8_t idx = 0;
  
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (idx > 0) {
        cmdBuf[idx] = '\0';
        idx = 0;
        
        if (strncmp(cmdBuf, "ENABLE AUTO", 10) == 0) {
          manualOverride = false;
          autoMode = true;
          Serial.println("AUTO MODE ENABLED");
        }
        else if (strncmp(cmdBuf, "POWER ON", 8) == 0) {
          powerOn = true;
          manualOverride = false;
          autoMode = true;
          lastMotionTime = millis(); // Initialize timer on power-on
          Serial.println("POWER ON - AUTO MODE");
        }
        else if (strncmp(cmdBuf, "POWER OFF", 9) == 0) {
          powerOn = false;
          manualOverride = false;
          autoMode = false;
          controller.manuelTurnOn(BrightnessLevel(0));
          Serial.println("POWER OFF OK");
        }
        else if (strncmp(cmdBuf, "SET INTENSITY LOW", 17) == 0) {
          if (!powerOn) {
            Serial.println("ERROR: System not powered on");
          } else {
            manualOverride = true;
            autoMode = false;
            currentManualLevel = _LOW; // Store manual level
            lastMotionTime = millis(); // Reset timer when setting manual mode
            controller.manuelTurnOn(_LOW);
            Serial.println("INTENSITY LOW OK");
          }
        }
        else if (strncmp(cmdBuf, "SET INTENSITY MEDIUM", 20) == 0) {
          if (!powerOn) {
            Serial.println("ERROR: System not powered on");
          } else {
            manualOverride = true;
            autoMode = false;
            currentManualLevel = _MEDIUM; // Store manual level
            lastMotionTime = millis(); // Reset timer when setting manual mode
            controller.manuelTurnOn(_MEDIUM);
            Serial.println("INTENSITY MEDIUM OK");
          }
        }
        else if (strncmp(cmdBuf, "SET INTENSITY HIGH", 18) == 0) {
          if (!powerOn) {
            Serial.println("ERROR: System not powered on");
          } else {
            manualOverride = true;
            autoMode = false;
            currentManualLevel = _HIGH; // Store manual level
            lastMotionTime = millis(); // Reset timer when setting manual mode
            controller.manuelTurnOn(_HIGH);
            Serial.println("INTENSITY HIGH OK");
          }
        }
        else if (strncmp(cmdBuf, "AUTO", 4) == 0) {
          if (!powerOn) {
            Serial.println("ERROR: System not powered on");
          } else {
            manualOverride = false;
            autoMode = true;
            lastMotionTime = millis(); // Reset timer when switching to auto mode
            Serial.println("AUTO MODE ENABLED");
          }
        }
        else {
          Serial.print("UNKNOWN: ");
          Serial.println(cmdBuf);
        }
      }
    }
    else if (idx < sizeof(cmdBuf) - 1) {
      cmdBuf[idx++] = c;
    }
  }
  
  // Only check motion and control LED when system is powered on
  if (powerOn) {
    // Check motion detection for ALL modes (auto and manual)
    // Motion detection resets the timer regardless of mode
    bool currentMotionState = (digitalRead(MOTIONPIN) == HIGH);
    if (currentMotionState && !lastMotionState) {
      Serial.println("Motion detected");
      Serial.println("Motion detected");  // Print twice for visibility
      motionDetectedSinceLastUpdate = true;
      lastMotionTime = millis(); // Reset inactivity timer
    }
    lastMotionState = currentMotionState;
  }
  
  // Auto LED off after 10 seconds of no motion (ALL MODES)
  // System stays powered on, only LED turns off
  if (powerOn && (millis() - lastMotionTime >= AUTO_OFF_DELAY)) {
    controller.manuelTurnOn(BrightnessLevel(0));
    // Only print message once when LED turns off (avoid spam)
    static bool ledWasOn = true;
    if (ledWasOn) {
      Serial.println("AUTO LED OFF - No motion detected for 10 seconds");
      ledWasOn = false;
    }
    // Don't reset timer - LED stays off until motion detected
  } else {
    static bool ledWasOn = false;
    ledWasOn = true;
  }
  
  // Auto mode brightness recalibration
  if (!manualOverride && autoMode) {
    // Auto mode recalibrates LED brightness
    // Faster recalibration (2s) when motion detected, otherwise 5s
    // Only update if there has been recent motion (within AUTO_OFF_DELAY)
    bool hasRecentMotion = (millis() - lastMotionTime < AUTO_OFF_DELAY);
    
    unsigned long recalInterval = motionDetectedSinceLastUpdate ? 2000 : 5000;
    if (hasRecentMotion && (millis() - lastAutoUpdate >= recalInterval)) {
      int luxLevel = analogRead(LUXPIN);
      
      // Map lux level inversely to PWM (brighter room = dimmer LED)
      // Lux range: 375 (min) to 975 (max)
      int targetPWM = map(luxLevel, 375, 975, 255, 0);
      targetPWM = constrain(targetPWM, 0, 255);
      
      // Recalibrate LED brightness
      controller.manuelTurnOn(BrightnessLevel(targetPWM));
      
      // Report light sensor and LED brightness once per recalibration
      Serial.print("Light sensor: ");
      Serial.print(luxLevel);
      Serial.print(" lux | LED brightness: ");
      Serial.print((targetPWM * 100) / 255);
      Serial.println("%");
      
      // Only show recalibration message if motion was detected since last update
      if (motionDetectedSinceLastUpdate) {
        Serial.println("LED brightness recalibrated");
        motionDetectedSinceLastUpdate = false;
      }
      
      lastAutoUpdate = millis();
    }
  }
  // Manual mode - restore brightness level when motion detected
  else if (manualOverride && (millis() - lastMotionTime < AUTO_OFF_DELAY)) {
    // Re-apply manual brightness to ensure LED turns back on at set level
    controller.manuelTurnOn(currentManualLevel);
  }
  
  delay(50);
}

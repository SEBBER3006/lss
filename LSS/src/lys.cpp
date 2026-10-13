#include <Arduino.h>
#include "lys.h"

// Constructor
Light::Light(int pwmPin) : pin(pwmPin) {
  pinMode(pin, OUTPUT);        // Set pin as output
  analogWrite(pin, 0);         // Start with light off
}

// Turn light on and start timing
void Light::turnOn() {
  if (!status) {
    lastUpdate = millis();      // Start new timing
    status = true;
  }
  analogWrite(pin, currentBrightness);  // Apply brightness
}

// Turn light off and accumulate time
void Light::turnOff() {
  if (status) {
    timeOn += (millis() - lastUpdate) / 1000;
    status = false;
  }
  analogWrite(pin, 0);  // Turn off PWM
}

// Sets Light to Low, Medium or High PWM brightness (no gradual transition)
void Light::manuelTurnOn(BrightnessLevel level) {
  currentBrightness = level;  // Set brightness directly (no smooth transition)
  if (!status) {
    lastUpdate = millis();    // Start timing when turned on
    status = true;
  }
  analogWrite(pin, currentBrightness);  // Apply brightness immediately
}

// Gradually adjust brightness toward target
void Light::regulateBrightness(int targetPWM) {
  
  // Define step size for smooth transition
  const int step = 5;
  
  // Adjust brightness in steps
  if (currentBrightness < targetPWM) {
    currentBrightness = min(currentBrightness + step, targetPWM);
  } else if (currentBrightness > targetPWM) {
    currentBrightness = max(currentBrightness - step, targetPWM);
  }

  if (currentBrightness <= step && targetPWM == 0) {
    currentBrightness = 0;
  }

  // Apply new brightness or turn off
  if (currentBrightness == 0) {
    turnOff();
  } else {
    turnOn();
  }
  
}

// Returns total time light has been on (in seconds)
int Light::getTimeOn() const {
  return timeOn;
}

// Update time while light is on
void Light::updateTimeOn() {
  if (status) {
    unsigned long currentTime = millis();
    if (currentTime - lastUpdate >= 1000) {
      timeOn += (currentTime - lastUpdate) / 1000;
      lastUpdate = currentTime;
    }
  }
}

// Returns whether the light is currently on
bool Light::isOn() const {
  return status;
}

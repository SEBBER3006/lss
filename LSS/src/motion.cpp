#include <Arduino.h>
#include "motion.h"

MotionSensor::MotionSensor(int sensorPin) : pin(sensorPin) {
    pinMode(pin, INPUT);
}

bool MotionSensor::isMotionDetected() const{
  return digitalRead(pin) == HIGH;
  }

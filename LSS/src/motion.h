#ifndef MOTION_H
#define MOTION_H

#include <Arduino.h>

class MotionSensor {
private:
int pin = A2;

public:
MotionSensor(int sensorPin);

bool isMotionDetected() const;
  
};

#endif

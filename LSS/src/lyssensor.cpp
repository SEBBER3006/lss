#include <Arduino.h>
#include "lyssensor.h" 


Lyssensor::Lyssensor(int analogPin) : pin(analogPin) {
	pinMode(pin, INPUT);
}


int Lyssensor::getLuxLevel() const {
	return analogRead(pin);
}

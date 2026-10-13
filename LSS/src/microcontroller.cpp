#include <Arduino.h>
#include "microcontroller.h"
#include "lyssensor.h"
#include "motion.h"
#include "lys.h"


 Microcontroller::Microcontroller(int motionPin, int PWMpin , int luxPin): 
 motionsensor(motionPin), 
 light(PWMpin),
 lyssensor(luxPin), 
 sensitivity(500),
 counter(5000)
 {}

 //main loop

 void Microcontroller::loop() {
  light.turnOff();
  //check for bevægelse
  if(motionsensor.isMotionDetected()){
    resetCounter();
    light.turnOn();
  }
    //If there is movement then regulate light
    if(getCounterRemaining() > 0) {
      int rawLux = lyssensor.getLuxLevel();
      int lux = constrain(rawLux, 0 , 1023);
      int targetPwm = constrain(map(lux, 0, sensitivity, 255, 0), 0, 255);
      light.regulateBrightness(targetPwm);
      
      Serial.print("Lux: ");
      Serial.print(lux);
      Serial.print("  TargetPWM: ");
      Serial.println(targetPwm);
    } else {
      light.turnOff();
    }

    light.updateTimeOn();
 }

//reset timer for getCounterRemaining 
void Microcontroller::resetCounter(){
    counterEnd = millis() + counter;
  }
  
unsigned long Microcontroller::getCounterRemaining() {
    unsigned long lastMotiontime = millis();
    if (lastMotiontime >= counterEnd) {
      return 0;
    }
    return counterEnd - lastMotiontime;
}

//Get time of light on (seconds)
int Microcontroller::getCounterOn() {
  return light.getTimeOn(); 
}
//Check if light is on 
bool Microcontroller::isLightOn() {
  return light.isOn();
}

//Manual brightness control - set light to fixed brightness level
void Microcontroller::manuelTurnOn(BrightnessLevel level) {
  light.manuelTurnOn(level);
}

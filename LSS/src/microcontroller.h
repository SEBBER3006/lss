#ifndef MICROCONTROLLER_H
#define MICROCONTROLLER_H
#include "lyssensor.h"
#include "motion.h"
#include "lys.h"

class Microcontroller {

    private:
    MotionSensor motionsensor;
    Light light;
    Lyssensor lyssensor;
    

    int lastLux = 0;
    bool lastMotionTime = false;
    int sensitivity;
    unsigned long counter;
    unsigned long counterEnd;

    public:
    Microcontroller(int motionPin, int PWMpin, int luxPin);

    void setup();

    void update();

    void loop();

    void setLuxSensitivity(int sensitivity);
    
    void resetCounter();
    //The time left before light turns off 
    unsigned long getCounterRemaining();
    //Store the amount of full running time 
    int getCounterOn();
    //Store last lux reading for use 
    //int LastLux();
    
    //Check if motion is on 
    bool isMotionDetected();
    //Check if light is on 
    bool isLightOn();
    // Manual brightness control (LOW, MEDIUM, HIGH)
    void manuelTurnOn(BrightnessLevel level);

};

#endif

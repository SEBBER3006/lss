#ifndef LYS_H
#define LYS_H

#include <Arduino.h>

// Brightness levels for manual control
enum BrightnessLevel {
    _LOW = 85,       // 33% brightness (255 / 3)
    _MEDIUM = 170,   // 67% brightness (255 * 2 / 3)
    _HIGH = 255      // 100% brightness
};

class Light {
public:
    // Constructor
    Light(int pwmPin);

    // Control functions
    void turnOn();
    void turnOff();
    void regulateBrightness(int targetPWM);
    void updateTimeOn();
    void manuelTurnOn(BrightnessLevel level);  // Set fixed brightness (low/medium/high)

    // Status queries
    bool isOn() const;
    int getTimeOn() const;

private:
    bool status = false;              // Light is currently on/off
    int currentBrightness = 0;        // Current PWM brightness
    int timeOn = 0;                   // Duration light has been on (seconds)
    int pin = 9;                      // Output pin (set via constructor)
    unsigned long lastUpdate = 0;     // Timestamp of last update
};

#endif

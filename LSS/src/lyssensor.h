#ifndef LYSSENSOR_H
#define LYSSENSOR_H

#include <Arduino.h> 

class Lyssensor {
 private: 
    int pin = A0; 

 public: 
     Lyssensor(int analogPin); 
     int getLuxLevel() const; 
};
#endif

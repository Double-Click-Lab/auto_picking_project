import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Mirobot_robot.Mirobot_UART import WLKATA_UART

import time
import serial
import re

#MS4220步进电机
#MS4220 stepper motor

class MS4220_UART(WLKATA_UART):
    def __init__(self,block_flag=False,message_flag=False):
        super().__init__(block_flag,message_flag)
        self.num = None
        # self.string = None
        self.address = None
        self.pSerial = None
        self.MS4220_state_all = {"state": " ","WPos": 0, "S0": 0, "S1": 0, "S2": 0}

    #Save serial object and MS4220 address (RS458 address range: 0-255)
    # 保存串口对象和MS4220地址（RS458地址范围：0-255）

    def init(self, p, adr):
        self.pSerial = p
        self.address = adr

    def restart(self):
        self.sendMsg("o100")
        time.sleep(0.5)

    def homing(self):
        self.sendMsg("$h")
        time.sleep(0.5)
        self.delay_idle() 

    def move_position(self,position, num,speed=50):
        if speed > 100:
            speed = 100
        elif speed < 0:
            speed = 0

        if position == 0:
            self.sendMsg("G90 G01 E" + str(num)+" F"+str(speed))
        elif position == 1:
            self.sendMsg("G91 G01 E" + str(num)+" F"+str(speed))
        else:
            self.sendMsg("G91 G01 E" + str(num)+" F"+str(speed))
        self.delay_idle() 

    #Step motor speed setting, value range: 0-100
    # 步进电机速度设置，范围：0-100

    def move_speed(self, num):
        self.num = num
        if self.num > 100:
            self.num == 100
        elif self.num < -100:
            self.num == -100
        self.num = "G6 F" + str(self.num)
        self.sendMsg(self.num)


    #Get full status of the robot
    # 获取步进电机全部状态

    def getStatus(self):
        self.line = " "
        self.pSerial.flushInput()
        self.pSerial.flushOutput()
        self.sendMsg("?")
        if self.pSerial.in_waiting > 0:
            self.line = self.read_message()
            if self.line[0] == "<" and self.line[-1] == ">":
                self.data = self.__parse_response(self.line)
            else:
                self.data = -1
        else:
            return "error"

        time.sleep(0.5)
        return self.data

    #Regular expression for the full status of the robot
    # 步进电机全部状态的正则表达式

    def __parse_response(self, line):
        self.pattern = r'<(\w+),WPos:([\d.-]+),S0:([\d.-]+),S1:([\d.-]+),S2:([\d.-]+)>'
        match = re.match(self.pattern, line)
        if match:

            self.MS4220_state_all = {"state": " ","WPos": 0, "S0": 0, "S1": 0, "S2": 0}

            self.MS4220_state_all["state"] = match.group(1)
            self.MS4220_state_all["WPos"] = match.group(2)
            self.MS4220_state_all["S0"] = int( match.group(3))
            self.MS4220_state_all["S1"] = int( match.group(4))
            self.MS4220_state_all["S2"] = int( match.group(5))
   
            return self.MS4220_state_all
        else:
            return "parse error"

    #Get robot state
    # 获取步进电机状态

    def getState(self):
        self.getStatus()
        return self.MS4220_state_all["state"]
    #获取步进电机当前绝对位置
    def get_position(self):
        self.getStatus()
        return self.MS4220_state_all["WPos"]
    #获取步进电机传感器的值
    def get_sensor(self,name):
        self.getStatus()
        if name == "S0":
            return self.MS4220_state_all["S0"]
        elif name == "S1":
            return self.MS4220_state_all["S1"]
        elif name == "S2":
            return self.MS4220_state_all["S2"]
        else :
            return self.MS4220_state_all["S0"]



    def delay_idle(self):
        if self._block_flag:
            while True:
                falg1=self.getState()
                time.sleep(0.1)
                falg2=self.getState()
                time.sleep(0.1)
                falg3=self.getState()
                time.sleep(0.1)
                if falg1==falg2==falg3=="Idle":
                    break


if __name__ == "__main__":
    ms4220 = MS4220_UART(True,True)
    ms4220.init(serial.Serial('COM5', 38400,timeout=1), 10)
    # ms4220.message_print(True)
    # ms4220.move_speed(100)
    # ms4220.homing()
    # ms4220.move_position(0,5000,100),
    print(ms4220.getStatus())

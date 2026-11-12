import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from Mirobot_robot.Mirobot_UART import Mirobot_UART
import serial
import time

#E4串口控制类
#E4 serial port control class

class MT4_UART(Mirobot_UART):
    
    def __init__(self,block_flag=False,message_flag=False):
        super().__init__(block_flag,message_flag)
    # E4复位指令
    # E4 homing command
    def homing(self, mode=8):
        self.mode = mode
        if self.mode == 0:
            self.sendMsg("o105=0")
        elif self.mode == 1:
            self.sendMsg("o105=1")
        elif self.mode == 2:
            self.sendMsg("o105=2")
        elif self.mode == 3:
            self.sendMsg("o105=3")
        elif self.mode == 4:
            self.sendMsg("o105=4")
        elif self.mode == 7:
            self.sendMsg("o105=7")
        elif self.mode == 8:
            self.sendMsg("o105=8")
        elif self.mode == 9:
            self.sendMsg("o105=9")
        elif self.mode == 10:
            self.sendMsg("o105=10")
        else:
            self.sendMsg("$h")
        self.delay_idle()
    # E4初始位置
    # E4 initial position
    def zero(self):
        self.sendMsg("M21 G90 G00 X0 Y0 Z0 A0 ")
        self.delay_idle()
    # E4笛卡尔坐标控制
    # E4 Cartesian coordinate control
    def writecoordinate(self, motion, position,  x=None, y=None, z=None, a=None):
        self.motion = motion
        self.position = position
        
        if self.motion == 0:
            self.motion = "G00"
        elif self.motion == 1:
            self.motion = "G01"
        elif self.motion == 2:
            self.motion = "G05"
        else:
            self.motion = "G00"

        if self.position == 0:
            self.position = "G90"
        elif self.position == 1:
            self.position = "G91"
        else:
            self.position = "G90"

        self.coordinate = ""
        if x is not None:
            self.coordinate += f"X{x}"
        if y is not None:
            self.coordinate += f"Y{y}"
        if z is not None:
            self.coordinate += f"Z{z}"
        if a is not None:
            self.coordinate += f"A{a}"

        self.cmd = f"M20{self.position}{self.motion}{self.coordinate}"
        self.sendMsg(self.cmd)
        self.delay_idle()
    # E4角度设置
    # E4 angle setting
    def writeangle(self, position,axle1=None, axle2=None, axle3=None, axle4=None):
        self.position = position

        if self.position == 0:
            self.position = "G90"
        elif self.position == 1:
            self.position = "G91"
        else:
            self.position = "G90"

        self.angle = ""
        if axle1 is not None:
            self.angle += f"X{axle1}"
        if axle2 is not None:
            self.angle += f"Y{axle2}"
        if axle3 is not None:
            self.angle += f"Z{axle3}"
        if axle4 is not None:
            self.angle += f"A{axle4}"

        # 组合完整指令
        self.cmd = f"M21{self.position}G00{self.angle}"
        self.sendMsg(self.cmd)
        self.delay_idle()

    # E4机械臂角度获取
    # E4 robotic arm angle acquisition
    def getAngle(self, num):
        self.num = num
        self.getStatus()
        if num == 1:
            return self.mirobot_state_all["angle_X"]
        elif num == 2:
            return self.mirobot_state_all["angle_Y"]
        elif num == 3:
            return self.mirobot_state_all["angle_Z"]
        elif num == 4:
            return self.mirobot_state_all["angle_A"]
        elif num == 7:
            return self.mirobot_state_all["angle_D"]
        else:
            return "parameter error/参数错误"
        self.delay_idle()
    # E4机械臂坐标获取
    # E4 robotic arm coordinate acquisition 
    def getcoordinate(self, num):
        self.num = num
        self.getStatus()
        if num == 1:
            return self.mirobot_state_all["coordinate_X"]
        elif num == 2:
            return self.mirobot_state_all["coordinate_Y"]
        elif num == 3:
            return self.mirobot_state_all["coordinate_Z"]
        elif num == 4:
            return self.mirobot_state_all["coordinate_RX"]
        else:
            return "parameter error/参数错误"
        self.delay_idle()
    # E4查询版本信息
    # E4 query version information  
    def version(self):
        self.lina = ""  # 初始化为字符串 #Initialized as a string
        self.lina1 = ""  # 初始化为字符串 #Initialized as a string   

        self.pSerial.flushInput()
        self.pSerial.flushOutput()
        self.sendMsg("$V")

        timeout_cnt = 0  # 计数器，用于记录超时次数 #Counter, used to record the number of times the timeout occurs


        while True:
            if timeout_cnt >= 5:  # 如果超时次数超过5次，返回失败消息 #If the number of times the timeout occurs exceeds 5 times, return the failure message
                return "查询失败"

            self.lina = self.pSerial.readline().decode('utf-8').strip()
            self.lina1 = self.pSerial.readline().decode('utf-8').strip()

            if self.lina.startswith('EXbox') and self.lina1.startswith(
                    'E4'):  # 如果两行数据分别以 'EXbox' 和 'e4' 开头，跳出循环并返回结果 #If the two lines of data start with 'EXbox' and 'e4', jump out of the loop and return the result  
                break

            timeout_cnt += 1
            time.sleep(0.1)

        return self.lina, self.lina1

if __name__ == "__main__":
    robot = MT4_UART(block_flag=True,message_flag=True)
    robot.init(serial.Serial('COM4', 115200,timeout=1), -1)
    # robot.restart()
    # robot.homing()
    # robot.pump(1)
    # time.sleep(1)
    # robot.pump(2)
    # time.sleep(1)
    # robot.pump(0)
    # time.sleep(1)
    # robot.writeangle(0,axle1=90,axle2=0,axle3=0,axle4=0)
    # robot.writecoordinate(0,0,x=198,y=0,z=210,a=0)

    robot.writeangle(0,axle1=70)
    robot.writecoordinate(0,0,z=150)
    robot.zero()



// REN/LEN/RPWM/LPWM 입력을 사용하는 모터 드라이버 기준

const byte REN = 7;
const byte LEN = 8;
const byte RPWM = 5;
const byte LPWM = 6;

// 실제 방향이 반대라면 true로 변경
const bool REVERSE_DIRECTION = false;

// 이동 시간 제한이 아님.
// 컴퓨터와 통신이 끊겼을 때 정지하기 위한 설정.
const unsigned long LINK_TIMEOUT_MS = 1000;

char lineBuffer[64];
byte used = 0;

bool moving = false;
unsigned long lastHeartbeat = 0;


void stopMotor() {
  analogWrite(RPWM, 0);
  analogWrite(LPWM, 0);

  digitalWrite(REN, LOW);
  digitalWrite(LEN, LOW);

  moving = false;
}


void runMotor(bool back, int speed) {
  stopMotor();

  if (REVERSE_DIRECTION) {
    back = !back;
  }

  digitalWrite(REN, HIGH);
  digitalWrite(LEN, HIGH);

  if (back) {
    analogWrite(RPWM, 0);
    analogWrite(LPWM, speed);
  } else {
    analogWrite(LPWM, 0);
    analogWrite(RPWM, speed);
  }

  moving = true;
  lastHeartbeat = millis();
}


void handleLine(char *line) {
  if (strcmp(line, "HB") == 0) {
    lastHeartbeat = millis();
    return;
  }

  char verb[12];
  char extra;
  unsigned long token;
  int speed;

  // STOP 작업번호
  if (
    sscanf(
      line,
      "%11s %lu %c",
      verb,
      &token,
      &extra
    ) == 2
    && strcmp(verb, "STOP") == 0
  ) {
    stopMotor();

    Serial.print("STOPPED ");
    Serial.println(token);

    return;
  }

  // BACK 속도 작업번호
  // FORWARD 속도 작업번호
  if (
    sscanf(
      line,
      "%11s %d %lu %c",
      verb,
      &speed,
      &token,
      &extra
    ) == 3
    && (
      strcmp(verb, "BACK") == 0
      || strcmp(verb, "FORWARD") == 0
    )
    && speed >= 1
    && speed <= 255
    && !moving
  ) {
    runMotor(
      strcmp(verb, "BACK") == 0,
      speed
    );

    Serial.print("MOVING ");
    Serial.println(token);

    return;
  }

  stopMotor();
  Serial.println("FAULT INVALID_COMMAND");
}


void setup() {
  pinMode(REN, OUTPUT);
  pinMode(LEN, OUTPUT);
  pinMode(RPWM, OUTPUT);
  pinMode(LPWM, OUTPUT);

  stopMotor();

  Serial.begin(115200);
  Serial.println("READY");
}


void loop() {
  while (Serial.available() > 0) {
    char c = Serial.read();

    if (c == '\r') {
      continue;
    }

    if (c == '\n') {
      lineBuffer[used] = '\0';

      if (used > 0) {
        handleLine(lineBuffer);
      }

      used = 0;
    } else if (used < sizeof(lineBuffer) - 1) {
      lineBuffer[used++] = c;
    } else {
      used = 0;

      stopMotor();
      Serial.println("FAULT LINE_TOO_LONG");
    }
  }

  // 정상 통신 중에는 시간에 관계없이 계속 이동.
  // 통신이 끊기면 출력 정지.
  if (
    moving
    && millis() - lastHeartbeat > LINK_TIMEOUT_MS
  ) {
    stopMotor();
    Serial.println("FAULT HEARTBEAT_TIMEOUT");
  }
}
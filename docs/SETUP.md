# TradeLab 세팅 가이드

## 환경 구조

```
로컬 (Windows, PyCharm)
  └── .env + .env.local 로드
  └── 서버 PostgreSQL에 원격 접속

서버 (Ubuntu)
  └── .env + .env.server 로드
  └── PostgreSQL localhost 접속
  └── crontab으로 자동 수집/분석
  └── Streamlit 상시 가동
```

---

## 1. 서버 (Ubuntu) 세팅

### Python 3.14

```bash
sudo add-apt-repository ppa:deadsnakes/ppa
sudo apt update
sudo apt install python3.14 python3.14-venv python3.14-dev
```

### PostgreSQL

```bash
# 설치
sudo apt install postgresql postgresql-contrib

# 시작
sudo systemctl start postgresql
sudo systemctl enable postgresql

# DB + 유저 생성
sudo -u postgres psql
```

```sql
CREATE USER tradelab WITH PASSWORD 'yourpassword';
CREATE DATABASE tradelab OWNER tradelab;
GRANT ALL PRIVILEGES ON DATABASE tradelab TO tradelab;
\q
```

### 외부 접속 허용 (로컬에서 접속하려면 필요)

```bash
# postgresql.conf 수정
sudo nano /etc/postgresql/*/main/postgresql.conf
# listen_addresses = '*' 로 변경

# pg_hba.conf 수정
sudo nano /etc/postgresql/*/main/pg_hba.conf
# 맨 아래 추가:
# host    tradelab    tradelab    0.0.0.0/0    md5

# 재시작
sudo systemctl restart postgresql
```

> 방화벽에서 5432 포트도 열어야 함

### 프로젝트 세팅

```bash
# 코드 가져오기
git clone <repo-url> ~/tradelab
cd ~/tradelab

# 가상환경
python3.14 -m venv .venv
source .venv/bin/activate

# 패키지 설치
pip install -r requirements.txt

# 환경변수 설정 (.bashrc에 추가)
echo 'export TRADELAB_ENV=server' >> ~/.bashrc
source ~/.bashrc

# .env, .env.server 에 실제 값 채우기
nano .env
nano .env.server
```

### Streamlit 실행 (백그라운드)

```bash
# 방법 1: nohup
nohup streamlit run app.py --server.port 8501 &

# 방법 2: systemd 서비스 (추천, 서버 재부팅시 자동 시작)
sudo nano /etc/systemd/system/tradelab.service
```

```ini
[Unit]
Description=TradeLab Streamlit
After=network.target postgresql.service

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/tradelab
Environment=TRADELAB_ENV=server
ExecStart=/home/ubuntu/tradelab/.venv/bin/streamlit run app.py --server.port 8501
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable tradelab
sudo systemctl start tradelab

# 상태 확인
sudo systemctl status tradelab
```

> 브라우저에서 `http://서버IP:8501` 로 접속

### crontab 등록

```bash
crontab -e
```

```
0 * * * *  cd /home/ubuntu/tradelab && TRADELAB_ENV=server .venv/bin/python scripts/collect_prices.py
5 * * * *  cd /home/ubuntu/tradelab && TRADELAB_ENV=server .venv/bin/python scripts/collect_news.py
10 * * * * cd /home/ubuntu/tradelab && TRADELAB_ENV=server .venv/bin/python scripts/collect_onchain.py
15 * * * * cd /home/ubuntu/tradelab && TRADELAB_ENV=server .venv/bin/python scripts/run_analysis.py
16 * * * * cd /home/ubuntu/tradelab && TRADELAB_ENV=server .venv/bin/python scripts/send_alerts.py
```

---

## 2. 로컬 (Windows, PyCharm) 세팅

### Python 3.14

python.org에서 3.14 설치

### PyCharm 설정

1. File > Settings > Project > Python Interpreter
2. Add Interpreter > Add Local Interpreter
3. Virtualenv > Base interpreter: Python 3.14 선택
4. Location: 프로젝트 내 `.venv`
5. OK

### 패키지 설치

PyCharm 터미널에서:

```bash
pip install -r requirements.txt
```

### 환경변수

`.env`와 `.env.local`에 실제 값 채우기:

```
# .env.local
DATABASE_URL=postgresql://tradelab:yourpassword@서버IP:5432/tradelab
```

`TRADELAB_ENV`는 설정 안 하면 기본값 `local`이라 별도 설정 필요 없음.

### 실행

```bash
streamlit run app.py
```

`http://localhost:8501` 에서 확인.

---

## 3. env 파일 관리

```
.env           → 공통 API 키 (로컬/서버에 같은 내용)
.env.local     → 로컬 DB 접속 정보
.env.server    → 서버 DB 접속 정보
```

- 전부 `.gitignore`에 포함 → git에 올라가지 않음
- 서버에 처음 배포할 때 직접 만들어야 함
- API 키 변경시 `.env`만 양쪽 다 수정

### config.py 로딩 순서

```
1) .env 로드 (공통)
2) TRADELAB_ENV 확인 (기본값: "local")
3) .env.{TRADELAB_ENV} 로드 (override)
```

로컬: `.env` → `.env.local` (DATABASE_URL 덮어씀)
서버: `.env` → `.env.server` (DATABASE_URL 덮어씀)

---

## 4. 접속 확인

### PostgreSQL 접속 테스트 (로컬에서)

```bash
psql -h 서버IP -U tradelab -d tradelab
```

### Streamlit 접속 테스트

- 로컬: `http://localhost:8501`
- 서버: `http://서버IP:8501`

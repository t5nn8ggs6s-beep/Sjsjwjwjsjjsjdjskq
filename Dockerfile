FROM python:3.12-slim

RUN apt-get update && apt-get install -y build-essential python3-dev libssl-dev && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

COPY . .
CMD ["python", "bot.py"]

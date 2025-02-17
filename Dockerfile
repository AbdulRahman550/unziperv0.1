FROM python:3.9-slim

RUN apt-get update && apt-get install -y \
    p7zip-full \
    unrar \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV API_ID="29728224"
ENV API_HASH="b3a147834fd9d39e52e48221988c3702"
ENV BOT_TOKEN="7514240817:AAGItz8eiGbzKYVHA7N5gVy6OdeKrk9nLtU"
ENV DEFAULT_PASSWORD="ee"

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "bot.py"]

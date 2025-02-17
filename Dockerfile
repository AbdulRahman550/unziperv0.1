# Use the official Python image from the Docker Hub
FROM python:3.9-slim

# Install required system packages with non-free repository
RUN apt-get update && \
    apt-get install -y \
    p7zip-full \
    unrar-free \  # Use unrar-free instead of unrar
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Environment variables (consider using docker secrets instead)
ENV API_ID="29728224" \
    API_HASH="b3a147834fd9d39e52e48221988c3702" \
    BOT_TOKEN="7514240817:AAGItz8eiGbzKYVHA7N5gVy6OdeKrk9nLtU" \
    DEFAULT_PASSWORD="Telegram MEQIQU"

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "bot.py"]

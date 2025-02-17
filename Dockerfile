FROM python:3.9-slim

# Install base dependencies
RUN apt-get update && \
    apt-get install -y \
    wget \
    p7zip-full \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install official RAR tools (supports passwords)
RUN wget https://www.rarlab.com/rar/rarlinux-x64-710.tar.gz && \
    tar -xvf rarlinux-x64-710.tar.gz && \
    cd rar && \
    cp rar unrar /usr/local/bin/ && \
    cd .. && \
    rm -rf rar rarlinux-x64-710.tar.gz

# Environment variables (use Docker secrets in production)
ENV API_ID="29728224" \
    API_HASH="b3a147834fd9d39e52e48221988c3702" \
    BOT_TOKEN="7514240817:AAGItz8eiGbzKYVHA7N5gVy6OdeKrk9nLtU" \
    DEFAULT_PASSWORD="Telegram MEQIQU"

WORKDIR /app
COPY . /app

RUN pip install --no-cache-dir -r requirements.txt

CMD ["python", "bot.py"]

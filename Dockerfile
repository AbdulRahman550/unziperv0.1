# Use the official Python image from the Docker Hub
FROM python:3.9-slim

# Set environment variables
ENV API_ID=29728224
ENV API_HASH="b3a147834fd9d39e52e48221988c3702"
ENV BOT_TOKEN="7514240817:AAGItz8eiGbzKYVHA7N5gVy6OdeKrk9nLtU"
ENV DEFAULT_PASSWORD="Telegram MEQIQU"

# Set the working directory
WORKDIR /app

# Copy the current directory contents into the container at /app
COPY . /app

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Install 7z
RUN apt-get update && apt-get install -y p7zip-full

# Install system dependencies
RUN apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Run bot.py when the container launches
CMD ["python", "bot.py"]

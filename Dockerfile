FROM python:3.11-slim

WORKDIR /app

# Install git for git+ssh dependency
RUN apt-get update && \
    apt-get install -y git openssh-client && \
    rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY web/public/ ./web/public/

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Expose API port
EXPOSE 8766

# Run API service
CMD ["python", "-m", "kilimanjaro.api.api"]

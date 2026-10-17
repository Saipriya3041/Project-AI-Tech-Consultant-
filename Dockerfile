# Use Python base image
FROM python:3.11-slim

# Install Node.js (for task2-inbox-agent)
RUN apt-get update && apt-get install -y curl \
    && curl -fsSL https://deb.nodesource.com/setup_18.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g ts-node typescript

# Set working directory
WORKDIR /app

# Copy project files
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install Node dependencies for task2-inbox-agent
WORKDIR /app/task2-inbox-agent
RUN npm install

# Go back to root
WORKDIR /app

# Expose ports (website + APIs)
EXPOSE 8010 8011 8012 8081

# Run orchestrator
CMD ["python", "run.py"]

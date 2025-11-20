#!/bin/bash
set -e

# Start Ollama server in the background
ollama serve &

# Wait until the server responds
until ollama ps > /dev/null 2>&1; do
    echo "Waiting for Ollama server..."
    sleep 2
done

# Pull the model if not already present
if ! ollama show | grep -q "llama3.1:8b"; then
    echo "Pulling llama3.1:8b model..."
    ollama pull llama3.1:8b
fi

# Keep the server running
wait

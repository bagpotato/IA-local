#!/bin/bash

# Start of AI infrastructure deployment
echo "[INFO] Starting Docker Compose services..."
sudo docker compose up -d

# Wait for the API to initialize
echo "[INFO] Waiting for Ollama API response..."
sleep 10

# Model downloads
echo "[MODEL] Pulling Qwen 2.5 Coder 1.5B..."
sudo docker exec -it ollama ollama pull qwen2.5-coder:1.5b

echo "[MODEL] Pulling Gemma-3-1B Thinking..."
sudo docker exec -it ollama ollama pull hf.co/Andycurrent/Gemma-3-1B-it-GLM-4.7-Flash-Heretic-Uncensored-Thinking_GGUF:latest

echo "[SUCCESS] Installation finished."

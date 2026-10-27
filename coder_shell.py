import os
import requests
import json

def chat_ollama(prompt):
    url = "http://localhost:11434/api/generate"
    data = {
        "model": os.environ["OLLAMA_MODEL"],
        "prompt": prompt,
        "stream": False
    }
    try:
        response = requests.post(url, json=data)
        print(response.json().get('response', 'No response'))
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    p = input("Query: ")
    chat_ollama(p)

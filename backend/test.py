import os
import requests

key = ""
with open(".env") as f:
    for line in f:
        if line.startswith("LLM_API_KEY"):
            key = line.split("=")[1].strip('"\' \r\n')

r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}")
models = r.json().get("models", [])
print([m["name"] for m in models if "gemini-1.5" in m["name"]])

import os
from google import genai

env_path = os.path.join("backend", ".env")
api_key = None
if os.path.exists(env_path):
    with open(env_path, "r") as f:
        for line in f:
            if line.startswith("GEMINI_API_KEY="):
                api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break

if not api_key:
    print("No GEMINI_API_KEY found.")
    exit(1)

client = genai.Client(api_key=api_key)

models_to_try = [
    'gemini-3.5-flash',
    'gemini-3.1-flash-lite',
    'gemini-2.5-flash',
    'gemini-2.5-pro',
    'gemini-2.5-flash-lite',
]

for m in models_to_try:
    print(f"Testing model: {m}")
    try:
        response = client.models.generate_content(
            model=m,
            contents="Say 'hello world'",
        )
        print(f"Success with {m}: {response.text}")
    except Exception as e:
        print(f"Failed {m}: {e}")

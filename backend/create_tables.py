from dotenv import load_dotenv
load_dotenv()

from app.database import engine, Base
from app import models  # noqa: F401

import traceback

def mask_url(url: str) -> str:
    if not url: return "None (using default SQLite)"
    try:
        if "@" in url:
            # Mask password
            part1, part2 = url.split("@", 1)
            scheme_user, password = part1.rsplit(":", 1)
            return f"{scheme_user}:***@{part2}"
    except Exception:
        pass
    return url

def main():
    print(f"[INFO] Attempting to connect to DB: {mask_url(str(engine.url))}")
    try:
        Base.metadata.create_all(bind=engine)
        print("✅ Tables created.")
    except Exception as e:
        print("\n" + "="*50)
        print("❌ DATABASE CONNECTION ERROR ❌")
        print("="*50)
        print(f"Error Type: {type(e).__name__}")
        print(f"Error Message: {str(e)}")
        print("="*50)
        print("Common fixes for Google Cloud:")
        print("1. If using Supabase/Neon/Render: Ensure you appended '?sslmode=require' to your DATABASE_URL.")
        print("2. If using Cloud SQL: Use the unix socket format 'postgresql+psycopg2://USER:PASS@/DBNAME?host=/cloudsql/PROJECT:REGION:INSTANCE'.")
        print("3. Check that your database provider allows connections from all IPs (0.0.0.0/0) since Cloud Run IPs are dynamic.")
        print("="*50 + "\n")
        traceback.print_exc()

if __name__ == "__main__":
    main()

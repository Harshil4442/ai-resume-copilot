import logging
from sqlalchemy import inspect, text
from .database import engine

log = logging.getLogger("ai_resume_copilot.migrations")

def run_migrations():
    """
    Safely runs database migrations by checking table columns and adding
    missing ones dynamically. Works on both SQLite and PostgreSQL.
    """
    log.info("Running database schema migrations...")
    inspector = inspect(engine)
    
    # Check columns for the 'users' table
    if not inspector.has_table("users"):
        log.info("Table 'users' does not exist yet. It will be created by metadata.")
        return
        
    columns = [col["name"] for col in inspector.get_columns("users")]
    
    with engine.begin() as connection:
        # Add 'tier'
        if "tier" not in columns:
            log.info("Adding column 'tier' to 'users' table")
            # SQLite and Postgres support basic ALTER TABLE ADD COLUMN
            connection.execute(text("ALTER TABLE users ADD COLUMN tier VARCHAR(50) DEFAULT 'free'"))
            
        # Add 'ai_credits'
        if "ai_credits" not in columns:
            log.info("Adding column 'ai_credits' to 'users' table")
            connection.execute(text("ALTER TABLE users ADD COLUMN ai_credits INTEGER DEFAULT 5 NOT NULL"))
            
        # Add 'stripe_customer_id'
        if "stripe_customer_id" not in columns:
            log.info("Adding column 'stripe_customer_id' to 'users' table")
            connection.execute(text("ALTER TABLE users ADD COLUMN stripe_customer_id VARCHAR(255) NULL"))
            
        # Add 'stripe_subscription_id'
        if "stripe_subscription_id" not in columns:
            log.info("Adding column 'stripe_subscription_id' to 'users' table")
            connection.execute(text("ALTER TABLE users ADD COLUMN stripe_subscription_id VARCHAR(255) NULL"))

        # Add 'premium_until' (expiry for time-limited premium grants)
        if "premium_until" not in columns:
            log.info("Adding column 'premium_until' to 'users' table")
            connection.execute(text("ALTER TABLE users ADD COLUMN premium_until TIMESTAMP NULL"))

    log.info("Database migrations completed successfully.")

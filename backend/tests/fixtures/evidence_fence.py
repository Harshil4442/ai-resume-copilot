"""Explicit fence installation in an already-owned native test namespace.

This starts no service and accepts no external target/credentials. The caller's
normal owned PostgreSQL fixture establishes its disposable target first. Installing
the real trigger is not a claim about production migration or role authority.
"""
from backend.app.database import Base
from backend.app.domains.career import evidence_fence_definition, evidence_write_fence
from sqlalchemy import text


def install_owned_fence(engine):
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        namespace = connection.scalar(text("SELECT current_schema()"))
        evidence_fence_definition.install(connection, namespace)
        evidence_write_fence.require_installed(connection, namespace)

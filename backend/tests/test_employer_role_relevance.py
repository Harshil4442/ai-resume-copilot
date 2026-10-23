"""Role intent cannot be replaced by descriptive skill mentions; local SQL only."""

from copy import deepcopy
from datetime import timedelta

import pytest
from backend.app import models as core
from backend.app.domains.common import payload_fingerprint, utcnow
from backend.app.domains.employer import config, models, role_aliases, schemas, service
from backend.tests.test_employer_catalogue_search import (
    TOKEN,
    _complete,
    _content,
    _posting,
    _select,
)
from backend.tests.test_employer_catalogue_search import (
    catalogue_db as catalogue_db,
)
from backend.tests.test_employer_search_new_results import indexed_search as indexed_search


@pytest.mark.parametrize(
    "role,title,rank",
    [
        ("Software Engineer", "Senior Software Developer, Compute", 2),
        ("Software Engineer", "Software Engineering, Platform", 2),
        ("Software Engineer", "Software Development Engineer II", 2),
        ("Software Development Engineer", "Senior Software Developer", 2),
        ("Software Engineer", "Software Engineer, Revenue Systems", 2),
        ("Software Engineer", "Software Engineer, Sales Platform", 2),
        ("Software Engineer", "Software Engineer, Marketing Platform", 2),
        ("Software Engineer", "Software Engineer, Customer Support Tools", 2),
        ("Software Engineer", "Software Engineer, Management Systems", 2),
        ("Software Engineer", "Software Engineer, Go-to-Market Platform", 2),
        ("Software Engineer", "Backend Developer", 1),
        ("Software Engineer", "Full-Stack Engineer", 1),
        ("Software Engineer", "Member of Technical Staff", 1),
        ("Senior Python SDE", "Senior Python Software Developer", 2),
        ("Senior Python SDE", "Junior Python Software Engineer", 0),
        ("Backend Engineer", "Frontend Developer", 0),
        ("Software Engineer", "Product Manager, Dashboard", 0),
        ("Software Engineer", "Product Manager, Software Engineering", 0),
        ("Software Engineer", "GTM Engineer", 0),
        ("Software Engineer", "Go-to-Market Software Engineer", 2),
        ("Software Engineer", "Sales Engineer, Software Engineering", 0),
        ("Software Engineer", "Revenue Engineer, Software Engineering", 0),
        ("Software Engineer", "Customer Success Engineer, Software Engineering", 0),
        ("Software Engineer", "Customer Support Engineer, Software Engineering", 0),
        ("Software Engineer", "GTM Engineer, Software Engineering", 0),
        ("Software Engineer", "Engineering Manager, Software Engineering", 0),
        ("Software Engineer", "Director of Software Engineering", 0),
        ("Product Manager", "Product Manager, Dashboard", 2),
        ("GTM Engineer", "GTM Engineer", 2),
        ("Data Scientist", "Senior Data Scientist", 2),
    ],
)
def test_title_intent_has_common_aliases_and_preserves_qualifiers(role, title, rank):
    assert role_aliases.title_relevance(role, title) == rank


def _request(key, role="Software Engineer", count=3):
    prices = config.prices()
    return schemas.SearchCreate(
        resume_id=10,
        role=role,
        desired_count=count,
        published_within_days=30,
        idempotency_key=key,
        accepted_quote=schemas.SearchPriceAcceptance(
            pricing_version=prices["pricing_version"],
            unit_price=prices["search_credits_per_job"],
            maximum_credits=count * prices["search_credits_per_job"],
        ),
    )


def test_catalogue_actual_product_manager_gtm_misrank_never_delivers_or_charges_them(
    catalogue_db, monkeypatch
):
    db = catalogue_db
    monkeypatch.setenv("EMPLOYER_SEARCH_CREDITS_PER_JOB", "1")
    db.get(core.Resume, 10).skills = ["Python", "Vercel"]
    _content(db, "one-platform-skill", ["Vercel"])
    _content(db, "engineering-evidence", ["Python", "SQL", "Docker"])
    rows = [
        ("a-pm-dashboard", "Product Manager, Dashboard", "one-platform-skill", 28),
        ("b-pm-compute", "Product Manager, Compute", "one-platform-skill", 24),
        ("c-gtm", "GTM Engineer", "one-platform-skill", 7),
        ("d-engineer", "Software Engineer, Compute", "engineering-evidence", 20),
        ("e-backend", "Backend Developer", "one-platform-skill", 7),
        ("f-old", "Software Engineer", "one-platform-skill", 31),
        ("g-unknown-date", "Software Engineer", "one-platform-skill", None),
    ]
    for identity, title, content, age in rows:
        _posting(db, identity, content, title=title)
        db.flush()
        for model in (models.EmployerPosting, models.EmployerCataloguePostingRevision):
            row = (
                db.query(model)
                .filter_by(
                    **(
                        {"id": identity}
                        if model is models.EmployerPosting
                        else {"posting_id": identity}
                    )
                )
                .one()
            )
            row.publication_at = utcnow() - timedelta(days=age) if age is not None else None
    db.commit()
    plan = service.create_search(db, 1, _request("actual-role-regression"))
    plan.resume_ready, plan.resume_skills, plan.lease_token = True, ["python", "vercel"], TOKEN
    db.commit()
    identity = plan.id
    selected = _select(db, identity)
    assert [winner.posting_id for winner in selected] == ["d-engineer", "e-backend"]
    assert [winner.fit["score"] for winner in selected] == [33.3, 100.0]
    assert [winner.title_hit for winner in selected] == [True, False]
    result = _complete(db, identity)
    assert [item["posting"]["id"] for item in result.items] == ["d-engineer", "e-backend"]
    assert (
        result.desired_count,
        result.delivered_count,
        result.reserved_credits,
        result.charged_credits,
        result.refunded_credits,
    ) == (3, 2, 3, 2, 1)
    assert db.get(core.User, 1).job_service_credits == 48
    assert all(
        item["preference_evaluation"]["eligibility_verified"] is False for item in result.items
    )
    reservation = db.query(models.ServiceCreditReservation).filter_by(source_id=identity).one()
    assert (reservation.state, reservation.committed_amount, reservation.released_amount) == (
        "settled",
        2,
        1,
    )
    assert (
        db.query(models.ServiceCreditEvent)
        .filter_by(source_id=identity, event_type="release")
        .one()
        .amount
        == 1
    )
    assert db.query(models.EmployerJobDelivery).filter_by(search_id=identity).count() == 2
    saved = deepcopy(result.items)
    replay = service.create_search(db, 1, _request("actual-role-regression"))
    assert replay.items == saved and db.get(core.User, 1).job_service_credits == 48
    assert (
        db.query(models.ServiceCreditEvent)
        .filter_by(source_id=identity, event_type="commit")
        .count()
        == 1
    )


@pytest.mark.parametrize(
    "role,expected",
    [
        ("Software Engineer", []),
        ("Product Manager", ["role-pm"]),
        ("GTM Engineer", ["role-gtm"]),
    ],
)
def test_bounded_search_filters_mentions_and_refunds_shortfall(
    indexed_search, monkeypatch, role, expected
):
    db = indexed_search
    monkeypatch.setenv("EMPLOYER_SEARCH_CREDITS_PER_JOB", "1")
    db.query(models.EmployerPosting).delete(synchronize_session=False)
    db.get(core.Resume, 10).skills = ["Vercel"]
    for identity, title in [
        ("role-pm", "Product Manager, Dashboard"),
        ("role-gtm", "GTM Engineer"),
    ]:
        db.add(
            models.EmployerPosting(
                id=identity,
                source_id="source_index",
                external_id=identity,
                requisition_id=identity,
                title=title,
                employer="Synthetic Employer",
                location="Anywhere",
                description="Vercel. You will collaborate with software engineers and software developers.",
                canonical_url="https://employer.example/jobs/" + identity,
                apply_url="https://employer.example/jobs/" + identity,
                content_sha256=payload_fingerprint(identity),
                publication_at=utcnow() - timedelta(days=7),
                last_checked_at=utcnow(),
            )
        )
    db.commit()
    request = _request("bounded-role-" + role.replace(" ", "-"), role)
    result = service.create_search(db, 1, request)
    assert [item["posting"]["id"] for item in result.items] == expected
    assert (
        result.desired_count,
        result.delivered_count,
        result.charged_credits,
        result.refunded_credits,
    ) == (3, len(expected), len(expected), 3 - len(expected))
    assert db.get(core.User, 1).job_service_credits == 50 - len(expected)
    snapshot = deepcopy(result.items)
    assert service.create_search(db, 1, request).items == snapshot
    assert db.get(core.User, 1).job_service_credits == 50 - len(expected)


def test_department_titles_remain_eligible_but_actual_sales_roles_do_not(catalogue_db, monkeypatch):
    db = catalogue_db
    monkeypatch.setenv("EMPLOYER_SEARCH_CREDITS_PER_JOB", "1")
    _content(db, "department-role-evidence", ["Python"])
    for identity, title in [
        ("a-revenue-platform", "Software Engineer, Revenue Systems"),
        ("b-sales-platform", "Software Engineer, Sales Platform"),
        ("c-sales-role", "Sales Engineer, Software Engineering"),
        ("d-customer-role", "Customer Success Engineer, Software Engineering"),
    ]:
        _posting(db, identity, "department-role-evidence", title=title)
    db.commit()
    plan = service.create_search(db, 1, _request("department-role-regression"))
    plan.resume_ready, plan.resume_skills, plan.lease_token = True, ["python"], TOKEN
    db.commit()
    identity = plan.id
    assert [winner.posting_id for winner in _select(db, identity)] == [
        "a-revenue-platform",
        "b-sales-platform",
    ]
    result = _complete(db, identity)
    assert [item["posting"]["title"] for item in result.items] == [
        "Software Engineer, Revenue Systems",
        "Software Engineer, Sales Platform",
    ]
    assert (
        result.desired_count,
        result.delivered_count,
        result.charged_credits,
        result.refunded_credits,
    ) == (3, 2, 2, 1)

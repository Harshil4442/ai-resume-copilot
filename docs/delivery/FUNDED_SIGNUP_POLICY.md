# Prospective signup funding policy

This is part of the existing Stage 2/6 funding work. It does not activate the
new wallet, free-core allowance, optional AI, purchases or a promotion.

New password, Google and native candidate registrations explicitly start with
zero legacy analysis units and zero legacy job-service credits. The revised
plan funds bounded basic tools separately and quotes optional paid work; a new
account must not silently create an unfunded second promotional currency.
Future promotions require their own explicit funded ledger grant.

All existing balances keep their original terms. Login, a duplicate registration,
account enrollment and recovery do not reset, convert or add to those balances.
The historical ORM/database default and migration history remain unchanged;
only the three actual public account-creation paths use the prospective policy.
There is no schema change or production balance update.

Local verification covers password and verified-Google registration, repeated
login, duplicate registration and retained balances through authenticated reads:
57 auth and security tests passed, with Ruff and Mypy checks passing. Native
credential-lifecycle tests additionally assert new balances and preservation of
retained balances; their real localhost Firestore emulator is required and was
unavailable locally. The initial unavailable-emulator run is retained as a
limitation, not reported as a pass. Those tests must pass in the combined CI
environment before release.

The cold browser journeys create actual new accounts and therefore assert zero
initial legacy units. Their finite legacy-bundle fixture grants exactly two
analysis units only after its signed synthetic payment capture; duplicate
capture and subsequent non-AI work leave that purchased balance at two. The
unified-pass journey leaves both legacy balances at zero. Retained historical
balances are exercised separately through explicit existing-account fixtures.
These synthetic payment fixtures are not a production promotion or receipt.

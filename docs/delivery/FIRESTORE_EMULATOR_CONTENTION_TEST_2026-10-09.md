# Firestore contention test correction

Status: test-only correction integrated and verified by the parent. No application source, retry policy, cloud configuration or deployment was changed.

## Contract and diagnosis

Google documents contention failures and transaction isolation. Its emulator explicitly uses different, simplified locking behavior from production. These contracts do not promise one successful attempt whenever two native transactions compete. Treating two definite `ABORTED` replies as valid is an inference from that contract and the observed emulator behavior, rather than a documented promise of double aborts. [Contention](https://docs.cloud.google.com/firestore/native/docs/transaction-data-contention), [emulator transaction limitations](https://docs.cloud.google.com/firestore/native/docs/emulator#transactions).

The native RPC adapter already passes `retry=None` and propagates definite `Aborted` exceptions. The buffer starts a fresh transaction after each definite abort and keeps its existing three-attempt budget. Direct RPC callers must manage retries; retrying requires a fresh read/modify/write sequence. [Firestore retry guidance](https://docs.cloud.google.com/firestore/native/docs/best-practices#transactions_retries), [transaction RPC contract](https://docs.cloud.google.com/firestore/docs/reference/rpc/google.firestore.v1#google.firestore.v1.BeginTransactionRequest).

No runtime defect was found. The tests assumed a particular contention schedule: exactly one original native winner, then exactly one callback for the buffered winner and two for its loser. The emulator produced other safe schedules.

## Stronger assertions

- The direct race still rejects two committed competitors. It requires at least one definite abort, exactly one SDK Commit per original transaction, create-if-absent preconditions, no implicit Begin and `retry=None`. Every aborted transaction is cleaned up. The caller explicitly begins distinct new transactions, re-reads absence or ownership and creates only if still absent. Exactly one final owner persists; fresh conflict reads send no overwrite.
- The buffered race records callback transaction IDs and fresh read states. Each original operation retains at most three attempts and all IDs are distinct. Only definite-abort budget exhaustion can enter the test's `EXHAUSTED` state; ambiguous commits and other outages fail. An explicitly new one-attempt caller operation may recover after both original operations definitively exhaust and a fresh read confirms absence. This does not enlarge either original operation's budget. A separately fresh losing contender must observe immutable conflict with no Commit.
- Changing the hold binding still cannot replace the permanent owner. No ambiguous result is promoted into successful ownership.

## Stage evidence

1. Parent's initial full-suite integration reported **1,235 passed, one failed**: the direct race returned two `ABORTED` replies. This remains historical failed-stage evidence, not a successful full-suite result.
2. The isolated corrected direct test passed against the existing localhost emulator and recorded those same two aborted replies, followed by an explicit fresh successful create and a fresh conflict.
3. The next all-six emulator run found the neighboring unsupported attempt count: **five passed, one failed**, with its loser correctly reporting conflict on its third callback rather than its second.
4. After both corrections, a six-case emulator run passed. The final frozen assertions then passed **five consecutive six-case suites: 30 passed, zero failures/errors/skips**, in 7.404, 9.221, 5.240, 5.160 and 7.199 seconds. Observed schedules included two original native aborts, a buffered loser needing three attempts, and a buffered definite-abort exhaustion followed by a fresh conflict. These are bounded observations, not a statistical stability guarantee.
5. Ruff and compilation passed. Optional whole-file Mypy comparison reports the same pre-existing nested-dictionary inference error in an unrelated overlay test (original line 131, patched line 130); the changed tests introduce no additional type error.

Tests used only the existing root-owned `127.0.0.1:58877` emulator, anonymous explicit insecure transport and fresh unique synthetic named databases. ADC/cloud transports were prohibited. No shared data was reset, no container was destroyed and no production call was made. The parent must run the merged suite after applying the patch. Emulator evidence does not prove production locking schedules, IAM, database identity, retention or cloud durability.

## Final root component integration

The corrected default root suite passed **1,261 tests with no failures, errors or skips**.
[The integration report](ROOT_COMPONENT_INTEGRATION_2026-10-09.md) retains the earlier
failed stage and distinguishes local proof from zero-traffic compatibility staging.
Production money cutover and browser authority remain unenabled.

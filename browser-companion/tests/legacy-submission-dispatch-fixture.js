// Invocation-owned legacy fixture only. Shipping always requests protocol five.
// The exact original 0.8.7 (8648118cace6284f5b7e7347607475d6cb4e0f92)
// preparation dispatch creates a genuine old review before the unchanged current
// runtime/transport/adapter execute its accepted protocol-four/20-credit path.
// No price, approval, native proof, owner check or signed request is fabricated.
import assert from 'node:assert/strict';
export function legacySubmissionDispatch(source) {
  const current = 'if (message.type === "assistance_prepare_submit") return current.prepare(message.application_id, 5);';
  const historical = 'if (message.type === "assistance_prepare_submit") return current.prepare(message.application_id, 4);';
  assert.equal(source.split(current).length, 2, 'Exact one shipping protocol-five dispatch is required');
  return source.replace(current, historical);
}

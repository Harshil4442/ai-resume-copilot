export function fakeRequestDeadlines(t) {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const deadlines = [];
  // Node's native AbortSignal timer bypasses MockTimers. Keep real AbortSignal
  // composition and abort-aware fetch behavior while advancing the deadline.
  t.mock.method(AbortSignal, "timeout", (milliseconds) => {
    deadlines.push(milliseconds);
    const controller = new AbortController();
    setTimeout(() => controller.abort(new DOMException("Request deadline exceeded", "TimeoutError")), milliseconds);
    return controller.signal;
  });
  return deadlines;
}

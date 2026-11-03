export const SIGN_IN_RETRY_AFTER_SECONDS = 60;
export const SIGN_IN_CAPACITY_MESSAGE = "Sign-in capacity is temporarily busy. Please wait 60 seconds, then try again.";

export class SignInRateLimitedError extends Error {
  constructor() {
    super("RATE_LIMITED");
  }
}

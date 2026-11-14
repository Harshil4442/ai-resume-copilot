export interface RazorpaySuccessResponse {
  razorpay_payment_id: string;
  razorpay_order_id: string;
  razorpay_signature: string;
}

export interface RazorpayOptions {
  key: string; amount: number; currency: string; name: string; description: string; order_id: string;
  prefill?: { email?: string };
  handler: (response: RazorpaySuccessResponse) => void;
  modal: { confirm_close: boolean; ondismiss: () => void };
  retry: { enabled: boolean };
  theme: { color: string };
}

interface RazorpayCheckout {
  open: () => void;
  on: (event: "payment.failed", callback: (response: { error?: { description?: string } }) => void) => void;
}

type RazorpayConstructor = new (options: RazorpayOptions) => RazorpayCheckout;

declare global {
  interface Window { Razorpay?: RazorpayConstructor }
}

const CHECKOUT_SCRIPT_TIMEOUT_MS = 12_000;
let checkoutScriptPromise: Promise<RazorpayConstructor> | null = null;

export class HostedCheckoutLoadError extends Error {}

/** Call only after a validated, explicitly accepted server order exists. */
export function loadHostedCheckout(): Promise<RazorpayConstructor> {
  if (typeof window === "undefined") return Promise.reject(new HostedCheckoutLoadError("Checkout is available only in a browser."));
  if (window.Razorpay) return Promise.resolve(window.Razorpay);
  if (checkoutScriptPromise) return checkoutScriptPromise;
  const attempt = new Promise<RazorpayConstructor>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://checkout.razorpay.com/v1/checkout.js";
    script.async = true;

    function finish(checkout?: RazorpayConstructor) {
      window.clearTimeout(timeout);
      script.onload = null;
      script.onerror = null;
      if (checkout) {
        resolve(checkout);
        return;
      }
      script.remove();
      reject(new HostedCheckoutLoadError("Razorpay checkout did not open. Check the original payment status before trying again."));
    }

    const timeout = window.setTimeout(() => finish(), CHECKOUT_SCRIPT_TIMEOUT_MS);
    script.onload = () => finish(window.Razorpay);
    script.onerror = () => finish();
    try {
      document.body.appendChild(script);
    } catch {
      finish();
    }
  });
  checkoutScriptPromise = attempt.finally(() => { checkoutScriptPromise = null; });
  return checkoutScriptPromise;
}

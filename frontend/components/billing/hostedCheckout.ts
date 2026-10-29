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

declare global {
  interface Window { Razorpay?: new (options: RazorpayOptions) => RazorpayCheckout }
}

let checkoutScriptPromise: Promise<void> | null = null;

/** Call only after a validated, explicitly accepted server order exists. */
export function loadHostedCheckout(): Promise<void> {
  if (typeof window === "undefined") return Promise.reject(new Error("Checkout is available only in a browser."));
  if (window.Razorpay) return Promise.resolve();
  if (checkoutScriptPromise) return checkoutScriptPromise;
  checkoutScriptPromise = new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://checkout.razorpay.com/v1/checkout.js";
    script.async = true;
    script.onload = () => {
      if (window.Razorpay) resolve();
      else { checkoutScriptPromise = null; script.remove(); reject(new Error("The hosted checkout did not initialize.")); }
    };
    script.onerror = () => { checkoutScriptPromise = null; script.remove(); reject(new Error("The hosted checkout could not be loaded.")); };
    document.body.appendChild(script);
  });
  return checkoutScriptPromise;
}

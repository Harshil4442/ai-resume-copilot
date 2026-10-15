import BrowserAssistanceReview from "../../components/BrowserAssistanceReview";
import BrowserPairingReview from "../../components/BrowserPairingReview";
import BrowserDeviceRevocation from "../../components/BrowserDeviceRevocation";

export const metadata = { title: "Reviewed browser assistance | HireWiz", robots: { index: false, follow: false } };

export default async function BrowserCompanionPage({ searchParams }: { searchParams: Promise<{ device?: string | string[]; review?: string | string[]; application?: string | string[]; pairing?: string | string[]; native_device?: string | string[] }> }) {
  const query = await searchParams;
  const deviceId = typeof query.device === "string" ? query.device : null;
  const reviewId = typeof query.review === "string" ? query.review : null;
  const applicationId = typeof query.application === "string" ? query.application : null;
  const pairingId = typeof query.pairing === "string" ? query.pairing : null;
  const nativeDeviceId = typeof query.native_device === "string" ? query.native_device : null;
  const conflicting = (query.pairing != null || query.native_device != null)
    && [query.pairing, query.native_device, query.device, query.review, query.application].filter(value => value != null).length > 1;
  return <main className="section-shell py-10 sm:py-16"><div className="mx-auto max-w-3xl">{conflicting ? <p role="alert">Open one exact connection or application review from your companion.</p>
    : query.pairing != null ? <BrowserPairingReview key={pairingId} pairingId={pairingId} />
    : query.native_device != null ? <BrowserDeviceRevocation key={nativeDeviceId} deviceId={nativeDeviceId} />
    : <BrowserAssistanceReview key={`${deviceId}:${reviewId}:${applicationId}`} deviceId={deviceId} reviewId={reviewId} applicationId={applicationId} />}</div></main>;
}

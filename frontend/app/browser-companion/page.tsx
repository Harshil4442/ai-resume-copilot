import BrowserAssistanceReview from "../../components/BrowserAssistanceReview";

export const metadata = { title: "Reviewed browser assistance | HireWiz", robots: { index: false, follow: false } };

export default async function BrowserCompanionPage({ searchParams }: { searchParams: Promise<{ device?: string | string[]; review?: string | string[]; application?: string | string[] }> }) {
  const query = await searchParams;
  const deviceId = typeof query.device === "string" ? query.device : null;
  const reviewId = typeof query.review === "string" ? query.review : null;
  const applicationId = typeof query.application === "string" ? query.application : null;
  return <main className="section-shell py-10 sm:py-16"><div className="mx-auto max-w-3xl"><BrowserAssistanceReview key={`${deviceId}:${reviewId}:${applicationId}`} deviceId={deviceId} reviewId={reviewId} applicationId={applicationId} /></div></main>;
}

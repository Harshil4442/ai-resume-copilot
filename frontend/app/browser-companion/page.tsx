import BrowserPairingReview from "../../components/BrowserPairingReview";

export default async function BrowserCompanionPage({ searchParams }: { searchParams: Promise<{ pairing?: string | string[] }> }) {
  const query = await searchParams;
  const pairingId = typeof query.pairing === "string" ? query.pairing : null;
  return <main className="section-shell py-10 sm:py-16"><div className="mx-auto max-w-3xl"><BrowserPairingReview key={pairingId} pairingId={pairingId} /></div></main>;
}

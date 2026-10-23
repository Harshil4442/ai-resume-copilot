import CandidateAccountRecovery from "../../components/CandidateAccountRecovery";

export const metadata = { title: "Recover an existing account | HireWiz", robots: { index: false, follow: false } };

export default function AccountRecoveryPage() {
  return <main className="section-shell py-10 sm:py-16"><div className="mx-auto max-w-3xl"><CandidateAccountRecovery /></div></main>;
}

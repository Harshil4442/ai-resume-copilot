import CandidateAccountEnrollment from "../../components/CandidateAccountEnrollment";

export const metadata = { title: "Browser account enrollment | HireWiz", robots: { index: false, follow: false } };

export default function AccountEnrollmentPage() {
  return <main className="section-shell py-10 sm:py-16"><div className="mx-auto max-w-3xl"><CandidateAccountEnrollment /></div></main>;
}

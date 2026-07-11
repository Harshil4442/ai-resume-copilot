import PageHeader from "../../components/ui/PageHeader";

export default function PrivacyPolicy() {
  return (
    <main className="flex min-h-screen flex-col items-center pb-20">
      <PageHeader 
        badge="Legal"
        title="Privacy Policy" 
        subtitle="How we collect, use, and protect your data." 
      />
      <div className="w-full max-w-4xl mx-auto px-6 md:px-8 mt-12 text-slate-300 space-y-8 leading-relaxed">
        <p className="text-sm text-slate-400">
          Last updated: 11 July 2026. HireWiz is operated by SAVALIYA HARSHIL YOGESHBHAI, an
          individual trading as HireWiz, based in Gujarat, India. For any privacy question, contact{" "}
          <a href="mailto:work@hirewizhq.com" className="text-primary hover:underline">work@hirewizhq.com</a>.
        </p>
        <section>
          <h2 className="text-2xl font-bold text-white mb-4">1. Information We Collect</h2>
          <p>
            When you use HireWiz, we collect information that you provide to us directly, including:
          </p>
          <ul className="list-disc pl-6 mt-4 space-y-2">
            <li><strong>Account Information:</strong> Name, email address, and authentication credentials.</li>
            <li><strong>Career Data:</strong> Resumes (PDFs, text), work history, skills, and target job descriptions you upload.</li>
            <li><strong>Usage Data:</strong> How you interact with our platform, including match history and generated learning strategies.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">2. How We Use Your Data</h2>
          <p>We use the collected data strictly to provide and improve our core services:</p>
          <ul className="list-disc pl-6 mt-4 space-y-2">
            <li>To parse and structure your resume for ATS analysis.</li>
            <li>To match your profile against job descriptions using our AI models.</li>
            <li>To generate personalized learning strategies and bullet point optimizations.</li>
            <li>To operate, maintain, secure, and improve the reliability of the service.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">3. Data Sharing and Third-Party AI Services</h2>
          <p>
            Because HireWiz is an AI-assisted platform, we transmit your resume text and job descriptions to third-party AI providers only to generate the insights you request. We do not sell your personal data. Where our AI providers offer controls to disable the use of submitted content for training their models, we aim to use those controls.
          </p>
          <p className="mt-4 text-sm text-slate-400">
            A full, provider-by-provider list of the subprocessors we use, along with their data-handling and retention terms, will be published on this page. We do not make blanket guarantees on behalf of third parties beyond the settings and contractual terms actually available to us.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">4. Data Retention and Deletion</h2>
          <p>
            You own your career data. You may request deletion of your account and all associated
            resumes, match histories, and parsed data at any time by emailing{" "}
            <a href="mailto:work@hirewizhq.com" className="text-primary hover:underline">work@hirewizhq.com</a>{" "}
            from your registered email address. Once verified, your data is removed from our active
            databases; residual copies in encrypted backups are overwritten on our regular backup
            cycle, and we retain only records we are required to keep by law.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">5. Contact Us</h2>
          <p>
            If you have questions about this Privacy Policy, or wish to access, correct, or delete
            your data, please contact us at{" "}
            <a href="mailto:work@hirewizhq.com" className="text-primary hover:underline">work@hirewizhq.com</a>.
          </p>
        </section>
      </div>
    </main>
  );
}

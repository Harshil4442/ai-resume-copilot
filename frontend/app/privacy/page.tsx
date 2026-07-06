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
        <section>
          <h2 className="text-2xl font-bold text-white mb-4">1. Information We Collect</h2>
          <p>
            When you use HireWiz (operated by SAVALIYA HARSHIL YOGESHBHAI), we collect information that you provide to us directly, including:
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
            <li>To improve our parsing accuracy and market trend algorithms.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">3. Data Sharing and Third-Party AI Services</h2>
          <p>
            Because we are an AI-powered platform, we securely transmit your resume data and job descriptions to third-party AI providers (such as OpenAI or Anthropic) solely for the purpose of generating insights. <strong>We strictly opt-out of data training.</strong> Our AI partners are contractually prohibited from using your personal career data to train their foundational models.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">4. Data Retention and Deletion</h2>
          <p>
            You own your career data. You may delete your account and all associated resumes, match histories, and parsed data at any time from your Profile settings. Upon deletion, your data is permanently removed from our active databases.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">5. Contact Us</h2>
          <p>
            If you have questions about this Privacy Policy, please contact us at privacy@airesumecopilot.com.
          </p>
        </section>
      </div>
    </main>
  );
}

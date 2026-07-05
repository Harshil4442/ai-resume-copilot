import PageHeader from "../../components/ui/PageHeader";

export default function CookiePolicy() {
  return (
    <main className="flex min-h-screen flex-col items-center pb-20">
      <PageHeader 
        badge="Legal"
        title="Cookie Policy" 
        subtitle="How we use cookies to improve your experience." 
      />
      <div className="w-full max-w-4xl mx-auto px-6 md:px-8 mt-12 text-slate-300 space-y-8 leading-relaxed">
        <section>
          <h2 className="text-2xl font-bold text-white mb-4">1. What Are Cookies?</h2>
          <p>
            Cookies are small text files stored on your device by your web browser when you visit a website. They are widely used to make websites work efficiently and provide tracking information to the site owners.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">2. How We Use Cookies</h2>
          <p>AI Resume CoPilot uses cookies for the following purposes:</p>
          <ul className="list-disc pl-6 mt-4 space-y-2">
            <li><strong>Essential Cookies:</strong> These are required for the platform to function. They handle user authentication, secure your session, and allow you to access your dashboard. You cannot opt out of these if you wish to use the service.</li>
            <li><strong>Functional Cookies:</strong> These remember your preferences, such as your dark/light theme choices and your last accessed job match, to provide a smoother user experience.</li>
            <li><strong>Analytics Cookies:</strong> We use minimal analytics to understand how users interact with our tools (e.g., which micro-tools are most popular) so we can prioritize new features. This data is anonymized.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">3. Third-Party Cookies</h2>
          <p>
            We do not sell your data to advertisers. We do not use third-party tracking cookies for targeted advertising. Any third-party cookies present are strictly related to secure authentication providers or necessary infrastructure services.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">4. Managing Your Cookies</h2>
          <p>
            You can control or delete cookies through your browser settings. However, disabling essential cookies will prevent you from logging into your AI Resume CoPilot account or saving your parsed resume data.
          </p>
        </section>
      </div>
    </main>
  );
}

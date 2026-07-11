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
        <p className="text-sm text-slate-400">
          Last updated: 11 July 2026. HireWiz is operated by SAVALIYA HARSHIL YOGESHBHAI, an
          individual trading as HireWiz. Questions about this policy:{" "}
          <a href="mailto:work@hirewizhq.com" className="text-primary hover:underline">work@hirewizhq.com</a>.
        </p>
        <section>
          <h2 className="text-2xl font-bold text-white mb-4">1. What Are Cookies?</h2>
          <p>
            Cookies are small text files stored on your device by your web browser when you visit a website. They are widely used to make websites work efficiently and provide tracking information to the site owners.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">2. How We Use Cookies</h2>
          <p>HireWiz uses cookies for the following purposes:</p>
          <ul className="list-disc pl-6 mt-4 space-y-2">
            <li><strong>Essential Cookies:</strong> These are required for the platform to function. They handle user authentication, secure your session, and allow you to access your dashboard. You cannot opt out of these if you wish to use the service.</li>
            <li><strong>Functional Cookies:</strong> These remember your preferences, such as your dark/light theme choices and your last accessed job match, to provide a smoother user experience.</li>
            <li><strong>Analytics Cookies:</strong> We use Google Analytics 4 to understand how visitors interact with our tools (e.g., which pages and features are used) so we can prioritize improvements. Google may set cookies to measure usage; see Google's own privacy documentation for details of the data it processes.</li>
          </ul>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">3. Third-Party Cookies</h2>
          <p>
            We do not sell your data to advertisers, and we do not use third-party cookies for targeted advertising. Third-party cookies that may be present are limited to Google Analytics (usage measurement) and our authentication provider (secure sign-in and session management). A detailed, itemized inventory of the specific cookies and local-storage keys we use will be published on this page.
          </p>
        </section>

        <section>
          <h2 className="text-2xl font-bold text-white mb-4">4. Managing Your Cookies</h2>
          <p>
            You can control or delete cookies through your browser settings. However, disabling essential cookies will prevent you from logging into your HireWiz account or saving your parsed resume data.
          </p>
        </section>
      </div>
    </main>
  );
}

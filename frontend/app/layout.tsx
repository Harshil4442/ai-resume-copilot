import { Inter } from 'next/font/google';
import "./globals.css";
import Nav from "../components/Nav";
import Footer from "../components/Footer";
import SessionProviderWrapper from "../components/SessionProviderWrapper";
import AnimatedBackground from "../components/ui/AnimatedBackground";
import AnalyticsConsent from "../components/AnalyticsConsent";
import { Metadata } from 'next';
import { SITE } from "../lib/site";

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' });

export const metadata: Metadata = {
  metadataBase: new URL(SITE.canonicalUrl),
  title: {
    default: "HireWiz | Evidence-backed career workspace",
    template: "%s | HireWiz",
  },
  description:
    "Turn real career evidence into role-specific resumes, application decisions, interview preparation, and a connected job-search workspace.",
  applicationName: "HireWiz",
  openGraph: {
    type: "website",
    url: "https://www.hirewizhq.com",
    siteName: "HireWiz",
    title: "HireWiz | Evidence-backed career workspace",
    description:
      "A connected workspace for evidence-backed resumes, opportunity decisions, interview preparation, and application outcomes.",
  },
  twitter: {
    card: "summary_large_image",
    title: "HireWiz | Evidence-backed career workspace",
    description:
      "Build stronger applications from approved career evidence and keep every opportunity connected.",
  },
};

const GA_MEASUREMENT_ID = process.env.NEXT_PUBLIC_GA_ID?.trim() || null;
const POSTHOG_KEY = process.env.NEXT_PUBLIC_POSTHOG_KEY?.trim() || null;
const POSTHOG_HOST = process.env.NEXT_PUBLIC_POSTHOG_HOST?.trim() || "https://us.i.posthog.com";

const softwareApplicationJsonLd = {
  "@context": "https://schema.org",
  "@type": "SoftwareApplication",
  name: SITE.name,
  url: SITE.canonicalUrl,
  applicationCategory: "BusinessApplication",
  operatingSystem: "Web",
  description:
    "Self-service, AI-assisted software for reviewing a user's own resume, comparing it with job-description text, and generating informational suggestions.",
  provider: {
    "@type": "Person",
    name: SITE.operatorName,
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} font-sans`} data-scroll-behavior="smooth">
      <head>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(softwareApplicationJsonLd) }}
        />
      </head>
      <body className="flex min-h-screen flex-col text-[#f4f2ea] selection:bg-primary/20 selection:text-white">
        <SessionProviderWrapper>
          <AnimatedBackground />
          <Nav />
          <div className="flex flex-grow flex-col pt-16">{children}</div>
          <Footer />
          <AnalyticsConsent
            gaMeasurementId={GA_MEASUREMENT_ID}
            posthogKey={POSTHOG_KEY}
            posthogHost={POSTHOG_HOST}
          />
        </SessionProviderWrapper>
      </body>
    </html>
  );
}

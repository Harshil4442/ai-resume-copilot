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
    default: "HireWiz — AI-assisted resume analysis you review and control",
    template: "%s — HireWiz",
  },
  description:
    "HireWiz is self-service, AI-assisted software to analyze and improve your own resume: compatibility estimates, skill-gap analysis, market insights, and learning suggestions. Results are informational.",
  applicationName: "HireWiz",
  openGraph: {
    type: "website",
    url: "https://www.hirewizhq.com",
    siteName: "HireWiz",
    title: "HireWiz — AI-assisted resume analysis you review and control",
    description:
      "Self-service, AI-assisted resume analysis. Compare your resume with a job description, see a HireWiz compatibility estimate, and get learning suggestions.",
  },
  twitter: {
    card: "summary_large_image",
    title: "HireWiz — AI-assisted resume analysis you review and control",
    description:
      "Self-service, AI-assisted resume analysis. Results are informational estimates, not employer or ATS scores.",
  },
};

const GA_MEASUREMENT_ID = process.env.NEXT_PUBLIC_GA_ID?.trim() || null;

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
    <html lang="en" className={`${inter.variable} font-sans`}>
      <head>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(softwareApplicationJsonLd) }}
        />
      </head>
      <body className="flex flex-col min-h-screen text-white selection:bg-primary/20 selection:text-primary">
        <SessionProviderWrapper>
          <AnimatedBackground />
          <Nav />
          <div className="flex-grow flex flex-col pt-14">{children}</div>
          <Footer />
          <AnalyticsConsent gaMeasurementId={GA_MEASUREMENT_ID} />
        </SessionProviderWrapper>
      </body>
    </html>
  );
}


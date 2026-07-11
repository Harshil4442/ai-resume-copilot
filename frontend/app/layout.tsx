import { Inter } from 'next/font/google';
import "./globals.css";
import Script from "next/script";
import Nav from "../components/Nav";
import Footer from "../components/Footer";
import SessionProviderWrapper from "../components/SessionProviderWrapper";
import AnimatedBackground from "../components/ui/AnimatedBackground";
import { Metadata } from 'next';

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' });

export const metadata: Metadata = {
  metadataBase: new URL("https://www.hirewizhq.com"),
  title: "HireWiz — AI-assisted resume analysis you review and control",
  description:
    "HireWiz is self-service, AI-assisted software to analyze and improve your own resume: compatibility estimates, skill-gap analysis, market insights, and learning suggestions. Results are informational.",
  applicationName: "HireWiz",
  robots: { index: true, follow: true },
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

// Google Analytics 4 measurement ID. Override via env in deployments where
// you don't want analytics (e.g. staging) by setting NEXT_PUBLIC_GA_ID="".
const GA_MEASUREMENT_ID =
  process.env.NEXT_PUBLIC_GA_ID ?? "G-6N789ZRNER";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} font-sans`}>
      <head>
        {GA_MEASUREMENT_ID ? (
          <>
            {/* Google tag (gtag.js) */}
            <Script
              src={`https://www.googletagmanager.com/gtag/js?id=${GA_MEASUREMENT_ID}`}
              strategy="afterInteractive"
            />
            <Script id="gtag-init" strategy="afterInteractive">
              {`
                window.dataLayer = window.dataLayer || [];
                function gtag(){dataLayer.push(arguments);}
                gtag('js', new Date());
                gtag('config', '${GA_MEASUREMENT_ID}');
              `}
            </Script>
          </>
        ) : null}
      </head>
      <body className="flex flex-col min-h-screen text-white selection:bg-primary/20 selection:text-primary">
        <SessionProviderWrapper>
          <AnimatedBackground />
          <Nav />
          <div className="flex-grow flex flex-col pt-14">{children}</div>
          <Footer />
        </SessionProviderWrapper>
      </body>
    </html>
  );
}


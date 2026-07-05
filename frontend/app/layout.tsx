import { Inter } from 'next/font/google';
import "./globals.css";
import Script from "next/script";
import Nav from "../components/Nav";
import Footer from "../components/Footer";
import SessionProviderWrapper from "../components/SessionProviderWrapper";

const inter = Inter({ subsets: ['latin'], variable: '--font-inter' });

export const metadata = {
  title: "AI Resume CoPilot",
  description: "Parse resumes, match JDs, learn skill gaps, and prep interviews.",
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
      <body className="flex flex-col min-h-screen bg-background text-slate-900 selection:bg-primary/20 selection:text-primary">
        <SessionProviderWrapper>
          <Nav />
          <div className="flex-grow flex flex-col">{children}</div>
          <Footer />
        </SessionProviderWrapper>
      </body>
    </html>
  );
}


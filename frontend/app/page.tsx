import {
  ArrowRight,
  ArrowUpRight,
  BriefcaseBusiness,
  Check,
  CheckCheck,
  FileCheck2,
  FileText,
  Fingerprint,
  MessageSquare,
  ShieldCheck,
  Target,
} from "lucide-react";
import Image from "next/image";
import Link from "next/link";

import FadeIn from "../components/ui/FadeIn";
import { SITE } from "../lib/site";
import styles from "./home.module.css";

const benefits = [
  {
    title: "See where you fit",
    copy: "Compare your resume with a real role. Understand the strengths you can show and the gaps worth addressing.",
    icon: Target,
  },
  {
    title: "Make your experience count",
    copy: "Turn the work you’ve actually done into clear, relevant resume content. You review and approve the evidence.",
    icon: Fingerprint,
  },
  {
    title: "Prepare with purpose",
    copy: "Practise role-specific interview questions, grounded in the job description and your approved experience.",
    icon: MessageSquare,
  },
  {
    title: "Keep your search together",
    copy: "Save opportunities, connect resume versions, and track the next step without losing the context.",
    icon: BriefcaseBusiness,
  },
];

const connected = [
  ["Your experience", "Approved career evidence"],
  ["Your target role", "A saved job-description snapshot"],
  ["Your application", "A role-specific resume version"],
  ["Your preparation", "Interview questions with context"],
  ["Your next step", "Application stages and outcomes"],
];

const steps = [
  {
    title: "Start with your resume",
    copy: "Upload a PDF or DOCX. Review the source information and approve the facts you want to use.",
  },
  {
    title: "Find your direction",
    copy: "Save a target role and its job description. Run a match to see what’s relevant and what needs attention.",
  },
  {
    title: "Make your next move",
    copy: "Create a tailored resume, prepare for the interview, and keep the application’s progress in one place.",
  },
];

function WorkspacePreview() {
  return (
    <div
      className={styles.preview}
      aria-label="Illustrative HireWiz opportunity workspace"
    >
      <div className={styles.previewToolbar}>
        <span className={styles.windowDots} aria-hidden="true">
          <i />
          <i />
          <i />
        </span>
        <span>HireWiz / Workspace</span>
        <span className={styles.sampleLabel}>Example workspace</span>
      </div>
      <div className={styles.previewBody}>
        <aside
          className={styles.previewSidebar}
          aria-label="Example workspace sections"
        >
          <span className={styles.previewBrand}>
            HireWiz<span>.</span>
          </span>
          <span className={styles.previewNavActive}>
            <BriefcaseBusiness size={15} /> Opportunities
          </span>
          <span>
            <FileText size={15} /> My resumes
          </span>
          <span>
            <Fingerprint size={15} /> My evidence
          </span>
          <div className={styles.previewSidebarNote}>
            <ShieldCheck size={18} />
            <p>
              Your experience.
              <br />
              Your approval.
            </p>
          </div>
        </aside>
        <div className={styles.previewMain}>
          <div className={styles.previewHeading}>
            <div>
              <p className={styles.previewEyebrow}>YOUR NEXT OPPORTUNITY</p>
              <h2>Software Engineer</h2>
              <p>Product team · Bengaluru · Hybrid</p>
            </div>
            <span className={styles.preparing}>
              <span /> Preparing
            </span>
          </div>
          <div className={styles.previewTabs}>
            <span>Overview</span>
            <span>Resume & evidence</span>
            <span>Interview</span>
          </div>
          <div className={styles.previewCards}>
            <div className={styles.matchCard}>
              <span className={styles.previewEyebrow}>ROLE MATCH</span>
              <div className={styles.matchValue}>
                84<span>/100</span>
                <Target size={25} />
              </div>
              <p>A starting point for your review</p>
              <div className={styles.matchTrack}>
                <span />
              </div>
            </div>
            <div className={styles.evidenceCard}>
              <span className={styles.previewEyebrow}>YOUR EVIDENCE</span>
              <p>
                <CheckCheck size={16} /> Built and shipped a product API
              </p>
              <p>
                <CheckCheck size={16} /> Improved application performance
              </p>
              <span className={styles.approved}>
                <Check size={12} /> Approved by you
              </span>
            </div>
          </div>
          <div className={styles.previewNext}>
            <span className={styles.previewNextIcon}>
              <FileCheck2 size={20} />
            </span>
            <div>
              <strong>Make this application yours.</strong>
              <p>Bring the most relevant experience forward.</p>
            </div>
            <Link
              href="/register"
              aria-label="Create an account to tailor your resume"
            >
              Tailor resume <ArrowUpRight size={14} />
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function HomePage() {
  return (
    <main className={styles.home}>
      <section className={styles.hero} aria-labelledby="home-heading">
        <div className={styles.container}>
          <FadeIn direction="up" duration={0.6}>
            <h1 id="home-heading" className={styles.heroHeading}>
              Your next chapter.
            </h1>
            <div className={styles.heroIntroduction}>
              <p>
                An evidence-backed career workspace.
                <br className={styles.desktopBreak} /> Clearer applications. A
                more organised job search.
              </p>
              <Link href="/register" className="button-primary">
                Build your workspace <ArrowUpRight size={16} />
              </Link>
            </div>
          </FadeIn>
          <div className={styles.heroVisual}>
            <div className={styles.sageBackdrop} aria-hidden="true" />
            <FadeIn delay={0.12} className={styles.device}>
              <WorkspacePreview />
            </FadeIn>
          </div>
          <div className={styles.promiseStrip}>
            <span>Made for your next move:</span>
            <p>
              <FileText size={17} /> Resumes
            </p>
            <p>
              <Target size={17} /> Role matching
            </p>
            <p>
              <Fingerprint size={17} /> Career evidence
            </p>
            <p>
              <MessageSquare size={17} /> Interview preparation
            </p>
          </div>
        </div>
      </section>

      <section
        id="benefits"
        className={`${styles.section} ${styles.container}`}
        aria-labelledby="benefits-heading"
      >
        <FadeIn>
          <p className="eyebrow">Benefits</p>
          <h2 id="benefits-heading" className={styles.sectionHeading}>
            Bring your story into focus.
          </h2>
          <p className={styles.sectionCopy}>
            Real experience. Relevant opportunities. A clear way forward.
          </p>
        </FadeIn>
        <div className={styles.benefitGrid}>
          {benefits.map((item, index) => (
            <FadeIn key={item.title} delay={index * 0.06}>
              <article className={styles.benefit}>
                <item.icon size={24} strokeWidth={1.5} aria-hidden="true" />
                <h3>{item.title}</h3>
                <p>{item.copy}</p>
              </article>
            </FadeIn>
          ))}
        </div>
        <FadeIn className={styles.bannerWrap}>
          <div className={styles.photoBanner}>
            <Image
              src="/images/career-desk.jpg"
              alt="A real workspace with a laptop and notebook, ready for a focused job search"
              fill
              sizes="(max-width: 768px) 100vw, 1200px"
              className={styles.careerPhoto}
            />
            <span className={styles.photoCaption}>
              A little clarity goes a long way.
            </span>
          </div>
        </FadeIn>
      </section>

      <section
        className={`${styles.storySection} ${styles.container}`}
        aria-labelledby="story-heading"
      >
        <FadeIn className={styles.storyCopy}>
          <p className="eyebrow">Your experience, connected</p>
          <h2 id="story-heading" className={styles.sectionHeading}>
            See the bigger picture.
          </h2>
          <p className={styles.sectionCopy}>
            A job search has a lot of moving parts. HireWiz keeps the important
            ones connected to the role you’re working on.
          </p>
          <ol className={styles.storyList}>
            {[
              "Keep the original job description close.",
              "Know which experience supports each claim.",
              "Find the exact resume version you prepared.",
              "Pick up where you left off.",
            ].map((item, index) => (
              <li key={item}>
                <span>0{index + 1}</span>
                <p>{item}</p>
              </li>
            ))}
          </ol>
          <Link href="/about" className="button-secondary">
            Discover HireWiz <ArrowUpRight size={15} />
          </Link>
        </FadeIn>
        <FadeIn direction="right" className={styles.storyVisual}>
          <div className={styles.documentStack}>
            <div className={styles.documentBack} aria-hidden="true" />
            <div className={styles.resumeDocument}>
              <span className={styles.documentLabel}>
                BUILT FROM YOUR EVIDENCE
              </span>
              <span className={styles.documentName}>
                Your experience,
                <br />
                clearly told.
              </span>
              <div className={styles.documentRule} />
              <p>EXPERIENCE</p>
              <div className={styles.documentLines} aria-hidden="true">
                <i />
                <i />
                <i />
              </div>
              <p>SKILLS & PROJECTS</p>
              <div className={styles.skillChips}>
                <span>Problem solving</span>
                <span>Product thinking</span>
                <span>Your expertise</span>
              </div>
              <div className={styles.documentApproval}>
                <ShieldCheck size={20} />
                <span>
                  You approve the facts.
                  <br />
                  <strong>HireWiz helps shape the story.</strong>
                </span>
              </div>
            </div>
            <span className={styles.floatingNote}>
              <Check size={15} /> Evidence approved
            </span>
          </div>
        </FadeIn>
      </section>

      <section
        className={`${styles.section} ${styles.container}`}
        aria-labelledby="connected-heading"
      >
        <FadeIn className={styles.centeredIntro}>
          <p className="eyebrow">The workspace</p>
          <h2 id="connected-heading" className={styles.sectionHeading}>
            Built around your story.
          </h2>
          <p className={styles.sectionCopy}>
            From the first saved role to the next interview, your source
            material stays close to every decision.
          </p>
          <Link href="/register" className="button-secondary">
            Take the first step <ArrowUpRight size={15} />
          </Link>
        </FadeIn>
        <FadeIn>
          <div className={styles.connectionTable}>
            <div className={styles.connectionHead}>
              <span>What you bring</span>
              <span>What stays connected</span>
            </div>
            {connected.map(([source, result]) => (
              <div className={styles.connectionRow} key={source}>
                <span>{source}</span>
                <span>
                  <Check size={17} aria-hidden="true" />
                  {result}
                </span>
              </div>
            ))}
          </div>
        </FadeIn>
      </section>

      <section
        className={`${styles.trustSection} ${styles.container}`}
        aria-labelledby="trust-heading"
      >
        <FadeIn className={styles.trustVisual}>
          <div className={styles.trustPhoto}>
            <Image
              src="/images/career-conversation.jpg"
              alt="Professionals having a focused conversation in a bright workspace"
              fill
              sizes="(max-width: 768px) 100vw, 600px"
              className={styles.careerPhoto}
            />
          </div>
        </FadeIn>
        <FadeIn className={styles.trustCopy}>
          <p className="eyebrow">Built around trust</p>
          <h2 id="trust-heading">
            Your experience is the evidence.
            <br />
            You decide what happens next.
          </h2>
          <p>
            AI can help select, organise, and sharpen your story. You review the
            recommendations and approve the claims before using them.
          </p>
          <Link href="/about" className={styles.textLink}>
            Our product approach <ArrowRight size={16} />
          </Link>
        </FadeIn>
      </section>

      <section
        id="how-it-works"
        className={`${styles.stepsSection} ${styles.container}`}
        aria-labelledby="steps-heading"
      >
        <FadeIn className={styles.stepsIntro}>
          <div>
            <p className="eyebrow">How it works</p>
            <h2 id="steps-heading" className={styles.sectionHeading}>
              Make your next move.
            </h2>
          </div>
          <Link href="/register" className="button-secondary">
            Get started <ArrowUpRight size={15} />
          </Link>
        </FadeIn>
        <ol className={styles.stepGrid}>
          {steps.map((step, index) => (
            <li key={step.title}>
              <FadeIn delay={index * 0.08}>
                <span className={styles.stepNumber}>0{index + 1}</span>
                <h3>{step.title}</h3>
                <p>{step.copy}</p>
              </FadeIn>
            </li>
          ))}
        </ol>
      </section>

      <section
        className={`${styles.closing} ${styles.container}`}
        aria-labelledby="closing-heading"
      >
        <FadeIn>
          <p className="eyebrow">A fresh start</p>
          <h2 id="closing-heading" className={styles.sectionHeading}>
            A clearer path starts here.
          </h2>
          <p>
            Create your free account and start with 50 analysis units.
            <br className={styles.desktopBreak} /> Choose a finite prepaid pack
            when you’re ready.
          </p>
          <Link href="/register" className="button-primary">
            Build your workspace <ArrowUpRight size={17} />
          </Link>
          <div className={styles.closingLinks}>
            <Link href="/pricing">
              Explore pricing <ArrowRight size={14} />
            </Link>
            <a href={`mailto:${SITE.supportEmail}`}>
              Talk to us <ArrowRight size={14} />
            </a>
          </div>
        </FadeIn>
      </section>
    </main>
  );
}

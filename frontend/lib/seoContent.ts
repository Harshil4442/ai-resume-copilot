export type ToolPage = {
  slug: string;
  title: string;
  metaTitle: string;
  description: string;
  badge: string;
  intro: string;
  steps: string[];
  checklist: string[];
  cta: string;
  embedsBulletOptimizer?: boolean;
};

export type BlogPost = {
  slug: string;
  title: string;
  description: string;
  category: string;
  readTime: string;
  sections: Array<{
    heading: string;
    body: string[];
  }>;
};

export const toolPages: ToolPage[] = [
  {
    slug: "resume-bullet-optimizer",
    title: "Resume Bullet Optimizer",
    metaTitle: "Free Resume Bullet Optimizer",
    description: "Test one resume bullet and get a clearer, evidence-based alternative you can review.",
    badge: "Free public tool",
    intro:
      "Paste one bullet from your resume. HireWiz checks for action language and measurable evidence, then suggests a stronger version for you to verify before using.",
    steps: [
      "Paste one resume bullet from your experience section.",
      "Review the action-verb and metrics signal.",
      "Use the suggested alternative only if it is truthful and accurate.",
    ],
    checklist: [
      "Start with a specific action verb.",
      "Name the system, workflow, project, or audience affected.",
      "Add scale, frequency, percentage, time saved, quality improvement, or another truthful measure.",
      "Avoid inventing impact you cannot explain in an interview.",
    ],
    cta: "Create a free account to review full resumes and compare them with job-description text.",
    embedsBulletOptimizer: true,
  },
  {
    slug: "resume-score-checker",
    title: "Resume Score Checker",
    metaTitle: "Free Resume Score Checker Guide",
    description: "Use a practical checklist to estimate whether your resume is clear, specific, and role-aligned.",
    badge: "Checklist",
    intro:
      "A useful resume score is not magic. It is a structured review of clarity, relevance, evidence, formatting, and role alignment.",
    steps: [
      "Check whether the top third explains your target role and strongest evidence.",
      "Review each bullet for action, context, and measurable result.",
      "Compare the resume against one target job description instead of judging it in isolation.",
    ],
    checklist: [
      "Clear target role or profile summary.",
      "Recent work, projects, or education are easy to scan.",
      "Skills match the role without keyword stuffing.",
      "Bullets show evidence, not only responsibilities.",
      "Formatting is readable and export-friendly.",
    ],
    cta: "Create a free HireWiz account to parse your resume and review AI-assisted improvement suggestions.",
  },
  {
    slug: "job-description-match-checker",
    title: "Job Description Match Checker",
    metaTitle: "Free Job Description Match Checker",
    description: "Learn how to compare your resume with a job description without treating estimates as guarantees.",
    badge: "Role alignment",
    intro:
      "A job-description match check helps you see which required skills and responsibilities are clearly supported by your resume.",
    steps: [
      "Paste the target job description into a comparison tool.",
      "Separate must-have skills from nice-to-have skills.",
      "Update your resume only where you have real evidence.",
    ],
    checklist: [
      "Role title and seniority are reflected in your summary.",
      "Core tools and technologies appear naturally in experience bullets.",
      "Important responsibilities are backed by examples.",
      "Missing skills become learning goals, not fake claims.",
    ],
    cta: "Sign up to run detailed HireWiz compatibility estimates and learning suggestions.",
  },
  {
    slug: "resume-keyword-scanner",
    title: "Resume Keyword Scanner",
    metaTitle: "Free Resume Keyword Scanner Guide",
    description: "Find role-relevant keywords and use them naturally in truthful resume evidence.",
    badge: "Keyword review",
    intro:
      "Resume keywords are useful only when they describe real skills, tools, and work. This guide helps you use them without stuffing.",
    steps: [
      "Extract repeated tools, responsibilities, and qualifications from the role description.",
      "Map each keyword to a real project, job, course, or portfolio item.",
      "Rewrite bullets so keywords appear inside meaningful evidence.",
    ],
    checklist: [
      "Include exact tool names when you have used them.",
      "Use both broad skill terms and specific frameworks where accurate.",
      "Avoid hiding keyword lists in the resume.",
      "Keep the resume readable for a human reviewer.",
    ],
    cta: "Create a free account to compare your resume with job descriptions and review missing-skill signals.",
  },
];

export const blogPosts: BlogPost[] = [
  {
    slug: "resume-keywords-for-software-engineers",
    title: "Resume Keywords for Software Engineers",
    description: "A practical guide to choosing honest software-engineering resume keywords from a target role.",
    category: "Software resumes",
    readTime: "5 min read",
    sections: [
      {
        heading: "Start from the role, not a generic keyword list",
        body: [
          "The best software-engineering keywords come from the exact role you are applying for. A backend role may value APIs, databases, observability, queues, and cloud infrastructure. A frontend role may emphasize React, accessibility, state management, testing, and performance.",
          "Use keywords only where you can connect them to a real project, contribution, course, internship, or production responsibility.",
        ],
      },
      {
        heading: "Turn tools into evidence",
        body: [
          "Instead of listing every framework you have seen, show how you used the important ones. For example, a React keyword becomes stronger when attached to a component, dashboard, workflow, or measurable user-facing improvement.",
          "Hiring teams and automated filters both benefit from clear language, but the human reader still needs proof.",
        ],
      },
      {
        heading: "Keep learning gaps visible",
        body: [
          "If a job asks for a skill you do not have yet, do not pretend. Add it to your learning plan and build a small project that gives you truthful evidence for a future application.",
        ],
      },
    ],
  },
  {
    slug: "how-to-match-resume-with-job-description",
    title: "How to Match Your Resume With a Job Description",
    description: "A step-by-step way to compare your resume with a job description and update only truthful evidence.",
    category: "Resume strategy",
    readTime: "6 min read",
    sections: [
      {
        heading: "Highlight the job requirements",
        body: [
          "Read the job description once for context, then again for requirements. Mark the skills, responsibilities, tools, domain knowledge, and seniority signals that appear repeatedly.",
          "Separate mandatory requirements from nice-to-have phrases so you do not over-optimize for less important words.",
        ],
      },
      {
        heading: "Map requirements to your resume",
        body: [
          "For each important requirement, identify the strongest place in your resume where it can be supported. This could be experience, projects, education, certifications, or open-source work.",
          "If your resume already has the evidence but uses different wording, rewrite for clarity. If the evidence does not exist, treat it as a learning gap.",
        ],
      },
      {
        heading: "Review the final version",
        body: [
          "Your final resume should still sound like you. Avoid copying the job description sentence-for-sentence. The goal is truthful alignment, not keyword stuffing.",
        ],
      },
    ],
  },
  {
    slug: "ats-resume-format-for-freshers",
    title: "ATS Resume Format for Freshers",
    description: "A simple resume structure freshers can use for readability, parsing, and honest role alignment.",
    category: "Freshers",
    readTime: "5 min read",
    sections: [
      {
        heading: "Use a simple structure",
        body: [
          "Freshers should prioritize clarity: contact details, summary, skills, projects, education, internships, certifications, and achievements. Avoid complex layouts that make your content harder to read.",
          "A clean single-column format often works better than a heavily designed template.",
        ],
      },
      {
        heading: "Projects are your evidence",
        body: [
          "If you have limited work experience, projects carry much of the signal. Explain what you built, which tools you used, what problem it solved, and what you learned.",
          "Do not exaggerate a tutorial as production experience. Clear learning evidence is still valuable.",
        ],
      },
      {
        heading: "Tailor without inventing",
        body: [
          "For each role, move the most relevant skills and projects higher. Add missing terms only when they truthfully describe your work.",
        ],
      },
    ],
  },
  {
    slug: "react-developer-resume-keywords",
    title: "React Developer Resume Keywords",
    description: "Role-relevant React resume keywords and how to use them inside real project evidence.",
    category: "Frontend",
    readTime: "4 min read",
    sections: [
      {
        heading: "Common React signals",
        body: [
          "React roles often mention hooks, component design, state management, routing, forms, API integration, accessibility, performance, testing, and TypeScript.",
          "Choose the terms that match your actual work and explain where you used them.",
        ],
      },
      {
        heading: "Examples of stronger evidence",
        body: [
          "A weak bullet says you worked on React pages. A stronger bullet names the feature, user flow, API, performance improvement, or test coverage you contributed.",
        ],
      },
    ],
  },
  {
    slug: "java-developer-resume-keywords",
    title: "Java Developer Resume Keywords",
    description: "Java resume keywords for backend, enterprise, and API-focused roles.",
    category: "Backend",
    readTime: "4 min read",
    sections: [
      {
        heading: "Common Java role terms",
        body: [
          "Java roles may ask for Spring Boot, REST APIs, SQL, Hibernate/JPA, microservices, testing, messaging queues, cloud deployment, security, and performance tuning.",
          "These words become stronger when placed in bullets that show actual implementation details.",
        ],
      },
      {
        heading: "Keep architecture claims realistic",
        body: [
          "If you worked on one service, say that. If you designed the architecture, explain your design decisions. Accurate scope builds trust.",
        ],
      },
    ],
  },
  {
    slug: "data-analyst-resume-keywords",
    title: "Data Analyst Resume Keywords",
    description: "Data analyst resume keywords for SQL, dashboards, reporting, and business insight roles.",
    category: "Data",
    readTime: "4 min read",
    sections: [
      {
        heading: "Common data analyst signals",
        body: [
          "Data analyst roles often look for SQL, Excel, Python, dashboards, BI tools, reporting automation, data cleaning, visualization, experimentation, and stakeholder communication.",
          "Use the tool names only when you can describe a real analysis, dashboard, or decision supported by your work.",
        ],
      },
      {
        heading: "Show the business question",
        body: [
          "A strong analyst bullet explains the question, the data used, the method, and the outcome or decision. Tool keywords are helpful, but insight is the real signal.",
        ],
      },
    ],
  },
];

export function getToolPage(slug: string) {
  return toolPages.find((page) => page.slug === slug) ?? null;
}

export function getBlogPost(slug: string) {
  return blogPosts.find((post) => post.slug === slug) ?? null;
}

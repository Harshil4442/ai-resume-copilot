import type { Metadata } from "next";
import { ExternalLink } from "lucide-react";

import TrackedExternalLink from "../../components/TrackedExternalLink";
import TrackEventOnView from "../../components/TrackEventOnView";
import GlassCard from "../../components/ui/GlassCard";
import PageHeader from "../../components/ui/PageHeader";

export const metadata: Metadata = {
  title: "Learning Resources",
  description:
    "Curated official and free learning resources for improving skills commonly referenced in software, frontend, backend, and data roles.",
  alternates: { canonical: "/resources" },
};

const resources = [
  {
    title: "Google Career Certificates",
    description: "Career-oriented learning paths for data analytics, UX, IT support, project management, and more.",
    href: "https://grow.google/certificates/",
    category: "Career learning",
  },
  {
    title: "freeCodeCamp",
    description: "Free programming curriculum and projects for web development, JavaScript, APIs, and data topics.",
    href: "https://www.freecodecamp.org/learn/",
    category: "Programming",
  },
  {
    title: "MDN Web Docs",
    description: "Official-quality references and guides for HTML, CSS, JavaScript, accessibility, and browser APIs.",
    href: "https://developer.mozilla.org/",
    category: "Frontend",
  },
  {
    title: "React Documentation",
    description: "The official React docs for components, hooks, state, effects, and modern React patterns.",
    href: "https://react.dev/learn",
    category: "Frontend",
  },
  {
    title: "PostgreSQL Documentation",
    description: "Official documentation for SQL, indexing, query planning, and PostgreSQL database concepts.",
    href: "https://www.postgresql.org/docs/",
    category: "Backend",
  },
  {
    title: "Kaggle Learn",
    description: "Short practical courses for Python, pandas, machine learning, SQL, and data visualization.",
    href: "https://www.kaggle.com/learn",
    category: "Data",
  },
];

export default function ResourcesPage() {
  return (
    <main className="w-full max-w-[76rem] mx-auto px-4 sm:px-6 md:px-8 py-12 space-y-10">
      <TrackEventOnView eventName="resources_viewed" />
      <PageHeader
        badge="Learning resources"
        title="Curated resources for closing skill gaps."
        subtitle="Use these public resources to build evidence for the skills your target roles ask for."
      />

      <GlassCard className="p-6 border-amber-800/60 bg-amber-950/20" hoverEffect={false}>
        <p className="text-sm text-amber-100 leading-relaxed">
          Disclosure: links currently point to free or official resources. If HireWiz later adds approved affiliate
          links, this page will be updated to clearly identify them. We do not recommend pretending to have skills you
          have not yet practiced.
        </p>
      </GlassCard>

      <section className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {resources.map((resource) => (
          <GlassCard key={resource.href} className="p-7 flex flex-col" hoverEffect={false}>
            <div className="text-xs font-bold uppercase tracking-wider text-blue-300">{resource.category}</div>
            <h2 className="mt-2 text-xl font-black text-white">{resource.title}</h2>
            <p className="mt-3 text-sm text-slate-400 leading-relaxed flex-1">{resource.description}</p>
            <TrackedExternalLink
              href={resource.href}
              className="mt-6 inline-flex items-center gap-2 text-sm font-bold text-primary hover:text-blue-300"
              eventName="resource_link_clicked"
              eventParams={{ resource_title: resource.title, resource_category: resource.category }}
            >
              Open resource <ExternalLink size={14} />
            </TrackedExternalLink>
          </GlassCard>
        ))}
      </section>
    </main>
  );
}

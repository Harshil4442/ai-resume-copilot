"use client";

import { useEffect, useState } from "react";
import { apiGet } from "../../../lib/api";
import type { ResumeParseResponse } from "../../../lib/types";

export default function ResumePreviewPage() {
  const [resumes, setResumes] = useState<Array<{ id: number; filename: string }>>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [resumeData, setResumeData] = useState<ResumeParseResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Fetch user's parsed resumes list
    apiGet<{ resumes: Array<{ id: number; filename: string }> }>("/resume/list")
      .then((data) => {
        setResumes(data.resumes);
        if (data.resumes.length > 0) {
          setSelectedId(data.resumes[0].id);
        } else {
          setLoading(false);
        }
      })
      .catch((loadError: unknown) => {
        setError(loadError instanceof Error ? loadError.message : "Failed to load resumes list");
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (selectedId === null) return;

    // Fetch single resume details
    apiGet<ResumeParseResponse>(`/resume/${selectedId}`)
      .then((data) => {
        setResumeData(data);
      })
      .catch((loadError: unknown) => {
        setError(loadError instanceof Error ? loadError.message : "Failed to load resume details");
      })
      .finally(() => {
        setLoading(false);
      });
  }, [selectedId]);

  function handlePrint() {
    window.print();
  }

  // Format section title for human reading (e.g. 'work_experience' -> 'Work Experience')
  function formatTitle(title: string) {
    return title
      .split(/_|-/)
      .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
      .join(" ");
  }

  if (resumes.length === 0 && !loading) {
    return (
      <main className="app-shell max-w-4xl mx-auto py-12 px-4 text-center">
        <div className="surface-panel p-8">
          <h2 className="font-display text-2xl font-normal text-foreground">No Resumes Found</h2>
          <p className="text-muted-foreground mt-2">Please upload a resume first to preview and print it.</p>
          <a href="/resume" className="button-primary mt-4">Go to Upload</a>
        </div>
      </main>
    );
  }

  return (
    <main className="app-shell max-w-4xl mx-auto py-6 px-4">
      {/* Control panel (not printed) */}
      <div className="no-print surface-panel mb-6 flex flex-wrap items-center justify-between gap-4 p-4">
        <div className="flex items-center gap-3">
          <label className="text-sm font-bold text-foreground">Select Resume:</label>
          <select
            className="field-control min-w-[200px] py-1.5 px-3"
            value={selectedId || ""}
            onChange={(event) => {
              setLoading(true);
              setError(null);
              setSelectedId(Number(event.target.value));
            }}
          >
            {resumes.map((r) => (
              <option key={r.id} value={r.id}>
                {r.filename}
              </option>
            ))}
          </select>
        </div>
        <button onClick={handlePrint} className="button-primary">
          <span>🖨️</span> Print / Save PDF
        </button>
      </div>

      {loading && <div className="text-center py-12 text-muted-foreground">Loading resume document...</div>}
      {error && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-6">{error}</div>}

      {/* Printable resume container */}
      {resumeData && !loading && (
        <div className="resume-container surface-panel flex min-h-[1100px] flex-col justify-between p-8 text-foreground md:p-12">
          <div className="space-y-6">
            {/* Header / Contact Info */}
            <div className="text-center border-b border-border pb-6">
              <h1 className="font-display text-3xl font-normal tracking-tight text-foreground">
                {resumeData.contact_info?.name || "Professional Candidate"}
              </h1>
              <div className="flex flex-wrap justify-center gap-x-4 gap-y-1 text-sm text-foreground mt-2 font-medium">
                {resumeData.contact_info?.email && (
                  <span>✉️ {resumeData.contact_info.email}</span>
                )}
                {resumeData.contact_info?.phone && (
                  <span>📞 {resumeData.contact_info.phone}</span>
                )}
                {resumeData.contact_info?.linkedin && (
                  <span>🔗 {resumeData.contact_info.linkedin}</span>
                )}
                {resumeData.contact_info?.github && (
                  <span>💻 {resumeData.contact_info.github}</span>
                )}
              </div>
            </div>

            {/* Resume Sections */}
            {Object.entries(resumeData.sections || {}).map(([secName, secText]) => {
              if (!secText || secName === "other") return null;
              return (
                <div key={secName} className="space-y-2">
                  <h2 className="font-display text-lg font-normal uppercase tracking-wider text-foreground border-b border-border pb-1">
                    {formatTitle(secName)}
                  </h2>
                  <div className="text-sm text-foreground leading-relaxed whitespace-pre-wrap font-normal">
                    {secText}
                  </div>
                </div>
              );
            })}

            {/* Fallback for other section */}
            {resumeData.sections?.other && (
              <div className="space-y-2">
                <h2 className="font-display text-lg font-normal uppercase tracking-wider text-foreground border-b border-border pb-1">
                  Additional Details
                </h2>
                <div className="text-sm text-foreground leading-relaxed whitespace-pre-wrap font-normal">
                  {resumeData.sections.other}
                </div>
              </div>
            )}
          </div>

          {/* Viral PLG Footer Hook */}
          <div className="mt-12 pt-4 border-t border-border text-center flex justify-center items-center">
            <a
              href="https://ai-resume-copilot-three.vercel.app/?ref=user_resume_share"
              target="_blank"
              rel="noopener noreferrer"
              className="text-[10px] font-bold uppercase tracking-widest text-muted-foreground hover:text-primary transition duration-300 pointer-events-auto decoration-none"
              style={{
                display: "inline-block",
                padding: "4px 8px",
                border: "1px solid var(--color-border)",
                borderRadius: "999px",
                backgroundColor: "var(--color-surface)",
              }}
            >
              Built with HireWiz
            </a>
          </div>
        </div>
      )}

      {/* Print-specific style override */}
      <style dangerouslySetInnerHTML={{ __html: `
        @media print {
          .no-print {
            display: none !important;
          }
          body {
            background: white !important;
            color: black !important;
            padding: 0 !important;
            margin: 0 !important;
          }
          .resume-container {
            border: none !important;
            box-shadow: none !important;
            padding: 0 !important;
            margin: 0 !important;
            min-height: auto !important;
            background: white !important;
            color: black !important;
          }
          .app-shell {
            max-width: 100% !important;
            padding: 0 !important;
            margin: 0 !important;
          }
          header, footer, nav {
            display: none !important;
          }
        }
      ` }} />
    </main>
  );
}

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
      .catch((err: any) => {
        setError(err.message || "Failed to load resumes list");
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (selectedId === null) return;
    setLoading(true);
    setError(null);
    
    // Fetch single resume details
    apiGet<ResumeParseResponse>(`/resume/${selectedId}`)
      .then((data) => {
        setResumeData(data);
      })
      .catch((err: any) => {
        setError(err.message || "Failed to load resume details");
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
        <div className="panel p-8 kinetic-border">
          <h2 className="text-2xl font-black text-slate-900">No Resumes Found</h2>
          <p className="text-slate-500 mt-2">Please upload a resume first to preview and print it.</p>
          <a href="/resume" className="btn-primary mt-4 inline-block">Go to Upload</a>
        </div>
      </main>
    );
  }

  return (
    <main className="app-shell max-w-4xl mx-auto py-6 px-4">
      {/* Control panel (not printed) */}
      <div className="no-print panel p-4 mb-6 kinetic-border flex flex-wrap items-center justify-between gap-4 bg-white/70 backdrop-blur-xl">
        <div className="flex items-center gap-3">
          <label className="text-sm font-bold text-slate-700">Select Resume:</label>
          <select
            className="field py-1.5 px-3 min-w-[200px]"
            value={selectedId || ""}
            onChange={(e) => setSelectedId(Number(e.target.value))}
          >
            {resumes.map((r) => (
              <option key={r.id} value={r.id}>
                {r.filename}
              </option>
            ))}
          </select>
        </div>
        <button onClick={handlePrint} className="btn-primary flex items-center gap-2">
          <span>🖨️</span> Print / Save PDF
        </button>
      </div>

      {loading && <div className="text-center py-12 text-slate-500">Loading resume document...</div>}
      {error && <div className="text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-4 py-3 mb-6">{error}</div>}

      {/* Printable resume container */}
      {resumeData && !loading && (
        <div className="resume-container panel p-8 md:p-12 bg-white text-slate-900 border border-slate-200 shadow-md min-h-[1100px] flex flex-col justify-between">
          <div className="space-y-6">
            {/* Header / Contact Info */}
            <div className="text-center border-b border-slate-200 pb-6">
              <h1 className="text-3xl font-bold tracking-tight text-slate-900">
                {resumeData.contact_info?.name || "Professional Candidate"}
              </h1>
              <div className="flex flex-wrap justify-center gap-x-4 gap-y-1 text-sm text-slate-600 mt-2 font-medium">
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
                  <h2 className="text-lg font-bold uppercase tracking-wider text-slate-800 border-b border-slate-100 pb-1">
                    {formatTitle(secName)}
                  </h2>
                  <div className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap font-normal">
                    {secText}
                  </div>
                </div>
              );
            })}

            {/* Fallback for other section */}
            {resumeData.sections?.other && (
              <div className="space-y-2">
                <h2 className="text-lg font-bold uppercase tracking-wider text-slate-800 border-b border-slate-100 pb-1">
                  Additional Details
                </h2>
                <div className="text-sm text-slate-700 leading-relaxed whitespace-pre-wrap font-normal">
                  {resumeData.sections.other}
                </div>
              </div>
            )}
          </div>

          {/* Viral PLG Footer Hook */}
          <div className="mt-12 pt-4 border-t border-slate-100 text-center flex justify-center items-center">
            <a
              href="https://ai-resume-copilot-three.vercel.app/?ref=user_resume_share"
              target="_blank"
              rel="noopener noreferrer"
              className="text-[10px] font-bold uppercase tracking-widest text-slate-400 hover:text-blue-500 transition duration-300 pointer-events-auto decoration-none"
              style={{
                display: "inline-block",
                padding: "4px 8px",
                border: "1px solid #f1f5f9",
                borderRadius: "6px",
                backgroundColor: "#f8fafc",
              }}
            >
              Built with AI Resume CoPilot
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

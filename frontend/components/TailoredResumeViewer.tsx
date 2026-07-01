"use client";

import { useRef, useState } from "react";
import ReactMarkdown from "react-markdown";

export default function TailoredResumeViewer({ markdownContent }: { markdownContent: string }) {
  const resumeRef = useRef<HTMLDivElement>(null);
  const [downloading, setDownloading] = useState(false);

  const handleDownloadPdf = async () => {
    if (!resumeRef.current) return;
    setDownloading(true);
    
    try {
      // Dynamically import html2pdf so it doesn't break SSR
      const html2pdf = (await import("html2pdf.js")).default;
      
      const opt: any = {
        margin:       10, // mm
        filename:     'Tailored_Resume.pdf',
        image:        { type: 'jpeg', quality: 0.98 },
        html2canvas:  { scale: 2, useCORS: true, logging: false },
        jsPDF:        { unit: 'mm', format: 'a4', orientation: 'portrait' }
      };

      // Wrap the content in a container that forces standard A4 styling for PDF
      const container = document.createElement('div');
      container.innerHTML = resumeRef.current.innerHTML;
      
      // Apply strict styling for the PDF generation so it looks like a real resume
      container.style.padding = '20px';
      container.style.fontFamily = 'Arial, sans-serif';
      container.style.color = '#000';
      container.style.lineHeight = '1.4';
      container.style.fontSize = '12px';
      
      // Hide the container from the viewport
      container.style.position = 'absolute';
      container.style.top = '-9999px';
      document.body.appendChild(container);
      
      await html2pdf().set(opt).from(container).save();
      
      // Cleanup
      document.body.removeChild(container);
    } catch (err) {
      console.error("PDF generation failed:", err);
      alert("Failed to generate PDF. Please try copying the text instead.");
    } finally {
      setDownloading(false);
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(markdownContent);
    alert("Copied Markdown to clipboard!");
  };

  return (
    <div className="bg-white border border-gray-200 rounded-xl shadow-sm overflow-hidden flex flex-col">
      <div className="bg-gray-50 border-b border-gray-200 px-4 py-3 flex justify-between items-center">
        <h3 className="font-bold text-gray-800 flex items-center gap-2">
          <span className="text-xl">✨</span> Your Tailored Resume
        </h3>
        <div className="flex gap-2">
          <button 
            onClick={handleCopy}
            className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-gray-300 bg-white text-gray-700 hover:bg-gray-50"
          >
            Copy Markdown
          </button>
          <button 
            onClick={handleDownloadPdf}
            disabled={downloading}
            className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 flex items-center gap-2"
          >
            {downloading ? (
              <>
                <span className="animate-spin inline-block w-3 h-3 border-2 border-white border-t-transparent rounded-full"></span>
                Generating...
              </>
            ) : "Download PDF"}
          </button>
        </div>
      </div>
      
      {/* Scrollable Viewer Area */}
      <div className="p-6 md:p-8 bg-gray-100 max-h-[700px] overflow-y-auto">
        <div 
          ref={resumeRef} 
          className="resume-preview bg-white shadow-md mx-auto max-w-[800px] p-8 md:p-12 prose prose-sm md:prose-base prose-slate text-gray-800"
          style={{ minHeight: "1056px" /* Approx A4 aspect ratio height at 800px width */ }}
        >
          <ReactMarkdown>{markdownContent}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}

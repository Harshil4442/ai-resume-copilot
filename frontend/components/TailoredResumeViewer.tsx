"use client";

export default function TailoredResumeViewer({ markdownContent, pdfBase64 }: { markdownContent: string, pdfBase64?: string | null }) {
  const pdfUrl = pdfBase64 ? `data:application/pdf;base64,${pdfBase64}` : null;

  const handleDownloadPdf = () => {
    if (pdfUrl) {
      const link = document.createElement("a");
      link.href = pdfUrl;
      link.download = "Tailored_Resume.pdf";
      link.click();
    } else {
      alert("PDF not available. Compilation may have failed.");
    }
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(markdownContent);
    alert("Copied raw LaTeX to clipboard!");
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
            Copy Raw LaTeX
          </button>
          <button 
            onClick={handleDownloadPdf}
            disabled={!pdfUrl}
            className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-primary text-white hover:bg-primary/90 disabled:opacity-50 flex items-center gap-2"
          >
            Download PDF
          </button>
        </div>
      </div>
      
      {/* Scrollable Viewer Area */}
      <div className="p-6 md:p-8 bg-surface h-[800px]">
        {pdfUrl ? (
          <iframe 
            src={`${pdfUrl}#view=FitH`}
            className="w-full h-full shadow-md bg-white border-0 rounded-md"
            title="Tailored Resume PDF"
          />
        ) : (
          <div className="w-full h-full flex flex-col items-center justify-center text-gray-500 bg-white shadow-sm rounded-md p-8">
            <span className="text-sm font-semibold mb-2">PDF Compilation Failed or Pending</span>
            <span className="text-xs text-center max-w-md">The server was unable to return a compiled PDF. You can still copy the raw LaTeX and compile it yourself (e.g. on Overleaf).</span>
          </div>
        )}
      </div>
    </div>
  );
}

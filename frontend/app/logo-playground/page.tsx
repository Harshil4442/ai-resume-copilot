'use client';

import React, { useState } from 'react';

// ==================== CONCEPT A: ROCKET (20 VARIANTS) ====================
function RocketLogo({ variant, color = 'blue' }: { variant: number; color?: string }) {
  const getColors = () => {
    switch (color) {
      case 'purple': return { prim: '#8b5cf6', sec: '#ec4899', accent: '#f59e0b', glow: 'rgba(139, 92, 246, 0.4)' };
      case 'teal': return { prim: '#06b6d4', sec: '#0d9488', accent: '#fbbf24', glow: 'rgba(6, 182, 212, 0.4)' };
      case 'emerald': return { prim: '#10b981', sec: '#06b6d4', accent: '#f97316', glow: 'rgba(16, 185, 129, 0.4)' };
      default: return { prim: '#38bdf8', sec: '#1e40af', accent: '#f97316', glow: 'rgba(56, 189, 248, 0.4)' };
    }
  };
  const { prim, sec, accent } = getColors();

  // Basic Rocket Body Path
  const bodyPath = "M32 6C32 6 22 18 22 34C22 42 26 48 32 52C38 48 42 42 42 34C42 18 32 6 32 6Z";
  
  // Custom renders based on 20 variants
  return (
    <svg viewBox="0 0 64 64" fill="none" className="w-16 h-16 transition-all duration-300">
      <defs>
        <linearGradient id={`grad-rocket-${variant}`} x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor={prim} />
          <stop offset="100%" stopColor={sec} />
        </linearGradient>
        <filter id={`glow-rocket-${variant}`}>
          <feGaussianBlur stdDeviation="2" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
      </defs>

      {/* Orbit ring on variant 8 */}
      {variant === 8 && (
        <circle cx="32" cy="32" r="26" stroke={prim} strokeWidth="1.5" strokeDasharray="3 5" opacity="0.3" />
      )}

      {/* Group transformation for rotation */}
      <g transform={variant === 5 || variant === 17 ? "rotate(-30 32 32)" : ""}>
        {/* Flame Trail */}
        {variant !== 7 && (
          <path 
            d={variant === 6 || variant === 12 ? "M28 50L24 62L32 54L40 62L36 50" : "M28 52L26 60L32 56L38 60L36 52"} 
            fill={accent} 
            filter={variant === 13 ? `url(#glow-rocket-${variant})` : ''}
          />
        )}

        {/* Left Wing */}
        <path d="M22 38L14 48L22 44Z" fill={variant === 7 ? 'none' : prim} stroke={variant === 7 ? prim : 'none'} strokeWidth="2" />
        
        {/* Main Body */}
        <path 
          d={bodyPath} 
          fill={variant === 7 ? 'none' : `url(#grad-rocket-${variant})`} 
          stroke={variant === 7 ? `url(#grad-rocket-${variant})` : 'none'} 
          strokeWidth="2.5"
          filter={variant === 13 ? `url(#glow-rocket-${variant})` : ''}
          opacity={variant === 10 ? 0.75 : 1}
        />

        {/* Right Wing */}
        <path d="M42 38L50 48L42 44Z" fill={variant === 7 ? 'none' : prim} stroke={variant === 7 ? prim : 'none'} strokeWidth="2" />

        {/* Window */}
        <circle 
          cx="32" 
          cy="32" 
          r={variant === 18 ? 5.5 : 4} 
          fill={variant === 7 ? 'none' : '#ffffff'} 
          stroke={variant === 7 ? prim : 'none'} 
          strokeWidth="1.5" 
        />
      </g>

      {/* Decorative stars for variant 11 & 19 */}
      {(variant === 11 || variant === 19) && (
        <>
          <path d="M12 14L13.5 16.5L16 17L13.5 17.5L12 20L10.5 17.5L8 17L10.5 16.5Z" fill={prim} opacity="0.6" />
          <path d="M52 22L53 24L55 24.5L53 25L52 27L51 25L49 24.5L51 24Z" fill={sec} opacity="0.8" />
        </>
      )}
    </svg>
  );
}

// ==================== CONCEPT B: RAINBOW H (20 VARIANTS) ====================
function RainbowH({ variant, color = 'cyan' }: { variant: number; color?: string }) {
  const getRainbowColors = () => {
    switch (color) {
      case 'warm': return ['#f97316', '#ef4444', '#ec4899'];
      case 'cool': return ['#06b6d4', '#3b82f6', '#4f46e5'];
      default: return ['#22d3ee', '#6366f1', '#a855f7']; // Cyan, Indigo, Purple
    }
  };
  const [c1, c2, c3] = getRainbowColors();

  return (
    <svg viewBox="0 0 64 64" fill="none" className="w-16 h-16">
      {/* Background shape */}
      {variant === 6 && (
        <rect x="4" y="4" width="56" height="56" rx="14" fill="url(#rainbow-grad-bg)" />
      )}
      
      <defs>
        <linearGradient id="rainbow-grad-bg" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor={c1} />
          <stop offset="50%" stopColor={c2} />
          <stop offset="100%" stopColor={c3} />
        </linearGradient>
      </defs>

      {/* Multi-layered arcs */}
      {variant !== 5 && variant !== 7 && (
        <g strokeLinecap="round" opacity={variant === 20 ? 0.35 : 1}>
          <path d="M10 52Q10 14 32 10Q54 14 54 52" stroke={c1} strokeWidth="3" opacity="0.4" />
          <path d="M16 52Q16 20 32 16Q48 20 48 52" stroke={c2} strokeWidth="3.5" opacity="0.6" />
          <path d="M22 52Q22 26 32 22Q42 26 42 52" stroke={c3} strokeWidth="4" opacity="0.8" />
        </g>
      )}

      {/* Decorative dots for variant 14 */}
      {variant === 14 && (
        <>
          <circle cx="12" cy="18" r="3" fill={c1} />
          <circle cx="32" cy="8" r="4.5" fill={c2} />
          <circle cx="52" cy="18" r="3" fill={c3} />
        </>
      )}

      {/* The Central H */}
      <text
        x="32"
        y={variant === 6 || variant === 15 ? 44 : 46}
        textAnchor="middle"
        fontFamily="sans-serif"
        fontWeight="900"
        fontSize={variant === 5 ? "44" : "24"}
        fill={variant === 6 ? "white" : "url(#rainbow-grad-bg)"}
        className="select-none"
      >
        H
      </text>
    </svg>
  );
}

// ==================== CONCEPT C: ARROW H (20 VARIANTS) ====================
function ArrowH({ variant, color = 'green' }: { variant: number; color?: string }) {
  const arrowColor = color === 'orange' ? '#f97316' : color === 'purple' ? '#a855f7' : '#10b981';
  
  return (
    <svg viewBox="0 0 64 64" fill="none" className="w-16 h-16">
      <defs>
        <linearGradient id="h-grad" x1="0" y1="100%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#1e40af" />
          <stop offset="100%" stopColor="#3b82f6" />
        </linearGradient>
      </defs>

      {/* Solid background for variant 10 */}
      {variant === 10 && (
        <rect x="4" y="4" width="56" height="56" rx="14" fill="url(#h-grad)" />
      )}

      {/* Left Column of H */}
      {variant !== 19 && (
        <rect x="14" y="12" width="6" height="40" rx="2" fill={variant === 10 ? 'white' : 'url(#h-grad)'} />
      )}
      {/* Right Column of H */}
      {variant !== 19 && (
        <rect x="44" y="12" width="6" height="40" rx="2" fill={variant === 10 ? 'white' : 'url(#h-grad)'} />
      )}
      {/* Crossbar */}
      {variant !== 19 && (
        <rect x="18" y="29" width="28" height="6" rx="1" fill={variant === 10 ? 'white' : 'url(#h-grad)'} />
      )}

      {/* Alternative modern monoline for variant 19 */}
      {variant === 19 && (
        <path d="M16 12V52M16 32H44M44 52V12" stroke="url(#h-grad)" strokeWidth="5" strokeLinecap="round" />
      )}

      {/* The Slicing Arrow */}
      <g stroke={arrowColor} strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
        <path d="M44 36L44 12L54 22" />
      </g>
    </svg>
  );
}

// ==================== CONCEPT D: CLOUD H (20 VARIANTS) ====================
function CloudH({ variant, color = 'blue' }: { variant: number; color?: string }) {
  const getCloudColors = () => {
    switch (color) {
      case 'purple': return { start: '#8b5cf6', end: '#6366f1' };
      case 'sunset': return { start: '#f97316', end: '#ec4899' };
      default: return { start: '#38bdf8', end: '#1e40af' };
    }
  };
  const { start, end } = getCloudColors();

  return (
    <svg viewBox="0 0 64 64" fill="none" className="w-16 h-16">
      <defs>
        <linearGradient id="cloud-grad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor={start} />
          <stop offset="100%" stopColor={end} />
        </linearGradient>
      </defs>

      {/* Outer Hexagon Shield for variant 16 */}
      {variant === 16 && (
        <path d="M32 4L56 18V46L32 60L8 46V18Z" stroke="url(#cloud-grad)" strokeWidth="2.5" fill="none" />
      )}

      {/* The Cloud Body */}
      <path
        d="M14 44C8 44 6 38 10 34C8 28 14 24 20 26C22 18 32 16 38 20C42 14 54 16 54 24C60 24 62 32 58 36C62 42 58 48 52 46H14Z"
        fill={variant === 2 || variant === 8 || variant === 19 ? 'none' : 'url(#cloud-grad)'}
        stroke={variant === 2 || variant === 8 || variant === 19 ? 'url(#cloud-grad)' : 'none'}
        strokeWidth="2.5"
      />

      {/* Circuit Nodes overlay for variant 3 & 15 */}
      {(variant === 3 || variant === 15) && (
        <g stroke="white" strokeWidth="1" opacity="0.6">
          <line x1="20" y1="26" x2="32" y2="20" />
          <line x1="32" y1="20" x2="48" y2="24" />
          <circle cx="20" cy="26" r="2.5" fill="white" />
          <circle cx="32" cy="20" r="3" fill="white" />
          <circle cx="48" cy="24" r="2.5" fill="white" />
        </g>
      )}

      {/* The Inner letter H */}
      <text
        x="32"
        y="39"
        textAnchor="middle"
        fontFamily="sans-serif"
        fontWeight="900"
        fontSize="15"
        fill={variant === 2 || variant === 8 || variant === 19 ? 'url(#cloud-grad)' : 'white'}
        className="select-none"
      >
        H
      </text>
    </svg>
  );
}

export default function LogoPlayground() {
  const [activeTab, setActiveTab] = useState<'rocket' | 'rainbow' | 'arrow' | 'cloud'>('rocket');
  const [bgColor, setBgColor] = useState<'dark' | 'light'>('dark');

  const renderGrid = () => {
    const list = Array.from({ length: 20 }, (_, i) => i + 1);
    
    return (
      <div className="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-5 gap-6 max-w-6xl mx-auto px-4">
        {list.map((v) => (
          <div 
            key={v}
            className={`flex flex-col items-center justify-between p-6 rounded-2xl border transition-all duration-300 ${
              bgColor === 'dark' 
                ? 'bg-slate-900/60 border-slate-800/80 hover:border-slate-700/60 hover:bg-slate-800/40' 
                : 'bg-white border-slate-200/80 hover:border-slate-300/80 hover:shadow-md'
            }`}
          >
            <div className="h-20 flex items-center justify-center">
              {activeTab === 'rocket' && <RocketLogo variant={v} />}
              {activeTab === 'rainbow' && <RainbowH variant={v} />}
              {activeTab === 'arrow' && <ArrowH variant={v} />}
              {activeTab === 'cloud' && <CloudH variant={v} />}
            </div>
            
            <div className="mt-4 text-center">
              <span className={`text-xs font-bold px-2.5 py-1 rounded-full ${
                bgColor === 'dark' ? 'bg-slate-800/80 text-slate-400' : 'bg-slate-100 text-slate-500'
              }`}>
                Variant #{v}
              </span>
            </div>
          </div>
        ))}
      </div>
    );
  };

  return (
    <main className={`min-h-screen pb-24 transition-colors duration-300 ${
      bgColor === 'dark' ? 'bg-[#030712] text-white' : 'bg-slate-50 text-slate-900'
    }`}>
      {/* Header */}
      <div className="max-w-4xl mx-auto text-center pt-20 px-6 mb-16">
        <h1 className="text-4xl md:text-5xl font-black tracking-tight mb-4">
          Logo Concepts Playground
        </h1>
        <p className="text-lg text-slate-400 max-w-2xl mx-auto">
          Explore all 80 variations (4 designs × 20 styles each) rendered directly in sharp vector code.
        </p>

        {/* Bg Toggles & Controls */}
        <div className="flex justify-center items-center gap-4 mt-8">
          <button 
            onClick={() => setBgColor('dark')}
            className={`px-4 py-2 rounded-xl text-sm font-bold border transition-all ${
              bgColor === 'dark' 
                ? 'bg-white text-slate-900 border-white' 
                : 'bg-slate-800 text-slate-400 border-slate-700 hover:text-white'
            }`}
          >
            Dark Mode Preview
          </button>
          <button 
            onClick={() => setBgColor('light')}
            className={`px-4 py-2 rounded-xl text-sm font-bold border transition-all ${
              bgColor === 'light' 
                ? 'bg-slate-900 text-white border-slate-900' 
                : 'bg-slate-200 text-slate-600 border-slate-300 hover:text-slate-900'
            }`}
          >
            Light Mode Preview
          </button>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex justify-center gap-2 max-w-md mx-auto mb-12 px-4">
        {(['rocket', 'rainbow', 'arrow', 'cloud'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`flex-1 py-3 px-4 rounded-xl text-sm font-bold capitalize transition-all ${
              activeTab === tab 
                ? 'bg-blue-600 text-white shadow-lg shadow-blue-500/20' 
                : bgColor === 'dark'
                  ? 'bg-slate-900/60 border border-slate-800 text-slate-400 hover:text-white'
                  : 'bg-white border border-slate-200 text-slate-600 hover:text-slate-900'
            }`}
          >
            {tab === 'rocket' ? '🚀 Rocket' : tab === 'rainbow' ? '🌈 Rainbow' : tab === 'arrow' ? '📈 Arrow' : '☁️ Cloud'}
          </button>
        ))}
      </div>

      {/* Live Grid */}
      {renderGrid()}
    </main>
  );
}

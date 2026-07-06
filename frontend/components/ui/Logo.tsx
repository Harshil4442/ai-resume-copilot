import { SVGProps } from "react";

export default function Logo({ className, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      width="32"
      height="32"
      viewBox="0 0 32 32"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <defs>
        <linearGradient id="logo-grad" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" stopColor="#3b82f6" /> {/* Electric Blue */}
          <stop offset="50%" stopColor="#6366f1" />  {/* Indigo */}
          <stop offset="100%" stopColor="#a855f7" /> {/* Purple */}
        </linearGradient>
        <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="2" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
      </defs>
      
      {/* Outer Hexagon Shield */}
      <path
        d="M16 2L28 9V23L16 30L4 23V9L16 2Z"
        stroke="url(#logo-grad)"
        strokeWidth="2.5"
        strokeLinejoin="round"
        className="opacity-80"
      />
      
      {/* Wizard Hat Icon inside Shield */}
      <path
        d="M16 7L7 19H25L16 7Z"
        fill="url(#logo-grad)"
        filter="url(#glow)"
        className="opacity-90"
      />
      
      {/* Hat Brim */}
      <path
        d="M5 21C5 20.4477 5.44772 20 6 20H26C26.5523 20 27 20.4477 27 21C27 21.5523 26.5523 22 26 22H6C5.44772 22 5 21.5523 5 21Z"
        fill="url(#logo-grad)"
      />
      
      {/* Sparkle/Star representing magic insights */}
      <path
        d="M16 11.5L17 13.5L19 14.5L17 15.5L16 17.5L15 15.5L13 14.5L15 13.5L16 11.5Z"
        fill="white"
      />
    </svg>
  );
}

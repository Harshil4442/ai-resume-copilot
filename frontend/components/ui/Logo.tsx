import { SVGProps } from "react";

export default function Logo({ className, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      width="34"
      height="34"
      viewBox="0 0 100 100"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      {...props}
    >
      <defs>
        {/* Electric Blue -> Purple -> Cyan Gradient matching the uploaded logo */}
        <linearGradient id="logo-grad" x1="0%" y1="100%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="#4f46e5" />  {/* Deep Indigo/Purple */}
          <stop offset="50%" stopColor="#2563eb" />  {/* Royal Blue */}
          <stop offset="100%" stopColor="#06b6d4" /> {/* Bright Cyan */}
        </linearGradient>
        <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="1.5" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
      </defs>

      {/* 1. Left stem of H (Styled as a folded Document) */}
      <path
        d="M22 74V28L30 20H38V74H22Z"
        fill="url(#logo-grad)"
      />
      {/* Dog-ear fold highlight */}
      <path
        d="M22 28H30V20L22 28Z"
        fill="white"
        opacity="0.35"
      />

      {/* 2. Right stem of H */}
      <rect
        x="54"
        y="20"
        width="16"
        height="54"
        rx="2"
        fill="url(#logo-grad)"
      />

      {/* 3. Crossbar of H */}
      <rect
        x="38"
        y="42"
        width="16"
        height="12"
        fill="url(#logo-grad)"
      />

      {/* 4. Swooping Jet Stream */}
      <path
        d="M14 54C14 36, 44 30, 68 48"
        stroke="url(#logo-grad)"
        strokeWidth="4"
        strokeLinecap="round"
        fill="none"
      />

      {/* 5. Tilted Rocket (translated and rotated 45 degrees to match direction) */}
      <g transform="translate(68, 38) rotate(45)">
        {/* Rocket Flame */}
        <path
          d="M-3 12L0 22L3 12Z"
          fill="#fb923c"
          filter="url(#glow)"
        />
        {/* Left Fin */}
        <path
          d="M-6 4L-12 12L-4 10Z"
          fill="url(#logo-grad)"
        />
        {/* Right Fin */}
        <path
          d="M6 4L12 12L4 10Z"
          fill="url(#logo-grad)"
        />
        {/* Rocket Body */}
        <path
          d="M-6 0C-6 -10, 0 -18, 0 -18C0 -18, 6 -10, 6 0L4 12H-4L-6 0Z"
          fill="url(#logo-grad)"
        />
        {/* Window */}
        <circle
          cx="0"
          cy="-3"
          r="2"
          fill="white"
        />
      </g>
    </svg>
  );
}

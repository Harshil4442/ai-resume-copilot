"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { useSession } from "next-auth/react";
import { apiGet } from "../lib/api";

const links = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/resume", label: "Resume" },
  { href: "/jobs", label: "Match" },
  { href: "/market", label: "Market" },
  { href: "/learning", label: "Learning" },
  { href: "/profile", label: "Profile" },
  { href: "/billing", label: "Billing" },
];

export default function Nav() {
  const [loggedIn, setLoggedIn] = useState<boolean>(false);
  const [credits, setCredits] = useState<number | null>(null);
  const [tier, setTier] = useState<string>("free");
  const pathname = usePathname();
  const { data: session, status } = useSession();

  useEffect(() => {
    if (status === "authenticated" && (session as any)?.user?.accessToken) {
      localStorage.setItem("access_token", (session as any).user.accessToken);
      setLoggedIn(true);
    } else if (status === "unauthenticated") {
      localStorage.removeItem("access_token");
      setLoggedIn(false);
      setCredits(null);
      setTier("free");
    }
  }, [session, status]);

  useEffect(() => {
    const fetchProfile = () => {
      if (loggedIn) {
        apiGet<any>("/auth/profile")
          .then(data => {
            setCredits(data.ai_credits ?? 0);
            setTier(data.tier ?? "free");
          })
          .catch(console.error);
      }
    };
    fetchProfile();
    
    window.addEventListener("refresh_credits", fetchProfile);
    return () => window.removeEventListener("refresh_credits", fetchProfile);
  }, [loggedIn, pathname]);


  return (
    <header className="sticky top-0 z-30 glass-nav">
      <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between gap-4">
        <a href="/" className="group flex items-center gap-3">
          <span className="grid h-9 w-9 place-items-center rounded-lg bg-neutral-950 text-white text-xs font-black shadow-[0_14px_35px_rgba(15,23,42,0.18)]">AI</span>
          <span>
            <span className="block text-sm font-black tracking-tight text-slate-950">Resume CoPilot</span>
            <span className="block text-[10px] font-bold uppercase tracking-[0.22em] text-slate-400">Career OS</span>
          </span>
        </a>
        <nav className="flex gap-1 text-sm items-center overflow-x-auto rounded-full border border-slate-200/70 bg-white/70 p-1 backdrop-blur-xl">
          {links.map((link) => {
            const active = pathname === link.href;
            return (
              <a
                key={link.href}
                className={`relative px-3.5 py-1.5 rounded-full font-bold transition duration-300 ${
                  active
                    ? "bg-neutral-950 text-white shadow-sm"
                    : "text-slate-700 hover:bg-slate-100 hover:text-slate-950"
                }`}
                href={link.href}
              >
                {link.label}
              </a>
            );
          })}

          {loggedIn && credits !== null && (
            <div className="flex items-center gap-1.5 px-3 py-1 bg-slate-100 rounded-full border border-slate-200/60 mr-1 cursor-help group relative">
              <span className="text-xs font-black text-slate-700">⚡</span>
              <span className="text-xs font-bold text-slate-800">
                {tier === "premium" ? "∞" : credits}
              </span>
              <div className="absolute top-full mt-2 right-0 w-48 p-2 bg-slate-900 text-white text-[10px] rounded-lg shadow-xl opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50">
                {tier === "premium" ? "Premium Active: Unlimited AI Operations" : `${credits} AI Operations Remaining`}
              </div>
            </div>
          )}
          {loggedIn ? (
            <a className="px-3.5 py-1.5 rounded-full bg-neutral-950 text-white font-bold hover:bg-black transition" href="/logout">Logout</a>
          ) : (
            <a className="px-3.5 py-1.5 rounded-full bg-neutral-950 text-white font-bold hover:bg-black transition" href="/login">Login</a>
          )}
        </nav>
      </div>
    </header>
  );
}

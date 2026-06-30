"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { useSession } from "next-auth/react";

const links = [
  { href: "/dashboard", label: "Dashboard" },
  { href: "/resume", label: "Resume" },
  { href: "/jobs", label: "Match" },
  { href: "/market", label: "Market" },
  { href: "/learning", label: "Learning" },
  { href: "/profile", label: "Profile" },
];

export default function Nav() {
  const [loggedIn, setLoggedIn] = useState<boolean>(false);
  const pathname = usePathname();
  const { data: session, status } = useSession();

  useEffect(() => {
    if (status === "authenticated" && (session as any)?.user?.accessToken) {
      localStorage.setItem("access_token", (session as any).user.accessToken);
      setLoggedIn(true);
    } else if (status === "unauthenticated") {
      localStorage.removeItem("access_token");
      setLoggedIn(false);
    }
  }, [session, status]);


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

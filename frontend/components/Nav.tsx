"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { useSession } from "next-auth/react";
import { apiGet } from "../lib/api";
import Link from "next/link";
import Logo from "./ui/Logo";
import { Menu, X, LayoutDashboard, FileText, Target, TrendingUp, BookOpen, User, CreditCard } from "lucide-react";
import { motion, AnimatePresence } from "framer-motion";

const links = [
  { href: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { href: "/resume", label: "Resume", icon: FileText },
  { href: "/jobs", label: "Match", icon: Target },
  { href: "/market", label: "Market", icon: TrendingUp },
  { href: "/learning", label: "Learning", icon: BookOpen },
  { href: "/profile", label: "Profile", icon: User },
  { href: "/billing", label: "Billing", icon: CreditCard },
];

export default function Nav() {
  const [loggedIn, setLoggedIn] = useState<boolean>(false);
  const [credits, setCredits] = useState<number | null>(null);
  const [tier, setTier] = useState<string>("free");
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  
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

  // Close mobile menu on route change
  useEffect(() => {
    setIsMobileMenuOpen(false);
  }, [pathname]);

  return (
    <>
      <header className="fixed left-0 top-0 z-50 w-full h-14 bg-slate-900/70 backdrop-blur-[12px] border-b border-slate-700/50 animate-fade-in [--animation-delay:600ms] translate-y-[-1rem] opacity-0 shadow-sm">
        <div className="max-w-[80rem] mx-auto px-4 md:px-8 h-full flex items-center justify-between">
          <Link href="/" className="flex items-center gap-2.5">
            <Logo />
            <span className="text-sm font-black tracking-tight text-white">HireWiz</span>
          </Link>
          
          {/* Desktop Nav */}
          <nav className="hidden md:flex items-center gap-1 text-sm bg-slate-900/50 rounded-full border border-slate-700/50 p-1">
            {links.map((link) => {
              const active = pathname === link.href;
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={`relative px-3 py-1.5 rounded-full font-semibold transition-colors duration-200 flex items-center gap-1.5 ${
                    active
                      ? "bg-slate-950 text-white shadow-sm border border-slate-700/50"
                      : "text-slate-400 hover:text-white hover:bg-slate-800/50"
                  }`}
                >
                  <link.icon size={14} className={active ? "text-primary" : ""} />
                  {link.label}
                </Link>
              );
            })}
          </nav>

          <div className="flex items-center gap-3">
            {loggedIn && credits !== null && (
              <div className="hidden sm:flex items-center gap-1.5 px-3 py-1.5 bg-slate-800 rounded-full border border-slate-700/60 cursor-help group relative">
                <span className="text-xs font-black text-amber-500">⚡</span>
                <span className="text-xs font-bold text-slate-200">
                  {tier === "premium" ? "∞" : credits}
                </span>
                <div className="absolute top-full mt-2 right-0 w-48 p-2 bg-slate-900 text-white text-[10px] rounded-lg shadow-xl opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
                  {tier === "premium" ? "Premium Active: Unlimited AI Operations" : `${credits} AI Operations Remaining`}
                </div>
              </div>
            )}
            
            {loggedIn ? (
              <Link className="hidden md:inline-flex px-4 py-1.5 rounded-xl bg-slate-900 text-white text-sm font-semibold hover:bg-slate-800 transition shadow-sm" href="/logout">Logout</Link>
            ) : (
              <Link className="hidden md:inline-flex px-4 py-1.5 rounded-xl bg-primary text-white text-sm font-semibold hover:bg-primary/90 transition shadow-sm" href="/login">Login</Link>
            )}

            {/* Mobile Menu Toggle */}
            <button 
              className="md:hidden p-2 text-slate-300 hover:text-white transition-colors rounded-md hover:bg-slate-800"
              onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
            >
              {isMobileMenuOpen ? <X size={20} /> : <Menu size={20} />}
            </button>
          </div>
        </div>
      </header>

      {/* Mobile Drawer Menu */}
      <AnimatePresence>
        {isMobileMenuOpen && (
          <>
            <motion.div 
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 bg-slate-900/20 backdrop-blur-sm z-40 md:hidden"
              onClick={() => setIsMobileMenuOpen(false)}
            />
            <motion.div
              initial={{ x: "100%" }}
              animate={{ x: 0 }}
              exit={{ x: "100%" }}
              transition={{ type: "spring", bounce: 0, duration: 0.4 }}
              className="fixed top-14 right-0 bottom-0 w-64 bg-slate-950 border-l border-slate-700 z-40 md:hidden shadow-2xl flex flex-col"
            >
              <div className="p-4 flex-1 overflow-y-auto">
                <div className="flex flex-col gap-1">
                  {links.map((link) => {
                    const active = pathname === link.href;
                    return (
                      <Link
                        key={link.href}
                        href={link.href}
                        className={`flex items-center gap-3 px-4 py-3 rounded-xl font-semibold transition-colors ${
                          active 
                            ? "bg-slate-900/50 text-white" 
                            : "text-slate-400 hover:bg-slate-900/50 hover:text-white"
                        }`}
                      >
                        <link.icon size={18} className={active ? "text-primary" : ""} />
                        {link.label}
                      </Link>
                    );
                  })}
                </div>
              </div>
              
              <div className="p-4 border-t border-slate-800">
                {loggedIn && credits !== null && (
                  <div className="flex items-center justify-between px-4 py-3 bg-slate-900/50 rounded-xl mb-4">
                    <span className="text-sm font-semibold text-slate-300 flex items-center gap-2">
                      <span className="text-amber-500">⚡</span> Credits
                    </span>
                    <span className="font-bold text-white">{tier === "premium" ? "∞" : credits}</span>
                  </div>
                )}
                {loggedIn ? (
                  <Link className="flex w-full justify-center px-4 py-2.5 rounded-xl bg-slate-900 text-white text-sm font-semibold hover:bg-slate-800 transition" href="/logout">Logout</Link>
                ) : (
                  <Link className="flex w-full justify-center px-4 py-2.5 rounded-xl bg-primary text-white text-sm font-semibold hover:bg-primary/90 transition" href="/login">Login</Link>
                )}
              </div>
            </motion.div>
          </>
        )}
      </AnimatePresence>
    </>
  );
}

"use client";

import { useEffect, useState } from "react";
import { isLoggedIn } from "../lib/auth";

export default function Nav() {
  const [loggedIn, setLoggedIn] = useState(false);

  useEffect(() => {
    setLoggedIn(isLoggedIn());
  }, []);

  return (
    <header className="sticky top-0 z-30 border-b border-gray-200 bg-white/90 backdrop-blur">
      <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between gap-4">
        <a href="/" className="font-black tracking-tight text-gray-950">AI Resume CoPilot</a>
        <nav className="flex gap-1 text-sm items-center overflow-x-auto">
          <a className="px-3 py-1.5 rounded-lg hover:bg-gray-100" href="/dashboard">Dashboard</a>
          <a className="px-3 py-1.5 rounded-lg hover:bg-gray-100" href="/resume">Resume</a>
          <a className="px-3 py-1.5 rounded-lg hover:bg-gray-100" href="/jobs">Match</a>
          <a className="px-3 py-1.5 rounded-lg hover:bg-gray-100" href="/market">Market</a>
          <a className="px-3 py-1.5 rounded-lg hover:bg-gray-100" href="/learning">Learning</a>
          <a className="px-3 py-1.5 rounded-lg hover:bg-gray-100" href="/profile">Profile</a>

          {loggedIn ? (
            <a className="px-3 py-1.5 rounded-lg bg-gray-950 text-white hover:bg-black" href="/logout">Logout</a>
          ) : (
            <a className="px-3 py-1.5 rounded-lg bg-gray-950 text-white hover:bg-black" href="/login">Login</a>
          )}
        </nav>
      </div>
    </header>
  );
}

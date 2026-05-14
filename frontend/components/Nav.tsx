"use client";

import { useEffect, useState } from "react";
import { isLoggedIn } from "../lib/auth";

export default function Nav() {
  const [loggedIn, setLoggedIn] = useState(false);

  useEffect(() => {
    setLoggedIn(isLoggedIn());
  }, []);

  return (
    <header className="border-b bg-white">
      <div className="max-w-5xl mx-auto px-4 py-3 flex items-center justify-between">
        <a href="/" className="font-bold">AI Resume CoPilot</a>
        <nav className="flex gap-4 text-sm items-center">
          <a className="hover:underline" href="/resume">Resume</a>
          <a className="hover:underline" href="/jobs">Match</a>
          <a className="hover:underline" href="/learning">Learning</a>
          <a className="hover:underline" href="/dashboard">Dashboard</a>
          <a className="hover:underline" href="/profile">Profile</a>

          {loggedIn ? (
            <a className="hover:underline" href="/logout">Logout</a>
          ) : (
            <a className="hover:underline" href="/login">Login</a>
          )}
        </nav>
      </div>
    </header>
  );
}

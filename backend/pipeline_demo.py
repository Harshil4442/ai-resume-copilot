#!/usr/bin/env python3
"""
End-to-end demo pipeline for HireWiz.

Usage:
    python pipeline_demo.py --resume data/resume_demo.pdf --jd data/jd_demo.txt --tone impactful
"""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from app.services.parsing import parse_resume_file
from app.services.matching import extract_required_skills_from_jd, compute_match_score
from app.services.recommender import recommend_for_gaps
from app.services.llm_client import rewrite_bullets, generate_interview_questions


def load_file_bytes(path: Path) -> bytes:
    return path.read_bytes()


def load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="HireWiz demo pipeline")
    parser.add_argument("--resume", type=str, required=True, help="Path to resume PDF")
    parser.add_argument("--jd", type=str, required=True, help="Path to job description txt file")
    parser.add_argument("--tone", type=str, default="concise", help="Tone for LLM rewrite")
    args = parser.parse_args()

    resume_path = Path(args.resume)
    jd_path = Path(args.jd)

    if not resume_path.exists():
        raise SystemExit(f"Resume file not found: {resume_path}")
    if not jd_path.exists():
        raise SystemExit(f"JD file not found: {jd_path}")

    print("=== 1) Parsing resume ===")
    resume_bytes = load_file_bytes(resume_path)
    raw_text, sections, skills, exp_years = parse_resume_file(resume_bytes)
    print(f"Experience (estimated): {exp_years:.1f} years")
    print(f"Resume skills ({len(skills)}): {', '.join(skills) or 'None'}")
    print(f"Sections detected: {', '.join(sections.keys())}\n")

    print("=== 2) Parsing JD ===")
    jd_text = load_text(jd_path)
    jd_skills = extract_required_skills_from_jd(jd_text)
    print(f"JD skills ({len(jd_skills)}): {', '.join(jd_skills) or 'None'}\n")

    print("=== 3) Match score ===")
    score, missing, weak = compute_match_score(skills, jd_skills)
    print(f"Match score: {score:.1f} / 100")
    print(f"Missing skills: {', '.join(missing) or 'None'}")
    print(f"Weak skills: {', '.join(weak) or 'None'}\n")

    print("=== 4) Learning roadmap ===")
    roadmap = recommend_for_gaps(missing)
    for i, r in enumerate(roadmap[:10], start=1):
        print(f"{i}. {r['title']} ({r.get('level','N/A')} • {r.get('type','resource')})") 
        print(f"   URL: {r['url']}")
        print(f"   Covers: {', '.join(r.get('skills', []))}\n")
    if not roadmap:
        print("No roadmap items found.\n")

    print("=== 5) LLM rewrite ===")
    llm_rewrite = rewrite_bullets(raw_text, jd_text, args.tone)
    bullets = llm_rewrite.get("bullets", [])
    summary = llm_rewrite.get("summary", "")
    print("Summary:", summary[:500], "\n")
    print("Sample bullets:")
    for b in bullets[:5]:
        print("-", b)
    print()

    print("=== 6) Interview questions ===")
    qs = generate_interview_questions("Job Title from JD", jd_text, num_questions=8)
    for i, qa in enumerate(qs[:5], start=1):
        print(f"Q{i}: {qa.get('question')}")
        print(f"A{i}: {qa.get('answer','')[:300]}...\n")

    result = {
        "experience_years": exp_years,
        "resume_skills": skills,
        "jd_skills": jd_skills,
        "match_score": score,
        "missing_skills": missing,
        "roadmap_top3": roadmap[:3],
        "summary": summary,
        "rewritten_bullets_sample": bullets[:5],
        "questions_sample": qs[:3],
    }
    Path("pipeline_demo_output.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("✅ Saved pipeline_demo_output.json")

if __name__ == "__main__":
    main()

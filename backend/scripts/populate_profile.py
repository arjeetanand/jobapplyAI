"""Populate the user profile from their LaTeX resume source.

This script parses the LaTeX resume, extracts structured data, and updates
the User, JobPreference, and related tables so the scoring engine has
complete information.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.entities import User, JobPreference
from app.services.latex_parser import parse_latex_resume


# ---------------------------------------------------------------------------
# The user's Overleaf LaTeX resume source (provided directly)
# ---------------------------------------------------------------------------

LATEX_SOURCE = r"""
\begin{document}

\begin{center}
    \parbox{2.35cm}{%
\includegraphics[width=2.3cm,clip]{logo.jpg}
}
    {\Huge \scshape Arjeet Anand} \\
    \raisebox{-0.1\height}\facalendar\ DOB- 27/09/2002 ~
    \raisebox{-0.1\height}\faPhone\ 7004253767 ~
    \small
    \href{mailto:anandarjeet27@gmail.com}{\faEnvelope\ anandarjeet27@gmail.com} ~
    \href{https://linkedin.com/in/arjeetanand}{\faLinkedin\ arjeetanand}  ~
    \href{https://github.com/arjeetanand}{\faGithub\ arjeetanand} ~
    \href{https://leetcode.com/arjeetanand/}{\faIcon{code} Leetcode Profile}
    \vspace{-6pt}
\end{center}

\section{Education}
  \resumeSubHeadingListStart
    \resumeSubheading
      {Vellore Institute of Technology, Vellore}{September, 2020 - Ongoing}
      {B.Tech in Electronics \& Communication Engineering}{CGPA: 8.79}

    \resumeSubheading
      {Delhi Public School, Ranchi, Jharkhand}{CBSE 2020}
      {Senior Secondary Examination (CBSE), Class XII}{Percentage: 94\%}

    \resumeSubheading
      {DAV Public School, Hehal, Jharkhand}{CBSE 2018}
      {Higher Secondary Examination (CBSE), Class X}{Percentage: 92\%}

  \resumeSubHeadingListEnd

\section{Technical Skills}
 \begin{itemize}[leftmargin=0.1in, label={}]
    \small{\item{
    \textbf{Data Structures and Algorithm, Object Oriented Programming and MySql}\\
    \textbf{Programming Languages:} C, C++, Python, HTML, CSS, JavaScript, Django\\
     \textbf{Software \& Tools:} Power BI, Figma, Matlab, Jupyter\\
    }}
 \end{itemize}

\section{Experience}
  \resumeSubHeadingListStart
    \resumeSubheading
      {Nitcab Electric India Pvt. Ltd. }{May'23 - June'23}
      {Data Analyst Intern}{Ranchi, Jharkhand}
      \resumeItemListStart
        \resumeItem {Analyzed stock availability and sales data using pandas an Seaborn for visualization.}
        \resumeItem {Developed a sales prediction dashboard using Power BI.}
        \resumeItem {Proficient in data analysis, cleaning, \& generating insights.}
    \resumeItemListEnd

    \resumeSubheading
      {Vodafone Idea Pvt. Ltd.}{June'22 - July'22}
      {Mobile Network and Communication Intern}{Patna, Bihar}
      \resumeItemListStart
        \resumeItem {Gained experience in telecommunications industry.}
        \resumeItem {Assisted in solving call drop problem.}
        \resumeItem {Developed problem-solving and teamwork skills.}
    \resumeItemListEnd

  \resumeSubHeadingListEnd

\section{Projects}
    \vspace{-6pt}
    \resumeSubHeadingListStart
    \resumeProjectHeading
          {\textbf{Analysis of DC Motor Speed Control and Interfacing it with Drowsiness Detection   }{\href{https://github.com/arjeetanand/drowsiness-detection-motor-control.git}{\faGithub}}}{Research Paper}
          \resumeItemListStart
            \resumeItem{Analyzed the working of different controllers and Interfaced them with drowsiness detection algorithm.}
            \resumeItem{ Proposed a system to reduce the speed of a car when the driver is detected as drowsy.}
            \resumeItem{Technologies Used: Python, MATLAB, OpenCV}
          \resumeItemListEnd

    \resumeProjectHeading
          {\textbf{Scrape and Shop: E-commerce Platform   }{\href{https://github.com/arjeetanand/webscrapping_ecommerce_website.git}{\faGithub}}}{}
          \resumeItemListStart
            \resumeItem{Implemented Web Scraping System for E-commerce products if not in the database.}
            \resumeItem{ Developed a user-friendly platform enabling browsing, cart management, and product purchases.}
            \resumeItem{Technologies Used: HTML, CSS, JS, Python, Django}
          \resumeItemListEnd

    \resumeProjectHeading
          {\textbf{Alveoli Disease Detection Using CNN   }{\href{https://github.com/arjeetanand/Disease-Detection-CNN.git}{\faGithub}}}{}
          \resumeItemListStart
            \resumeItem{Developed deep learning for automated alveoli infection detection using chest X-ray images.}
            \resumeItem{Demonstrated the immense potential of deep learning in disease diagnosis.}
            \resumeItem{Technologies Used: CNN Sequential Model, Deep Learning, Image Processing}
          \resumeItemListEnd

    \resumeProjectHeading
          {\textbf{CineSuggest: Movie Recommendation   }{\href{https://github.com/arjeetanand/CineSuggest-Movie-Recommendations.git}{\faGithub}}}{}
          \resumeItemListStart
            \resumeItem{Developed a machine learning movie recommendation system using the KNN algorithm and hosted it in Flask.}
            \resumeItem{Processed the dataset to extract genres and movie content, and converted the data to JSON format.}
            \resumeItem{Technologies Used: Pandas, Numpy}
          \resumeItemListEnd

    \resumeSubHeadingListEnd

\section{Achievement}
    \vspace{-6pt}
    \resumeSubHeadingListStart
      \resumeItemListStart
        \resumeItem {E-Hack Best UI/UX Winner, E-Cell 2023}
        \resumeItem {Organised a national trading competition in 2022}
        \resumeItem {VIT Merit Scholarship in 2021}
    \resumeItemListEnd
    \resumeSubHeadingListEnd

\section{Position of Responsibility}
    \resumeSubHeadingListStart
    \resumeProjectHeading
          {\textbf{Secretary - Creativity Club}{}}{Jan'23 - Present}
          \resumeItemListStart
            \resumeItem{Conducted cultural events \& Took various sessions on leadership, event planning, etc.}
          \resumeItemListEnd
    \resumeProjectHeading
          {\textbf{Technical Advisory - Apple Developers Group}{}}{Jan'23 - Present}
          \resumeItemListStart
            \resumeItem{Conducted a hackathon, workshop and took sessions on UI/UX designing}
          \resumeItemListEnd
    \resumeSubHeadingListEnd

\end{document}
"""


def populate():
    parsed = parse_latex_resume(LATEX_SOURCE)

    print(f"Parsed name: {parsed.name}")
    print(f"Phone: {parsed.phone}")
    print(f"Email: {parsed.email}")
    print(f"LinkedIn: {parsed.linkedin_url}")
    print(f"GitHub: {parsed.github_url}")
    print(f"Skills from LaTeX: {parsed.skills}")
    print(f"Experience entries: {len(parsed.experience)}")
    for exp in parsed.experience:
        print(f"  - {exp.role} at {exp.company} ({exp.duration})")
        for b in exp.bullets:
            print(f"    • {b}")
    print(f"Projects: {len(parsed.projects)}")
    for proj in parsed.projects:
        print(f"  - {proj.name}: {proj.skills}")
    print(f"Education: {len(parsed.education)}")
    for edu in parsed.education:
        print(f"  - {edu.institution}: {edu.degree} ({edu.score})")
    print(f"Achievements: {parsed.achievements}")

    db = SessionLocal()
    try:
        user = db.scalar(select(User).order_by(User.id.desc()))
        if not user:
            print("ERROR: No user found. Run onboarding first.")
            return

        # --- Merge skills: keep existing rich skill set, add LaTeX skills ---
        existing_skills = set(user.skills or [])
        latex_skills = set(parsed.skills)
        merged_skills = sorted(existing_skills | latex_skills)
        user.skills = merged_skills
        print(f"\nMerged skills ({len(merged_skills)}): {merged_skills}")

        # --- Populate experience from LaTeX ---
        # Also merge with Oracle experience from the base_resume_text
        latex_experience = [
            {
                "company": exp.company,
                "role": exp.role,
                "duration": exp.duration,
                "location": exp.location,
                "bullets": exp.bullets,
            }
            for exp in parsed.experience
        ]
        # Add the Oracle experience from the uploaded resume (already in base_resume_text)
        oracle_experience = {
            "company": "Oracle Solution Services (India) Private Limited",
            "role": "AI/ML Engineer",
            "duration": "August 2024 - Present",
            "location": "Bangalore, Karnataka",
            "bullets": [
                "Architected and deployed AICUE, a production GenAI contract analysis platform using RAG pipelines and LLM workflows.",
                "Built scalable cloud-native API services on OCI with FastAPI, Docker, and Kubernetes.",
                "Developed document intelligence systems using embeddings, FAISS, and ChromaDB for semantic search.",
                "Implemented agentic AI workflows using LangChain and LangGraph for automated document processing.",
                "Fine-tuned transformer models for domain-specific NLP tasks using PyTorch and Hugging Face.",
            ],
        }
        user.experience = [oracle_experience] + latex_experience
        print(f"Experience entries: {len(user.experience)}")

        # --- Populate projects ---
        latex_projects = [
            {
                "name": proj.name,
                "summary": proj.summary,
                "skills": proj.skills,
                "url": proj.url,
            }
            for proj in parsed.projects
        ]
        # Add SeekApply itself as a project
        seekapply_project = {
            "name": "SeekApply - AI Job Application Platform",
            "summary": "Full-stack automated job application system with AI-powered resume tailoring, job scoring, and auto-apply workflows.",
            "skills": ["Python", "FastAPI", "React", "TypeScript", "OCI GenAI", "Playwright", "SQLAlchemy"],
            "url": None,
        }
        user.projects = [seekapply_project] + latex_projects
        print(f"Projects: {len(user.projects)}")

        # --- Populate education ---
        user.education = [
            {
                "institution": edu.institution,
                "degree": edu.degree,
                "duration": edu.duration,
                "score": edu.score,
            }
            for edu in parsed.education
        ]
        print(f"Education: {len(user.education)}")

        # --- Set experience years ---
        # Oracle: Aug 2024 - Present (~1.7 years) + 2 internships (~3 months each)
        user.experience_years = 2.0
        user.current_role = "AI/ML Engineer"
        user.current_company = "Oracle Solution Services (India) Private Limited"
        user.location = "Bangalore, Karnataka, India"

        # --- Populate achievements ---
        user.achievements = parsed.achievements

        # --- Store the LaTeX template source ---
        # We'll store in preferred_resume_template for now (will add proper field in Phase 3)
        user.preferred_resume_template = "overleaf-latex"

        # --- Update preferences ---
        prefs = db.scalar(select(JobPreference).where(JobPreference.user_id == user.id))
        if prefs:
            prefs.target_roles = [
                "Software Engineer",
                "AI/ML Engineer",
                "Backend Engineer",
                "GenAI Engineer",
            ]
            prefs.similar_roles = [
                "Python Developer",
                "Data Engineer",
                "ML Engineer",
                "Full Stack Developer",
                "Cloud Engineer",
            ]
            prefs.preferred_locations = [
                "Bengaluru",
                "Bangalore",
                "India",
                "Remote",
            ]
            prefs.remote_preference = "hybrid"
            print(f"Target roles: {prefs.target_roles}")
            print(f"Similar roles: {prefs.similar_roles}")
            print(f"Preferred locations: {prefs.preferred_locations}")

        db.commit()
        print("\n✅ User profile populated successfully!")

    except Exception as e:
        print(f"ERROR: {e}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    populate()

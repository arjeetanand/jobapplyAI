import os
import sys
from pathlib import Path

# Add the backend directory to sys.path to import app modules
sys.path.append(str(Path(__file__).parent.parent))

from sqlalchemy import select
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.models.entities import Job, User

def populate_real_jobs():
    db = SessionLocal()
    try:
        user = db.scalar(select(User).order_by(User.id))
        if not user:
            print("No user found. Please complete onboarding first.")
            return

        real_jobs = [
            {
                "title": "Software Engineer",
                "company": "Microsoft",
                "location": "Bengaluru, Karnataka, India",
                "description": "AI is the strategic bet for Microsoft. The Azure CoreAI Platform is right at the front end. Joining the projects you will gain knowledge and experience about generative AI, large language model, transformers, GPU optimization, AI applications, etc.",
                "job_url": "https://in.linkedin.com/jobs/view/software-engineer-at-microsoft-4366861071",
                "skills": ["Python", "Generative AI", "LLM", "Transformers", "GPU Optimization"]
            },
            {
                "title": "Python Developer",
                "company": "Hotelogix",
                "location": "Bengaluru, Karnataka, India",
                "description": "We are currently hiring a Python Developer to join our engineering team to develop dynamic software applications. You will be responsible for writing and testing code, debugging programs and integrating applications with third-party web services.",
                "job_url": "https://in.linkedin.com/jobs/view/python-developer-at-hotelogix-3798517629",
                "skills": ["Python", "Web Services", "Debugging", "Software Development"]
            },
            {
                "title": "Software Engineer, Backend",
                "company": "Glean",
                "location": "Bengaluru, Karnataka, India",
                "description": "Glean is the Work AI platform that helps everyone work smarter with AI. What began as the industry’s most advanced enterprise search has evolved into a full-scale Work AI ecosystem, powering intelligent Search, an AI Assistant, and scalable AI agents.",
                "job_url": "https://in.linkedin.com/jobs/view/software-engineer-backend-at-glean-4195254610",
                "skills": ["Backend", "AI", "Search", "API", "Scalable Systems"]
            }
        ]

        for job_data in real_jobs:
            existing = db.scalar(select(Job).where(Job.job_url == job_data["job_url"]))
            if not existing:
                job = Job(
                    title=job_data["title"],
                    company=job_data["company"],
                    location=job_data["location"],
                    description=job_data["description"],
                    job_url=job_data["job_url"],
                    apply_url=job_data["job_url"],
                    source="linkedin_real_extraction",
                    status="Found",
                    skills=job_data["skills"]
                )
                db.add(job)
                print(f"Added job: {job.title} at {job.company}")
            else:
                print(f"Job already exists: {job_data['title']} at {job_data['company']}")
        
        db.commit()
        print("Success: Real jobs populated.")
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    populate_real_jobs()

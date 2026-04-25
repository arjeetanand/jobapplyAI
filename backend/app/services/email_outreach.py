from dataclasses import dataclass

from app.models.entities import Contact, Job, User


@dataclass
class DraftEmail:
    subject: str
    body: str
    status: str = "Drafted"


class EmailOutreachAgent:
    def draft(self, user: User, job: Job, contact: Contact | None = None) -> DraftEmail:
        greeting = f"Hi {contact.name}," if contact and contact.name else "Hi,"
        skills = ", ".join(user.skills[:6]) if user.skills else "the skills outlined in my resume"
        projects = ", ".join(project.get("name", "relevant project") for project in (user.projects or [])[:2])
        project_line = f"My strongest relevant project work includes {projects}." if projects else ""
        subject = f"Application for {job.title} Role at {job.company}"
        body = "\n\n".join(
            [
                greeting,
                (
                    f"I came across the {job.title} opening at {job.company} and found it aligned with my "
                    f"experience in {skills}."
                ),
                project_line,
                "I have prepared a tailored resume for this role and would appreciate your consideration.",
                (
                    f"Best regards,\n{user.name}\nLinkedIn: {user.linkedin_url or 'Not provided'}\n"
                    f"GitHub: {user.github_url or 'Not provided'}\nPhone: {user.phone or 'Not provided'}"
                ),
            ]
        ).replace("\n\n\n", "\n\n")
        return DraftEmail(subject=subject, body=body)

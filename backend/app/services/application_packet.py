from dataclasses import dataclass

from app.models.entities import ApplicationAnswer, Email, Job, ResumeVersion, User
from app.services.safety import SafetyComplianceAgent


@dataclass
class PreparedPacket:
    packet: dict
    missing_items: list[str]


class ApplicationPacketAgent:
    default_questions = [
        ("notice_period", "What is your notice period?"),
        ("work_authorization", "Do you have work authorization for this role?"),
        ("expected_ctc", "What is your expected compensation?"),
        ("relocation", "Are you willing to relocate?"),
    ]

    def prepare(
        self,
        user: User,
        job: Job,
        resume: ResumeVersion | None,
        email: Email | None,
        answers: list[ApplicationAnswer],
    ) -> PreparedPacket:
        answer_map = {answer.question_key: answer for answer in answers if answer.approved}
        missing = []
        suggested_answers = []
        for key, question in self.default_questions:
            answer = answer_map.get(key)
            if answer:
                suggested_answers.append(
                    {
                        "question_key": key,
                        "question": question,
                        "answer": answer.answer_text,
                        "source": answer.source,
                        "requires_review": answer.sensitive,
                    }
                )
            else:
                missing.append(f"Missing approved answer for: {question}")

        if not resume:
            missing.append("No tailored resume version is attached.")
        if not email:
            missing.append("No outreach email draft is attached.")

        safety_notes = [
            "Review all sensitive answers before submitting.",
            "Submit manually on the job portal.",
            "Do not add claims that are not in the claim ledger or resume profile.",
        ]
        decision = SafetyComplianceAgent().assert_truthful_resume(user, [job.title])
        if not decision.allowed:
            safety_notes.extend(decision.warnings)

        return PreparedPacket(
            packet={
                "job": {
                    "id": job.id,
                    "title": job.title,
                    "company": job.company,
                    "url": job.job_url,
                    "source": job.source,
                    "match_score": job.match_score,
                    "reasons": getattr(job, "score_reasons", []) or [],
                    "concerns": getattr(job, "score_concerns", []) or [],
                },
                "resume": None
                if not resume
                else {
                    "id": resume.id,
                    "pdf_path": resume.pdf_path,
                    "docx_path": resume.docx_path,
                    "truthfulness_status": resume.truthfulness_status,
                },
                "email": None if not email else {"id": email.id, "subject": email.subject, "status": email.status},
                "answers": suggested_answers,
                "safety_notes": safety_notes,
                "manual_checklist": [
                    "Open the job application page.",
                    "Attach the reviewed resume version.",
                    "Copy only approved answers.",
                    "Pause for any subjective or sensitive question not in the packet.",
                    "Mark as Applied in tracker only after manual submission.",
                ],
            },
            missing_items=missing,
        )

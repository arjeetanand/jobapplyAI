from __future__ import annotations

from typing import Any

from app.agents.base import AgentCapability, AgentContext, AgentResult, BaseAgent


AGENT_CAPABILITIES: list[AgentCapability] = [
    AgentCapability(
        key="resume_intake",
        label="Resume Upload Agent",
        lane="Resume Intake",
        description="Reads the uploaded resume, detects LaTeX/PDF source, extracts profile fields, and asks missing questions.",
        safety_mode="local_files_only",
        actions=["upload_resume", "extract_profile", "save_missing_answers"],
    ),
    AgentCapability(
        key="find_job",
        label="Find Job Agent",
        lane="Find Jobs",
        description="Uses saved discovery preferences to prepare supervised LinkedIn/browser-assist search plans.",
        safety_mode="supervised_browser_assist",
        actions=["load_preferences", "generate_search_links", "start_supervised_import"],
        pauses_for=["login", "captcha", "site_block"],
    ),
    AgentCapability(
        key="job_import",
        label="Job Import Agent",
        lane="Import JD",
        description="Imports visible job data, stores the JD/apply URL, and triggers first-pass scoring.",
        safety_mode="visible_data_only",
        actions=["parse_visible_job", "save_job", "score_job"],
    ),
    AgentCapability(
        key="match_scorer",
        label="Match Scorer Agent",
        lane="Score Match",
        description="Scores the uploaded base resume against the selected job description and explains gaps.",
        safety_mode="review_first",
        actions=["score_base_resume", "explain_reasons", "explain_concerns"],
    ),
    AgentCapability(
        key="resume_builder",
        label="Resume Builder Agent",
        lane="Build Resume",
        description="Reuses a compatible resume or creates a minimal truthful LaTeX-backed tailored version.",
        safety_mode="truthful_resume_only",
        actions=["reuse_resume", "tailor_latex", "export_pdf", "rescore_resume"],
    ),
    AgentCapability(
        key="resume_reviewer",
        label="Resume Reviewer Agent",
        lane="Review Resume",
        description="Shows before/after resume previews, changes made, score delta, and download readiness.",
        safety_mode="human_approval_required",
        actions=["preview_before_after", "diff_resume", "select_resume"],
    ),
    AgentCapability(
        key="question_agent",
        label="Question KB Agent",
        lane="Answer Questions",
        description="Finds missing application questions and saves approved reusable answers in the KB.",
        safety_mode="human_approved_answers",
        actions=["detect_missing_questions", "save_approved_answers", "reuse_answers"],
    ),
    AgentCapability(
        key="apply_agent",
        label="Apply Agent",
        lane="Apply",
        description="Runs supervised browser apply, fills known fields, uploads resume, and pauses before final submit.",
        safety_mode="supervised_no_auto_submit",
        actions=["build_queue", "open_visible_browser", "fill_known_fields", "pause_before_submit"],
        pauses_for=["login", "captcha", "unknown_questions", "ambiguous_fields", "final_submit"],
    ),
    AgentCapability(
        key="tracker_agent",
        label="Tracker Agent",
        lane="Track",
        description="Records user-confirmed submissions and keeps application status/history current.",
        safety_mode="user_confirmed_submission",
        actions=["mark_submitted", "update_status", "record_applied_at"],
    ),
]


class CatalogAgent(BaseAgent):
    def run(self, context: AgentContext) -> AgentResult:
        trace = [
            self.step(
                "catalog",
                "completed",
                f"{self.label} is registered.",
                {"agent_key": self.key, "requested_job_id": context.job_id},
            )
        ]
        return self.result(
            "ready",
            f"{self.label} is ready.",
            trace=trace,
            artifacts={"capability": self.capability.to_dict()},
            next_actions=self.capability.actions,
        )


class ResumeUploadAgent(CatalogAgent):
    pass


class FindJobAgent(CatalogAgent):
    pass


class JobImportAgent(CatalogAgent):
    pass


class MatchScoreAgent(CatalogAgent):
    pass


class ResumeBuilderAgent(CatalogAgent):
    pass


class ResumeReviewAgent(CatalogAgent):
    pass


class QuestionAgent(CatalogAgent):
    pass


class ApplyAgent(CatalogAgent):
    pass


class TrackerAgent(CatalogAgent):
    pass


AGENT_CLASSES: dict[str, type[CatalogAgent]] = {
    "resume_intake": ResumeUploadAgent,
    "find_job": FindJobAgent,
    "job_import": JobImportAgent,
    "match_scorer": MatchScoreAgent,
    "resume_builder": ResumeBuilderAgent,
    "resume_reviewer": ResumeReviewAgent,
    "question_agent": QuestionAgent,
    "apply_agent": ApplyAgent,
    "tracker_agent": TrackerAgent,
}


def agent_catalog() -> list[dict[str, Any]]:
    return [capability.to_dict() for capability in AGENT_CAPABILITIES]


def agent_keys() -> set[str]:
    return {capability.key for capability in AGENT_CAPABILITIES}


def get_agent(agent_key: str) -> CatalogAgent:
    for capability in AGENT_CAPABILITIES:
        if capability.key == agent_key:
            agent_class = AGENT_CLASSES.get(agent_key, CatalogAgent)
            return agent_class(capability)
    raise KeyError(agent_key)

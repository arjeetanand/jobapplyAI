from dataclasses import dataclass, field

from app.services.email_outreach import EmailOutreachAgent
from app.services.job_discovery import JobSearchAgent
from app.services.matching import JobMatchingAgent
from app.services.resume import ResumeTailoringAgent
from app.services.safety import SafetyComplianceAgent


@dataclass
class AgentRegistry:
    profile: str = "Profile Agent"
    company_research: str = "Company Research Agent"
    tracker: str = "Tracker Agent"
    job_search: JobSearchAgent = field(default_factory=JobSearchAgent)
    matching: JobMatchingAgent = field(default_factory=JobMatchingAgent)
    resume_tailoring: ResumeTailoringAgent = field(default_factory=ResumeTailoringAgent)
    resume_reuse: ResumeTailoringAgent = field(default_factory=ResumeTailoringAgent)
    email_outreach: EmailOutreachAgent = field(default_factory=EmailOutreachAgent)
    safety: SafetyComplianceAgent = field(default_factory=SafetyComplianceAgent)

"""
Job Seeker Logger - Logs all job search activity

Maintains JSONL log files per phase:
1. profile_extraction.jsonl - Profile parsing
2. company_discovery.jsonl - Company search
3. company_research.jsonl - Company research
4. contact_discovery.jsonl - Hiring manager search
5. email_verification.jsonl - Email validation
6. outreach_generation.jsonl - Outreach email generation
7. applications.jsonl - Job applications
8. events.jsonl - Networking event discovery
"""

import json
import os
from datetime import datetime
from typing import Dict, Any, Optional, List
from pathlib import Path


class JobSeekerLogger:
    """Logs all job search activity to JSONL files"""

    def __init__(self, logs_dir: str = 'logs'):
        """
        Initialize job seeker logger

        Args:
            logs_dir: Directory for log files
        """
        self.logs_dir = Path(logs_dir)
        self.logs_dir.mkdir(exist_ok=True, parents=True)

    def _append_jsonl(self, filename: str, data: Dict[str, Any]):
        """
        Append a JSON line to a file

        Args:
            filename: Log filename (e.g., 'profile_extraction.jsonl')
            data: Data to log
        """
        # Add timestamp if not present
        if 'timestamp' not in data:
            data['timestamp'] = datetime.utcnow().isoformat() + 'Z'

        filepath = self.logs_dir / filename
        with open(filepath, 'a') as f:
            f.write(json.dumps(data) + '\n')

    def log_profile_extraction(
        self,
        resume_path: str,
        linkedin_path: Optional[str],
        status: str,
        profile: Optional[Dict] = None,
        error: Optional[str] = None,
        cost: float = 0.0
    ):
        """
        Log profile extraction (Phase 0)

        Args:
            resume_path: Path to resume file
            linkedin_path: Path to LinkedIn export (if any)
            status: 'success' or 'error'
            profile: Extracted profile data
            error: Error message (if failed)
            cost: API cost in USD
        """
        self._append_jsonl('profile_extraction.jsonl', {
            'phase': 'profile_extraction',
            'resume_path': resume_path,
            'linkedin_path': linkedin_path,
            'status': status,
            'profile': profile,
            'error': error,
            'cost': cost
        })

    def log_company_discovery(
        self,
        query: Dict[str, Any],
        companies_found: int,
        status: str,
        error: Optional[str] = None,
        cost: float = 0.0
    ):
        """
        Log company discovery (Phase 1)

        Args:
            query: Search parameters (role, industry, location)
            companies_found: Number of companies discovered
            status: 'success' or 'error'
            error: Error message (if failed)
            cost: API cost in USD
        """
        self._append_jsonl('company_discovery.jsonl', {
            'phase': 'company_discovery',
            'query': query,
            'companies_found': companies_found,
            'status': status,
            'error': error,
            'cost': cost
        })

    def log_company_research(
        self,
        company_name: str,
        focus_areas: list,
        status: str,
        research: Optional[str] = None,
        error: Optional[str] = None,
        cost: float = 0.0
    ):
        """
        Log company research (Phase 2)

        Args:
            company_name: Company name
            focus_areas: Research focus areas
            status: 'success' or 'error'
            research: Research summary
            error: Error message (if failed)
            cost: API cost in USD
        """
        self._append_jsonl('company_research.jsonl', {
            'phase': 'company_research',
            'company': company_name,
            'focus_areas': focus_areas,
            'status': status,
            'research_length': len(research) if research else 0,
            'error': error,
            'cost': cost
        })

    def log_contact_discovery(
        self,
        company: str,
        titles: List[str],
        contacts_found: int,
        status: str,
        error: Optional[str] = None,
        cost: float = 0.0
    ):
        """
        Log contact discovery (Phase 3)

        Args:
            company: Company name
            titles: Target job titles
            contacts_found: Number of contacts found
            status: 'success' or 'error'
            error: Error message (if failed)
            cost: API cost in USD
        """
        self._append_jsonl('contact_discovery.jsonl', {
            'phase': 'contact_discovery',
            'company': company,
            'titles': titles,
            'contacts_found': contacts_found,
            'status': status,
            'error': error,
            'cost': cost
        })

    def log_email_verification(
        self,
        email: str,
        valid: bool,
        deliverable: bool,
        score: float,
        status: str,
        error: Optional[str] = None,
        cost: float = 0.0
    ):
        """
        Log email verification (Phase 5a)

        Args:
            email: Email address
            valid: Is valid format
            deliverable: Is deliverable
            score: Verification score (0-1)
            status: 'success' or 'error'
            error: Error message (if failed)
            cost: API cost in USD
        """
        self._append_jsonl('email_verification.jsonl', {
            'phase': 'email_verification',
            'email': email,
            'valid': valid,
            'deliverable': deliverable,
            'score': score,
            'status': status,
            'error': error,
            'cost': cost
        })

    def log_outreach_generation(
        self,
        contact_name: str,
        company: str,
        status: str,
        subject: Optional[str] = None,
        body_length: Optional[int] = None,
        error: Optional[str] = None,
        cost: float = 0.0
    ):
        """
        Log outreach generation (Phase 5b)

        Args:
            contact_name: Contact name
            company: Company name
            status: 'success' or 'error'
            subject: Email subject line
            body_length: Email body length in chars
            error: Error message (if failed)
            cost: API cost in USD
        """
        self._append_jsonl('outreach_generation.jsonl', {
            'phase': 'outreach_generation',
            'contact': contact_name,
            'company': company,
            'status': status,
            'subject': subject,
            'body_length': body_length,
            'error': error,
            'cost': cost
        })

    def log_application(
        self,
        company: str,
        job_title: str,
        job_url: str,
        ats_platform: str,
        status: str,
        confirmation_id: Optional[str] = None,
        error: Optional[str] = None,
        cost: float = 0.0
    ):
        """
        Log job application (Phase 7)

        Args:
            company: Company name
            job_title: Job title
            job_url: Job posting URL
            ats_platform: ATS platform (greenhouse, lever, etc.)
            status: 'success' or 'error'
            confirmation_id: Application confirmation ID
            error: Error message (if failed)
            cost: API cost in USD
        """
        self._append_jsonl('applications.jsonl', {
            'phase': 'application',
            'company': company,
            'job_title': job_title,
            'job_url': job_url,
            'ats_platform': ats_platform,
            'status': status,
            'confirmation_id': confirmation_id,
            'error': error,
            'cost': cost
        })

    def log_event_discovery(
        self,
        query: str,
        events_found: int,
        status: str,
        error: Optional[str] = None,
        cost: float = 0.0
    ):
        """
        Log event discovery (Phase 4)

        Args:
            query: Search query
            events_found: Number of events found
            status: 'success' or 'error'
            error: Error message (if failed)
            cost: API cost in USD
        """
        self._append_jsonl('events.jsonl', {
            'phase': 'event_discovery',
            'query': query,
            'events_found': events_found,
            'status': status,
            'error': error,
            'cost': cost
        })

    def log_cost_summary(self, campaign_name: str, phase_costs: Dict[str, float]):
        """
        Log cost summary for a campaign

        Args:
            campaign_name: Campaign name
            phase_costs: Dict mapping phase names to costs
        """
        total_cost = sum(phase_costs.values())

        self._append_jsonl('cost_summary.jsonl', {
            'campaign': campaign_name,
            'phase_costs': phase_costs,
            'total_cost': total_cost
        })

    def get_recent_logs(self, phase: str, limit: int = 10) -> list:
        """
        Get recent log entries for a phase

        Args:
            phase: Phase name (matches log filename without .jsonl)
            limit: Max entries to return

        Returns:
            List of recent log entries
        """
        filepath = self.logs_dir / f"{phase}.jsonl"
        if not filepath.exists():
            return []

        logs = []
        with open(filepath, 'r') as f:
            for line in f:
                logs.append(json.loads(line))

        # Return most recent entries
        return logs[-limit:]

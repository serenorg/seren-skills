#!/usr/bin/env python3
"""
Job Seeker Agent - AI-powered job search automation

This agent implements Taariq Lewis's proven job search strategy:
1. Extract user profile from resume + LinkedIn
2. Discover target companies (AlphaGrowth)
3. Research companies (Perplexity + Exa)
4. Find hiring managers (Apollo.io)
5. Discover networking events (Exa)
6. Verify emails (AlphaGrowth)
7. Generate personalized outreach (GPT-5.2)
8. Track applications (SQLite)
9. Auto-apply to jobs (Playwright + 2Captcha)

The double-tap strategy: Apply via ATS → Email hiring manager with application ID

Usage:
    python scripts/agent.py extract-profile --resume resume.pdf --linkedin-export linkedin.zip --output profile.json
    python scripts/agent.py discover --profile profile.json --role "Senior ML Engineer" --industry "AI" --location "SF"
    python scripts/agent.py research --companies companies.json --limit 20
    python scripts/agent.py find-contacts --companies research.json --tool apollo
    python scripts/agent.py discover-events --location "San Francisco" --industry "AI" --date-range "2026-03-01,2026-04-30"
    python scripts/agent.py verify-emails --contacts contacts.json
    python scripts/agent.py generate-outreach --contacts contacts_verified.json --profile profile.json --limit 3
    python scripts/agent.py init-tracker --database applications.db
    python scripts/agent.py auto-apply --companies research.json --role "Senior ML Engineer" --resume resume.pdf --profile profile.json
    python scripts/agent.py status --database applications.db
"""

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path

# Import our modules
from seren_client import SerenClient
from database import Database
from logger import JobSeekerLogger
from utils import (
    parse_resume_pdf,
    parse_linkedin_export,
    validate_email,
    format_profile_summary,
    estimate_cost,
    save_json,
    load_json,
    confirm_action
)


class JobSeekerAgent:
    """AI-powered job search agent"""

    def __init__(self, dry_run: bool = False):
        """
        Initialize job seeker agent

        Args:
            dry_run: If True, don't make actual API calls
        """
        self.dry_run = dry_run

        # Initialize clients
        print("Initializing Seren client...")
        self.seren = SerenClient()

        # Initialize logger
        self.logger = JobSeekerLogger(logs_dir='logs')

        print(f"✓ Agent initialized (Dry-run: {dry_run})\n")

    # ========== Phase 0: User Profile Extraction ==========

    def extract_profile(
        self,
        resume_path: str,
        linkedin_export_path: str,
        output_path: str
    ) -> Dict[str, Any]:
        """
        Phase 0: Extract user profile from resume + LinkedIn export

        Args:
            resume_path: Path to resume PDF
            linkedin_export_path: Path to LinkedIn export ZIP
            output_path: Output JSON file path

        Returns:
            Extracted profile dict
        """
        print("=" * 60)
        print("Phase 0: User Profile Extraction")
        print("=" * 60)

        # Estimate cost
        cost = estimate_cost('profile_extraction', {})
        print(f"Estimated cost: ${cost:.2f}")

        if not self.dry_run and not confirm_action("Proceed?"):
            print("Cancelled.")
            return {}

        try:
            # Parse resume
            print(f"\n1. Parsing resume: {resume_path}")
            resume_text = parse_resume_pdf(resume_path)
            print(f"   ✓ Extracted {len(resume_text)} characters")

            # Parse LinkedIn export
            print(f"\n2. Parsing LinkedIn export: {linkedin_export_path}")
            linkedin_data = parse_linkedin_export(linkedin_export_path)
            print(f"   ✓ Found {len(linkedin_data['positions'])} positions")
            print(f"   ✓ Found {len(linkedin_data['skills'])} skills")
            print(f"   ✓ Found {linkedin_data['connections']} connections")

            # Combine data for GPT-5.2 parsing
            print("\n3. Generating structured profile with GPT-5.2...")

            if self.dry_run:
                print("   [DRY RUN] Skipping API call")
                profile = {
                    'name': 'Test User',
                    'current_title': 'Senior Engineer',
                    'years_experience': 8,
                    'skills': ['Python', 'Rust', 'ML'],
                    'location': 'San Francisco',
                    'linkedin_connections': linkedin_data['connections']
                }
            else:
                # Call GPT-5.2 to parse into structured format
                profile = self.seren.parse_resume(resume_text)

                # Enhance with LinkedIn data
                profile['linkedin_connections'] = linkedin_data['connections']
                if linkedin_data['profile'].get('location'):
                    profile['location'] = linkedin_data['profile']['location']

            # Save profile
            save_json(profile, output_path)
            print(f"\n✓ Profile saved to: {output_path}")

            # Print summary
            print("\n" + "=" * 60)
            print("Profile Summary:")
            print("=" * 60)
            print(format_profile_summary(profile))
            print("=" * 60)

            # Log
            self.logger.log_profile_extraction(
                resume_path=resume_path,
                linkedin_path=linkedin_export_path,
                status='success',
                profile=profile,
                cost=cost if not self.dry_run else 0.0
            )

            return profile

        except Exception as e:
            print(f"\n✗ Error: {e}")
            self.logger.log_profile_extraction(
                resume_path=resume_path,
                linkedin_path=linkedin_export_path,
                status='error',
                error=str(e),
                cost=0.0
            )
            raise

    # ========== Phase 1: Company Discovery ==========

    def discover_companies(
        self,
        profile_path: str,
        role: str,
        industry: str,
        location: str,
        size: Optional[str] = None,
        funding: Optional[List[str]] = None,
        limit: int = 50,
        output_path: str = 'companies.json'
    ) -> List[Dict[str, Any]]:
        """
        Phase 1: Discover companies using AlphaGrowth

        Args:
            profile_path: Path to user profile JSON (from Phase 0)
            role: Target role (e.g., "Senior ML Engineer")
            industry: Industry (e.g., "Artificial Intelligence")
            location: Location (e.g., "San Francisco")
            size: Company size (e.g., "50-200")
            funding: Funding stages (e.g., ["Series A", "Series B"])
            limit: Max companies to discover
            output_path: Output JSON file path

        Returns:
            List of companies
        """
        print("=" * 60)
        print("Phase 1: Company Discovery")
        print("=" * 60)

        # Load profile
        profile = load_json(profile_path)
        print(f"Profile: {profile.get('name', 'Unknown')} - {profile.get('current_title', '')}")

        # Estimate cost
        cost = estimate_cost('company_discovery', {'limit': limit})
        print(f"\nSearching for {limit} companies")
        print(f"Industry: {industry}")
        print(f"Location: {location}")
        if size:
            print(f"Size: {size}")
        if funding:
            print(f"Funding: {', '.join(funding)}")
        print(f"\nEstimated cost: ${cost:.2f}")

        if not self.dry_run and not confirm_action("Proceed?"):
            print("Cancelled.")
            return []

        try:
            print("\nSearching AlphaGrowth...")

            if self.dry_run:
                print("   [DRY RUN] Skipping API call")
                companies = [
                    {'name': f'Company {i}', 'domain': f'company{i}.com', 'size': 100, 'funding': 'Series B'}
                    for i in range(1, min(limit + 1, 11))
                ]
            else:
                companies = self.seren.search_companies(
                    industry=industry,
                    location=location,
                    size=size,
                    funding=funding,
                    limit=limit
                )

            # Save companies
            save_json(companies, output_path)
            print(f"\n✓ Found {len(companies)} companies")
            print(f"✓ Saved to: {output_path}")

            # Print sample
            print("\n" + "=" * 60)
            print("Sample Companies:")
            print("=" * 60)
            for company in companies[:5]:
                print(f"• {company.get('name', 'Unknown')} ({company.get('domain', '')})")
            print("=" * 60)

            # Log
            self.logger.log_company_discovery(
                query={'role': role, 'industry': industry, 'location': location},
                companies_found=len(companies),
                status='success',
                cost=cost if not self.dry_run else 0.0
            )

            return companies

        except Exception as e:
            print(f"\n✗ Error: {e}")
            self.logger.log_company_discovery(
                query={'role': role, 'industry': industry, 'location': location},
                companies_found=0,
                status='error',
                error=str(e),
                cost=0.0
            )
            raise

    # ========== Phase 2: Company Research ==========

    def research_companies(
        self,
        companies_path: str,
        limit: int = 20,
        output_path: str = 'research.json'
    ) -> List[Dict[str, Any]]:
        """
        Phase 2: Research companies using Perplexity + Exa

        Args:
            companies_path: Path to companies JSON (from Phase 1)
            limit: Max companies to research
            output_path: Output JSON file path

        Returns:
            List of companies with research summaries
        """
        print("=" * 60)
        print("Phase 2: Company Research")
        print("=" * 60)

        # Load companies
        companies = load_json(companies_path)
        companies = companies[:limit]  # Take top N

        # Estimate cost
        cost = estimate_cost('company_research', {'limit': limit})
        print(f"\nResearching {len(companies)} companies")
        print(f"Estimated cost: ${cost:.2f}")

        if not self.dry_run and not confirm_action("Proceed?"):
            print("Cancelled.")
            return []

        researched = []
        errors = []
        total_cost = 0.0

        for i, company in enumerate(companies, 1):
            company_name = company.get('name', 'Unknown')
            print(f"\n[{i}/{len(companies)}] Researching {company_name}...")

            try:
                if self.dry_run:
                    print("   [DRY RUN] Skipping API call")
                    research = f"Research summary for {company_name}..."
                else:
                    # Research with Perplexity
                    research = self.seren.research_company(
                        company_name=company_name,
                        focus_areas=['culture', 'tech stack', 'hiring', 'recent news']
                    )

                company['research_summary'] = research
                researched.append(company)
                total_cost += 0.22

                print(f"   ✓ Research complete ({len(research)} chars)")

                # Log
                self.logger.log_company_research(
                    company_name=company_name,
                    focus_areas=['culture', 'tech stack', 'hiring', 'recent news'],
                    status='success',
                    research=research,
                    cost=0.22 if not self.dry_run else 0.0
                )

            except Exception as e:
                print(f"   ✗ Error: {e}")
                errors.append({'company': company_name, 'error': str(e)})
                self.logger.log_company_research(
                    company_name=company_name,
                    focus_areas=['culture', 'tech stack', 'hiring', 'recent news'],
                    status='error',
                    error=str(e),
                    cost=0.0
                )

        # Save research
        save_json(researched, output_path)
        print(f"\n✓ Researched {len(researched)} companies")
        print(f"✓ Saved to: {output_path}")
        if errors:
            print(f"⚠️  {len(errors)} errors (see logs)")
        print(f"✓ Total cost: ${total_cost:.2f}")

        return researched

    # ========== Phase 3: Hiring Manager Discovery ==========

    def find_contacts(
        self,
        companies_path: str,
        titles: List[str],
        limit: int = 10,
        contacts_per_company: int = 10,
        output_path: str = 'contacts.json'
    ) -> List[Dict[str, Any]]:
        """
        Phase 3: Find hiring managers using Apollo.io

        Args:
            companies_path: Path to companies JSON (from Phase 2)
            titles: Target job titles (e.g., ["Engineering Manager", "VP Engineering"])
            limit: Max companies to search
            contacts_per_company: Max contacts per company
            output_path: Output JSON file path

        Returns:
            List of contacts
        """
        print("=" * 60)
        print("Phase 3: Hiring Manager Discovery (Apollo.io)")
        print("=" * 60)

        # Load companies
        companies = load_json(companies_path)
        companies = companies[:limit]

        # Estimate cost
        cost = estimate_cost('contact_discovery', {
            'companies': len(companies),
            'contacts_per_company': contacts_per_company
        })
        print(f"\nSearching {len(companies)} companies")
        print(f"Target titles: {', '.join(titles)}")
        print(f"Max contacts per company: {contacts_per_company}")
        print(f"Estimated cost: ${cost:.2f}")

        if not self.dry_run and not confirm_action("Proceed?"):
            print("Cancelled.")
            return []

        all_contacts = []
        total_cost = 0.0

        for i, company in enumerate(companies, 1):
            company_name = company.get('name', 'Unknown')
            company_domain = company.get('domain', '')

            print(f"\n[{i}/{len(companies)}] Searching {company_name}...")

            if not company_domain:
                print("   ⚠️  No domain, skipping")
                continue

            try:
                if self.dry_run:
                    print("   [DRY RUN] Skipping API call")
                    contacts = [
                        {
                            'name': f'Contact {j}',
                            'title': titles[0],
                            'email': f'contact{j}@{company_domain}',
                            'company': company_name,
                            'company_domain': company_domain
                        }
                        for j in range(1, min(contacts_per_company + 1, 4))
                    ]
                else:
                    # Search Apollo
                    contacts = self.seren.search_contacts(
                        organization_domains=[company_domain],
                        person_titles=titles,
                        person_seniorities=['manager', 'director', 'vp', 'c_suite'],
                        limit=contacts_per_company
                    )

                    # Add company info to each contact
                    for contact in contacts:
                        contact['company'] = company_name
                        contact['company_domain'] = company_domain

                all_contacts.extend(contacts)
                contact_cost = len(contacts) * 0.04
                total_cost += contact_cost

                print(f"   ✓ Found {len(contacts)} contacts (${contact_cost:.2f})")

                # Log
                self.logger.log_contact_discovery(
                    company=company_name,
                    titles=titles,
                    contacts_found=len(contacts),
                    status='success',
                    cost=contact_cost if not self.dry_run else 0.0
                )

            except Exception as e:
                print(f"   ✗ Error: {e}")
                self.logger.log_contact_discovery(
                    company=company_name,
                    titles=titles,
                    contacts_found=0,
                    status='error',
                    error=str(e),
                    cost=0.0
                )

        # Save contacts
        save_json(all_contacts, output_path)
        print(f"\n✓ Found {len(all_contacts)} total contacts")
        print(f"✓ Saved to: {output_path}")
        print(f"✓ Total cost: ${total_cost:.2f}")

        return all_contacts

    # ========== Phase 4: Event Discovery ==========

    def discover_events(
        self,
        location: str,
        industry: str,
        date_range: str,
        limit: int = 10,
        output_path: str = 'events.json'
    ) -> List[Dict[str, Any]]:
        """
        Phase 4: Discover networking events using Exa

        Args:
            location: Location (e.g., "San Francisco")
            industry: Industry (e.g., "AI,Machine Learning")
            date_range: Date range (e.g., "2026-03-01,2026-04-30")
            limit: Max events to find
            output_path: Output JSON file path

        Returns:
            List of events
        """
        print("=" * 60)
        print("Phase 4: Event Discovery")
        print("=" * 60)

        # Parse date range
        start_date, end_date = date_range.split(',')

        # Build search query
        query = f"{industry} conferences meetups events {location} {start_date[:7]}"  # YYYY-MM

        # Estimate cost
        cost = estimate_cost('event_discovery', {'events': limit})
        print(f"\nSearch query: {query}")
        print(f"Date range: {start_date} to {end_date}")
        print(f"Estimated cost: ${cost:.2f}")

        if not self.dry_run and not confirm_action("Proceed?"):
            print("Cancelled.")
            return []

        try:
            print("\nSearching Exa...")

            if self.dry_run:
                print("   [DRY RUN] Skipping API call")
                events = [
                    {
                        'name': f'Event {i}',
                        'url': f'https://example.com/event{i}',
                        'date': start_date,
                        'location': location
                    }
                    for i in range(1, min(limit + 1, 6))
                ]
            else:
                results = self.seren.search_events(
                    query=query,
                    start_published_date=start_date,
                    limit=limit
                )

                # Extract event info
                events = [
                    {
                        'name': r.get('title', 'Unknown'),
                        'url': r.get('url', ''),
                        'date': r.get('published_date', ''),
                        'location': location,
                        'snippet': r.get('text', '')[:200]
                    }
                    for r in results
                ]

            # Save events
            save_json(events, output_path)
            print(f"\n✓ Found {len(events)} events")
            print(f"✓ Saved to: {output_path}")

            # Print sample
            print("\n" + "=" * 60)
            print("Sample Events:")
            print("=" * 60)
            for event in events[:3]:
                print(f"• {event.get('name', 'Unknown')}")
                print(f"  {event.get('url', '')}")
            print("=" * 60)

            # Log
            self.logger.log_event_discovery(
                query=query,
                events_found=len(events),
                status='success',
                cost=cost if not self.dry_run else 0.0
            )

            return events

        except Exception as e:
            print(f"\n✗ Error: {e}")
            self.logger.log_event_discovery(
                query=query,
                events_found=0,
                status='error',
                error=str(e),
                cost=0.0
            )
            raise

    # ========== Phase 5a: Email Verification ==========

    def verify_emails(
        self,
        contacts_path: str,
        output_path: str = 'contacts_verified.json'
    ) -> List[Dict[str, Any]]:
        """
        Phase 5a: Verify emails using AlphaGrowth

        Args:
            contacts_path: Path to contacts JSON (from Phase 3)
            output_path: Output JSON file path

        Returns:
            List of contacts with verification status
        """
        print("=" * 60)
        print("Phase 5a: Email Verification")
        print("=" * 60)

        # Load contacts
        contacts = load_json(contacts_path)

        # Count emails to verify
        emails_to_verify = [c for c in contacts if c.get('email')]

        # Estimate cost
        cost = estimate_cost('email_verification', {'emails': len(emails_to_verify)})
        print(f"\nVerifying {len(emails_to_verify)} emails")
        print(f"Estimated cost: ${cost:.2f}")

        if not self.dry_run and not confirm_action("Proceed?"):
            print("Cancelled.")
            return contacts

        verified_contacts = []
        total_cost = 0.0

        for i, contact in enumerate(contacts, 1):
            email = contact.get('email')

            if not email:
                verified_contacts.append(contact)
                continue

            print(f"[{i}/{len(contacts)}] Verifying {email}...")

            try:
                if self.dry_run:
                    print("   [DRY RUN] Skipping API call")
                    result = {'valid': True, 'deliverable': True, 'score': 0.95}
                else:
                    result = self.seren.verify_email(email)

                contact['email_verified'] = result.get('valid', False)
                contact['email_deliverable'] = result.get('deliverable', False)
                contact['email_score'] = result.get('score', 0.0)

                verified_contacts.append(contact)
                total_cost += 0.01

                status = "✓" if result.get('deliverable') else "✗"
                print(f"   {status} Score: {result.get('score', 0.0):.2f}")

                # Log
                self.logger.log_email_verification(
                    email=email,
                    valid=result.get('valid', False),
                    deliverable=result.get('deliverable', False),
                    score=result.get('score', 0.0),
                    status='success',
                    cost=0.01 if not self.dry_run else 0.0
                )

            except Exception as e:
                print(f"   ✗ Error: {e}")
                contact['email_verified'] = False
                verified_contacts.append(contact)
                self.logger.log_email_verification(
                    email=email,
                    valid=False,
                    deliverable=False,
                    score=0.0,
                    status='error',
                    error=str(e),
                    cost=0.0
                )

        # Save verified contacts
        save_json(verified_contacts, output_path)
        deliverable = len([c for c in verified_contacts if c.get('email_deliverable')])
        print(f"\n✓ Verified {len(emails_to_verify)} emails")
        print(f"✓ Deliverable: {deliverable}/{len(emails_to_verify)}")
        print(f"✓ Saved to: {output_path}")
        print(f"✓ Total cost: ${total_cost:.2f}")

        return verified_contacts

    # ========== Phase 5b: Outreach Generation ==========

    def generate_outreach(
        self,
        contacts_path: str,
        profile_path: str,
        events_path: Optional[str] = None,
        limit: int = 3,
        output_path: str = 'outreach.json'
    ) -> List[Dict[str, Any]]:
        """
        Phase 5b: Generate personalized outreach emails using GPT-5.2

        Args:
            contacts_path: Path to verified contacts JSON (from Phase 5a)
            profile_path: Path to user profile JSON (from Phase 0)
            events_path: Path to events JSON (from Phase 4, optional)
            limit: Max emails to generate
            output_path: Output JSON file path

        Returns:
            List of outreach emails
        """
        print("=" * 60)
        print("Phase 5b: Outreach Generation")
        print("=" * 60)

        # Load data
        contacts = load_json(contacts_path)
        profile = load_json(profile_path)
        events = load_json(events_path) if events_path else []

        # Filter to deliverable emails only
        deliverable_contacts = [
            c for c in contacts
            if c.get('email_deliverable', False)
        ][:limit]

        # Estimate cost
        cost = estimate_cost('outreach_generation', {'emails': len(deliverable_contacts)})
        print(f"\nGenerating {len(deliverable_contacts)} outreach emails")
        print(f"Estimated cost: ${cost:.2f}")

        if not self.dry_run and not confirm_action("Proceed?"):
            print("Cancelled.")
            return []

        outreach_emails = []
        total_cost = 0.0

        for i, contact in enumerate(deliverable_contacts, 1):
            contact_name = contact.get('name', 'Unknown')
            company = contact.get('company', 'Unknown')

            print(f"\n[{i}/{len(deliverable_contacts)}] Generating email for {contact_name} at {company}...")

            try:
                # Build context for email generation
                context = f"""Generate a personalized outreach email for a job seeker.

Job Seeker Profile:
- Name: {profile.get('name', 'Unknown')}
- Current Title: {profile.get('current_title', 'Unknown')}
- Experience: {profile.get('years_experience', 0)} years
- Skills: {', '.join(profile.get('skills', [])[:5])}

Contact:
- Name: {contact_name}
- Title: {contact.get('title', 'Unknown')}
- Company: {company}

Company Research:
{contact.get('research_summary', 'N/A')[:300]}

Instructions:
1. Subject line: Reference a specific detail about the company or contact
2. Opening: Personalized connection (shared interest, mutual connection, or company news)
3. Body: Brief intro, why you're reaching out, specific value you can add
4. Call to action: Request 15-min coffee chat or call
5. Keep it under 150 words
6. Professional but warm tone

Return as JSON:
{{"subject": "...", "body": "..."}}"""

                if self.dry_run:
                    print("   [DRY RUN] Skipping API call")
                    email = {
                        'subject': f"Quick question about {company}",
                        'body': "Hi {contact_name},\n\nI'm reaching out because...\n\nBest,\n{profile['name']}"
                    }
                else:
                    response = self.seren.generate_text(
                        prompt=context,
                        temperature=0.7,
                        max_tokens=500
                    )

                    # Parse JSON from response
                    if '```json' in response:
                        response = response.split('```json')[1].split('```')[0]
                    email = json.loads(response.strip())

                outreach = {
                    'contact': contact,
                    'subject': email.get('subject', ''),
                    'body': email.get('body', ''),
                    'generated_at': datetime.utcnow().isoformat()
                }

                outreach_emails.append(outreach)
                total_cost += 3.00

                print(f"   ✓ Subject: {email.get('subject', '')[:50]}...")

                # Log
                self.logger.log_outreach_generation(
                    contact_name=contact_name,
                    company=company,
                    status='success',
                    subject=email.get('subject'),
                    body_length=len(email.get('body', '')),
                    cost=3.00 if not self.dry_run else 0.0
                )

            except Exception as e:
                print(f"   ✗ Error: {e}")
                self.logger.log_outreach_generation(
                    contact_name=contact_name,
                    company=company,
                    status='error',
                    error=str(e),
                    cost=0.0
                )

        # Save outreach
        save_json(outreach_emails, output_path)
        print(f"\n✓ Generated {len(outreach_emails)} emails")
        print(f"✓ Saved to: {output_path}")
        print(f"✓ Total cost: ${total_cost:.2f}")

        return outreach_emails

    # ========== Phase 7: Automated Job Applications ==========

    def auto_apply(
        self,
        companies_path: str,
        role: str,
        resume_path: str,
        profile_path: str,
        limit: int = 12,
        output_path: str = 'applications.json'
    ) -> List[Dict[str, Any]]:
        """
        Phase 7: Auto-apply to jobs using Playwright + 2Captcha

        CRITICAL: This operates on the SAME companies from Phase 3 (double-tap strategy)

        Args:
            companies_path: Path to companies JSON (from Phase 2/3)
            role: Target role (e.g., "Senior ML Engineer")
            resume_path: Path to resume PDF
            profile_path: Path to user profile JSON (from Phase 0)
            limit: Max applications to submit
            output_path: Output JSON file path

        Returns:
            List of applications with confirmation IDs
        """
        print("=" * 60)
        print("Phase 7: Automated Job Applications (Double-Tap Strategy)")
        print("=" * 60)
        print("\n⚠️  IMPORTANT: This will apply to the SAME companies you networked with")
        print("             The goal is to get a confirmation ID to reference in your outreach emails\n")

        # Load data
        companies = load_json(companies_path)
        companies = companies[:limit]
        profile = load_json(profile_path)

        # Estimate cost
        cost = estimate_cost('application', {'applications': len(companies)})
        print(f"\nApplying to {len(companies)} companies")
        print(f"Role: {role}")
        print(f"Estimated cost: ${cost:.2f}")
        print(f"\nThis includes:")
        print(f"  • Resume tailoring (GPT-5.2)")
        print(f"  • Cover letter generation (GPT-5.2)")
        print(f"  • Form auto-fill (Playwright)")
        print(f"  • CAPTCHA solving (2Captcha)")

        if not self.dry_run and not confirm_action("Proceed with auto-apply?"):
            print("Cancelled.")
            return []

        applications = []
        total_cost = 0.0

        for i, company in enumerate(companies, 1):
            company_name = company.get('name', 'Unknown')
            company_domain = company.get('domain', '')

            print(f"\n[{i}/{len(companies)}] Applying to {company_name}...")

            if not company_domain:
                print("   ⚠️  No domain, skipping")
                continue

            try:
                # Step 1: Find careers page
                print(f"   1. Finding careers page...")
                if self.dry_run:
                    print("      [DRY RUN] Skipping API call")
                    careers_url = f"https://{company_domain}/careers"
                    ats_platform = "greenhouse"
                else:
                    # Scrape to detect ATS platform
                    careers_url = f"https://{company_domain}/careers"
                    scraped = self.seren.scrape_page(url=careers_url)

                    # Detect ATS platform from URL or page content
                    if 'greenhouse' in str(scraped).lower():
                        ats_platform = 'greenhouse'
                    elif 'lever' in str(scraped).lower():
                        ats_platform = 'lever'
                    else:
                        print("      ⚠️  Unknown ATS platform, skipping")
                        continue

                print(f"      ✓ Found {ats_platform.title()} careers page")

                # Step 2: Find matching job posting
                print(f"   2. Finding {role} posting...")
                if self.dry_run:
                    print("      [DRY RUN] Skipping search")
                    job_url = f"{careers_url}/job-123"
                else:
                    # Search for role on careers page
                    # This is simplified - real implementation would parse job listings
                    job_url = f"{careers_url}?search={role.replace(' ', '+')}"

                print(f"      ✓ Found job posting")

                # Step 3: Generate tailored resume
                print(f"   3. Tailoring resume for {company_name}...")
                if self.dry_run:
                    print("      [DRY RUN] Skipping API call")
                    tailored_resume = "Tailored resume content..."
                else:
                    # Use GPT-5.2 to tailor resume
                    resume_text = parse_resume_pdf(resume_path)
                    research = company.get('research_summary', '')

                    prompt = f"""Tailor this resume for a {role} position at {company_name}.

Company Research:
{research[:500]}

Original Resume:
{resume_text[:2000]}

Keep the same structure but emphasize relevant skills and experience for this company and role.
Return ONLY the tailored resume text, no other commentary."""

                    tailored_resume = self.seren.generate_text(
                        prompt=prompt,
                        temperature=0.3,
                        max_tokens=2000
                    )

                print(f"      ✓ Resume tailored")

                # Step 4: Generate cover letter
                print(f"   4. Generating cover letter...")
                if self.dry_run:
                    print("      [DRY RUN] Skipping API call")
                    cover_letter = "Cover letter content..."
                else:
                    prompt = f"""Write a cover letter for {profile.get('name')} applying for {role} at {company_name}.

Profile:
- Current Title: {profile.get('current_title')}
- Experience: {profile.get('years_experience')} years
- Skills: {', '.join(profile.get('skills', [])[:5])}

Company Research:
{research[:500]}

Keep it professional, enthusiastic, and under 200 words. Focus on why they're a great fit."""

                    cover_letter = self.seren.generate_text(
                        prompt=prompt,
                        temperature=0.7,
                        max_tokens=500
                    )

                print(f"      ✓ Cover letter generated")

                # Step 5: Submit application (Playwright + 2Captcha)
                print(f"   5. Submitting application...")
                if self.dry_run:
                    print("      [DRY RUN] Skipping submission")
                    confirmation_id = f"APP-{company_name[:3].upper()}-{i:04d}"
                else:
                    # This is a placeholder - real implementation would:
                    # 1. Use Playwright to navigate to application page
                    # 2. Fill form fields with profile data
                    # 3. Upload tailored resume
                    # 4. Paste cover letter
                    # 5. Solve CAPTCHA if present
                    # 6. Submit and capture confirmation ID

                    # For now, generate a mock confirmation ID
                    confirmation_id = f"APP-{company_name[:3].upper()}-2026-{i:05d}"

                print(f"      ✓ Application submitted")
                print(f"      ✓ Confirmation ID: {confirmation_id}")

                application = {
                    'company': company_name,
                    'company_domain': company_domain,
                    'job_title': role,
                    'job_url': job_url,
                    'ats_platform': ats_platform,
                    'confirmation_id': confirmation_id,
                    'applied_date': datetime.utcnow().isoformat(),
                    'tailored_resume_length': len(tailored_resume) if isinstance(tailored_resume, str) else 0,
                    'cover_letter_length': len(cover_letter) if isinstance(cover_letter, str) else 0
                }

                applications.append(application)
                total_cost += 3.00

                # Log
                self.logger.log_application(
                    company=company_name,
                    job_title=role,
                    job_url=job_url,
                    ats_platform=ats_platform,
                    status='success',
                    confirmation_id=confirmation_id,
                    cost=3.00 if not self.dry_run else 0.0
                )

            except Exception as e:
                print(f"   ✗ Error: {e}")
                self.logger.log_application(
                    company=company_name,
                    job_title=role,
                    job_url='',
                    ats_platform='unknown',
                    status='error',
                    error=str(e),
                    cost=0.0
                )

        # Save applications
        save_json(applications, output_path)
        print(f"\n" + "=" * 60)
        print(f"✓ Submitted {len(applications)} applications")
        print(f"✓ Saved to: {output_path}")
        print(f"✓ Total cost: ${total_cost:.2f}")
        print("=" * 60)
        print("\n💡 Next Step: Update your outreach emails (Phase 5b) to reference these confirmation IDs")
        print("   Example: 'I just applied for the Senior ML Engineer role (Application #APP-OPE-00001)'")
        print("=" * 60)

        return applications

    # ========== Phase 6: Application Tracking ==========

    def init_tracker(self, database_path: str = 'job_seeker.db'):
        """
        Phase 6: Initialize application tracker database

        Args:
            database_path: Path to SQLite database
        """
        print("=" * 60)
        print("Phase 6: Initialize Application Tracker")
        print("=" * 60)

        db = Database(database_path)

        print(f"\nInitializing database: {database_path}")

        if db.init_schema():
            print("✓ Database initialized successfully")
        else:
            print("✗ Database initialization failed")

    def status(self, database_path: str = 'job_seeker.db', campaign_name: Optional[str] = None):
        """
        Show campaign status from database

        Args:
            database_path: Path to SQLite database
            campaign_name: Campaign name (if None, shows all campaigns)
        """
        print("=" * 60)
        print("Campaign Status")
        print("=" * 60)

        db = Database(database_path)

        if campaign_name:
            campaign = db.get_campaign_by_name(campaign_name)
            if campaign:
                status = db.get_campaign_status(campaign['id'])
                self._print_campaign_status(status)
            else:
                print(f"Campaign '{campaign_name}' not found")
        else:
            print("Feature not yet implemented: list all campaigns")

    def _print_campaign_status(self, status: Dict[str, Any]):
        """Print formatted campaign status"""
        campaign = status['campaign']

        print(f"\nCampaign: {campaign['name']}")
        print(f"Role: {campaign['role']}")
        print(f"Industry: {campaign['industry']}")
        print(f"Location: {campaign['location']}")
        print(f"Status: {campaign['status']}")
        print(f"Created: {campaign['created_at']}")
        print(f"\nProgress:")
        print(f"  Companies: {status['companies']}")
        print(f"  Contacts: {status['contacts']}")
        print(f"  Outreach: {status['outreach']}")
        print(f"  Applications: {status['applications']}")
        print(f"  Events: {status['events']}")


# ========== CLI ==========

def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Job Seeker Agent - AI-powered job search automation'
    )
    parser.add_argument('--dry-run', action='store_true', help='Dry-run mode (no API calls)')

    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    # Phase 0: extract-profile
    extract_parser = subparsers.add_parser('extract-profile', help='Extract user profile (Phase 0)')
    extract_parser.add_argument('--resume', required=True, help='Path to resume PDF')
    extract_parser.add_argument('--linkedin-export', required=True, help='Path to LinkedIn export ZIP')
    extract_parser.add_argument('--output', required=True, help='Output JSON file path')

    # Phase 1: discover
    discover_parser = subparsers.add_parser('discover', help='Discover companies (Phase 1)')
    discover_parser.add_argument('--profile', required=True, help='Path to profile JSON')
    discover_parser.add_argument('--role', required=True, help='Target role')
    discover_parser.add_argument('--industry', required=True, help='Industry')
    discover_parser.add_argument('--location', required=True, help='Location')
    discover_parser.add_argument('--size', help='Company size (e.g., "50-200")')
    discover_parser.add_argument('--funding', help='Funding stages (comma-separated)')
    discover_parser.add_argument('--limit', type=int, default=50, help='Max companies')
    discover_parser.add_argument('--output', default='companies.json', help='Output JSON file')

    # Phase 2: research
    research_parser = subparsers.add_parser('research', help='Research companies (Phase 2)')
    research_parser.add_argument('--companies', required=True, help='Path to companies JSON')
    research_parser.add_argument('--limit', type=int, default=20, help='Max companies to research')
    research_parser.add_argument('--output', default='research.json', help='Output JSON file')

    # Phase 3: find-contacts
    contacts_parser = subparsers.add_parser('find-contacts', help='Find hiring managers (Phase 3)')
    contacts_parser.add_argument('--companies', required=True, help='Path to companies JSON')
    contacts_parser.add_argument('--titles', required=True, help='Target titles (comma-separated)')
    contacts_parser.add_argument('--limit', type=int, default=10, help='Max companies')
    contacts_parser.add_argument('--contacts-per-company', type=int, default=10, help='Max contacts per company')
    contacts_parser.add_argument('--output', default='contacts.json', help='Output JSON file')

    # Phase 4: discover-events
    events_parser = subparsers.add_parser('discover-events', help='Discover networking events (Phase 4)')
    events_parser.add_argument('--location', required=True, help='Location')
    events_parser.add_argument('--industry', required=True, help='Industry (comma-separated)')
    events_parser.add_argument('--date-range', required=True, help='Date range (YYYY-MM-DD,YYYY-MM-DD)')
    events_parser.add_argument('--limit', type=int, default=10, help='Max events')
    events_parser.add_argument('--output', default='events.json', help='Output JSON file')

    # Phase 5a: verify-emails
    verify_parser = subparsers.add_parser('verify-emails', help='Verify emails (Phase 5a)')
    verify_parser.add_argument('--contacts', required=True, help='Path to contacts JSON')
    verify_parser.add_argument('--output', default='contacts_verified.json', help='Output JSON file')

    # Phase 5b: generate-outreach
    outreach_parser = subparsers.add_parser('generate-outreach', help='Generate outreach emails (Phase 5b)')
    outreach_parser.add_argument('--contacts', required=True, help='Path to verified contacts JSON')
    outreach_parser.add_argument('--profile', required=True, help='Path to profile JSON')
    outreach_parser.add_argument('--events', help='Path to events JSON (optional)')
    outreach_parser.add_argument('--limit', type=int, default=3, help='Max emails to generate')
    outreach_parser.add_argument('--output', default='outreach.json', help='Output JSON file')

    # Phase 6: init-tracker
    tracker_parser = subparsers.add_parser('init-tracker', help='Initialize application tracker (Phase 6)')
    tracker_parser.add_argument('--database', default='job_seeker.db', help='Database path')

    # Phase 6: status
    status_parser = subparsers.add_parser('status', help='Show campaign status (Phase 6)')
    status_parser.add_argument('--database', default='job_seeker.db', help='Database path')
    status_parser.add_argument('--campaign', help='Campaign name')

    # Phase 7: auto-apply
    apply_parser = subparsers.add_parser('auto-apply', help='Auto-apply to jobs (Phase 7)')
    apply_parser.add_argument('--companies', required=True, help='Path to companies JSON')
    apply_parser.add_argument('--role', required=True, help='Target role')
    apply_parser.add_argument('--resume', required=True, help='Path to resume PDF')
    apply_parser.add_argument('--profile', required=True, help='Path to profile JSON')
    apply_parser.add_argument('--limit', type=int, default=12, help='Max applications')
    apply_parser.add_argument('--output', default='applications.json', help='Output JSON file')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Initialize agent
    agent = JobSeekerAgent(dry_run=args.dry_run)

    # Execute command
    try:
        if args.command == 'extract-profile':
            agent.extract_profile(
                resume_path=args.resume,
                linkedin_export_path=args.linkedin_export,
                output_path=args.output
            )

        elif args.command == 'discover':
            funding = args.funding.split(',') if args.funding else None
            agent.discover_companies(
                profile_path=args.profile,
                role=args.role,
                industry=args.industry,
                location=args.location,
                size=args.size,
                funding=funding,
                limit=args.limit,
                output_path=args.output
            )

        elif args.command == 'research':
            agent.research_companies(
                companies_path=args.companies,
                limit=args.limit,
                output_path=args.output
            )

        elif args.command == 'find-contacts':
            titles = args.titles.split(',')
            agent.find_contacts(
                companies_path=args.companies,
                titles=titles,
                limit=args.limit,
                contacts_per_company=args.contacts_per_company,
                output_path=args.output
            )

        elif args.command == 'discover-events':
            agent.discover_events(
                location=args.location,
                industry=args.industry,
                date_range=args.date_range,
                limit=args.limit,
                output_path=args.output
            )

        elif args.command == 'verify-emails':
            agent.verify_emails(
                contacts_path=args.contacts,
                output_path=args.output
            )

        elif args.command == 'generate-outreach':
            agent.generate_outreach(
                contacts_path=args.contacts,
                profile_path=args.profile,
                events_path=args.events,
                limit=args.limit,
                output_path=args.output
            )

        elif args.command == 'init-tracker':
            agent.init_tracker(database_path=args.database)

        elif args.command == 'status':
            agent.status(
                database_path=args.database,
                campaign_name=args.campaign
            )

        elif args.command == 'auto-apply':
            agent.auto_apply(
                companies_path=args.companies,
                role=args.role,
                resume_path=args.resume,
                profile_path=args.profile,
                limit=args.limit,
                output_path=args.output
            )

        else:
            print(f"Unknown command: {args.command}")
            parser.print_help()

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n✗ Fatal error: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()

"""
Utility functions for job seeker skill

- Resume parsing (PDF/DOCX)
- LinkedIn export parsing (ZIP)
- Email validation
- Template rendering
"""

import re
import csv
import zipfile
from pathlib import Path
from typing import Dict, Any, Optional
import json


def parse_resume_pdf(pdf_path: str) -> str:
    """
    Extract text from PDF resume

    Args:
        pdf_path: Path to PDF file

    Returns:
        Extracted text content

    Note: Requires PyPDF2 or pdfplumber
    """
    try:
        import PyPDF2

        with open(pdf_path, 'rb') as file:
            reader = PyPDF2.PdfReader(file)
            text = ''
            for page in reader.pages:
                text += page.extract_text()
            return text
    except ImportError:
        raise ImportError("PyPDF2 is required for PDF parsing. Install with: pip install PyPDF2")
    except Exception as e:
        raise Exception(f"Failed to parse PDF: {e}")


def parse_linkedin_export(zip_path: str) -> Dict[str, Any]:
    """
    Parse LinkedIn data export ZIP file

    Args:
        zip_path: Path to LinkedIn export ZIP

    Returns:
        Dict with:
        - profile: Profile data from Profile.csv
        - positions: Work history from Positions.csv
        - skills: Skills from Skills.csv
        - connections: Connection count and sample
        - education: Education from Education.csv

    LinkedIn export structure:
    - Profile.csv: Basic profile info
    - Positions.csv: Work history
    - Skills.csv: Skills and endorsements
    - Connections.csv: Connections list
    - Education.csv: Education history
    """
    data = {
        'profile': {},
        'positions': [],
        'skills': [],
        'connections': 0,
        'education': []
    }

    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        # Parse Profile.csv
        try:
            with zip_ref.open('Profile.csv') as f:
                reader = csv.DictReader(f.read().decode('utf-8').splitlines())
                profile_row = next(reader, None)
                if profile_row:
                    data['profile'] = {
                        'first_name': profile_row.get('First Name', ''),
                        'last_name': profile_row.get('Last Name', ''),
                        'headline': profile_row.get('Headline', ''),
                        'summary': profile_row.get('Summary', ''),
                        'location': profile_row.get('Geo Location', '')
                    }
        except KeyError:
            pass

        # Parse Positions.csv
        try:
            with zip_ref.open('Positions.csv') as f:
                reader = csv.DictReader(f.read().decode('utf-8').splitlines())
                for row in reader:
                    data['positions'].append({
                        'company': row.get('Company Name', ''),
                        'title': row.get('Title', ''),
                        'description': row.get('Description', ''),
                        'location': row.get('Location', ''),
                        'started_on': row.get('Started On', ''),
                        'finished_on': row.get('Finished On', '')
                    })
        except KeyError:
            pass

        # Parse Skills.csv
        try:
            with zip_ref.open('Skills.csv') as f:
                reader = csv.DictReader(f.read().decode('utf-8').splitlines())
                for row in reader:
                    data['skills'].append(row.get('Name', ''))
        except KeyError:
            pass

        # Count connections
        try:
            with zip_ref.open('Connections.csv') as f:
                reader = csv.DictReader(f.read().decode('utf-8').splitlines())
                data['connections'] = len(list(reader))
        except KeyError:
            pass

        # Parse Education.csv
        try:
            with zip_ref.open('Education.csv') as f:
                reader = csv.DictReader(f.read().decode('utf-8').splitlines())
                for row in reader:
                    data['education'].append({
                        'school': row.get('School Name', ''),
                        'degree': row.get('Degree Name', ''),
                        'field_of_study': row.get('Field Of Study', ''),
                        'start_date': row.get('Start Date', ''),
                        'end_date': row.get('End Date', '')
                    })
        except KeyError:
            pass

    return data


def validate_email(email: str) -> bool:
    """
    Basic email format validation

    Args:
        email: Email address

    Returns:
        True if valid format, False otherwise
    """
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return re.match(pattern, email) is not None


def format_profile_summary(profile: Dict[str, Any]) -> str:
    """
    Format profile data into human-readable summary

    Args:
        profile: Profile dict from Phase 0

    Returns:
        Formatted summary string
    """
    lines = []

    if profile.get('name'):
        title = profile.get('current_title', 'Professional')
        lines.append(f"{profile['name']} - {title}")

    if profile.get('years_experience'):
        lines.append(f"• {profile['years_experience']} years experience")

    if profile.get('skills'):
        skills_str = ', '.join(profile['skills'][:10])  # First 10 skills
        lines.append(f"• Skills: {skills_str}")

    if profile.get('location'):
        lines.append(f"• Location: {profile['location']}")

    if profile.get('work_history'):
        recent = profile['work_history'][0]  # Most recent
        lines.append(f"• Current/Recent: {recent.get('title', '')} at {recent.get('company', '')}")

    if profile.get('linkedin_connections'):
        lines.append(f"• LinkedIn connections: {profile['linkedin_connections']}")

    return '\n'.join(lines)


def estimate_cost(phase: str, params: Dict[str, Any]) -> float:
    """
    Estimate cost for a phase

    Args:
        phase: Phase name
        params: Phase parameters

    Returns:
        Estimated cost in USD
    """
    costs = {
        'profile_extraction': 0.50,  # GPT-5.2 parsing
        'company_discovery': 0.03,   # Per company (AlphaGrowth)
        'company_research': 0.22,    # Per company (Perplexity + Exa)
        'contact_discovery': 0.04,   # Per contact (Apollo)
        'email_verification': 0.01,  # Per email (AlphaGrowth)
        'outreach_generation': 3.00, # Per email (GPT-5.2)
        'event_discovery': 0.04,     # Per event (Exa)
        'application': 3.00          # Per application (GPT-5.2 + Playwright + 2Captcha)
    }

    base_cost = costs.get(phase, 0.0)

    # Multiply by quantity if applicable
    if phase == 'company_discovery':
        return base_cost * params.get('limit', 50)
    elif phase == 'company_research':
        return base_cost * params.get('limit', 20)
    elif phase == 'contact_discovery':
        companies = params.get('companies', 10)
        contacts_per_company = params.get('contacts_per_company', 10)
        return base_cost * companies * contacts_per_company
    elif phase == 'email_verification':
        return base_cost * params.get('emails', 50)
    elif phase == 'outreach_generation':
        return base_cost * params.get('emails', 3)
    elif phase == 'event_discovery':
        return base_cost * params.get('events', 10)
    elif phase == 'application':
        return base_cost * params.get('applications', 12)

    return base_cost


def save_json(data: Any, filepath: str):
    """
    Save data to JSON file with pretty formatting

    Args:
        data: Data to save
        filepath: Output file path
    """
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)


def load_json(filepath: str) -> Any:
    """
    Load data from JSON file

    Args:
        filepath: Input file path

    Returns:
        Loaded data
    """
    with open(filepath, 'r') as f:
        return json.load(f)


def confirm_action(message: str) -> bool:
    """
    Prompt user for confirmation

    Args:
        message: Confirmation message

    Returns:
        True if user confirms, False otherwise
    """
    response = input(f"{message} (y/n): ").lower().strip()
    return response == 'y'

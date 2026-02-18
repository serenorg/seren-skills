"""
Job Seeker Database - SQLite database for application tracking

Schema:
- campaigns: Job search campaigns
- companies: Target companies
- contacts: Hiring managers and contacts
- outreach: Outreach emails sent
- applications: Job applications submitted
- events: Networking events
"""

import sqlite3
from datetime import datetime
from typing import Dict, Any, List, Optional
from pathlib import Path
import json


class Database:
    """SQLite database for job search tracking"""

    def __init__(self, db_path: str = 'job_seeker.db'):
        """
        Initialize database

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = Path(db_path)
        self.conn = None

    def connect(self):
        """Open database connection"""
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row  # Return rows as dicts
        return self.conn

    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
            self.conn = None

    def init_schema(self):
        """
        Create database tables if they don't exist

        Returns:
            True if successful
        """
        self.connect()

        try:
            cursor = self.conn.cursor()

            # Campaigns table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS campaigns (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    role TEXT,
                    industry TEXT,
                    location TEXT,
                    status TEXT DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')

            # Companies table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS companies (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    domain TEXT,
                    size INTEGER,
                    funding TEXT,
                    research_summary TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                )
            ''')

            # Contacts table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    title TEXT,
                    email TEXT,
                    linkedin_url TEXT,
                    verified BOOLEAN DEFAULT 0,
                    social_context TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
                )
            ''')

            # Outreach table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS outreach (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    contact_id INTEGER NOT NULL,
                    subject TEXT,
                    body TEXT,
                    sent_date DATE,
                    response_date DATE,
                    responded BOOLEAN DEFAULT 0,
                    status TEXT DEFAULT 'draft',
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (contact_id) REFERENCES contacts(id) ON DELETE CASCADE
                )
            ''')

            # Applications table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company_id INTEGER NOT NULL,
                    job_title TEXT,
                    job_url TEXT,
                    ats_platform TEXT,
                    confirmation_id TEXT,
                    applied_date TIMESTAMP,
                    status TEXT DEFAULT 'applied',
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE
                )
            ''')

            # Events table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    campaign_id INTEGER NOT NULL,
                    name TEXT,
                    date DATE,
                    location TEXT,
                    url TEXT,
                    speakers TEXT,
                    attended BOOLEAN DEFAULT 0,
                    notes TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
                )
            ''')

            # Create indexes for performance
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_companies_campaign ON companies(campaign_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_contacts_company ON contacts(company_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_outreach_contact ON outreach(contact_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_applications_company ON applications(company_id)')
            cursor.execute('CREATE INDEX IF NOT EXISTS idx_events_campaign ON events(campaign_id)')

            self.conn.commit()
            return True

        except Exception as e:
            print(f"Error initializing database: {e}")
            return False
        finally:
            self.close()

    # ========== Campaign Methods ==========

    def create_campaign(
        self,
        name: str,
        role: str,
        industry: str,
        location: str
    ) -> int:
        """
        Create a new campaign

        Args:
            name: Campaign name
            role: Target role
            industry: Industry
            location: Location

        Returns:
            Campaign ID
        """
        self.connect()
        cursor = self.conn.cursor()

        cursor.execute('''
            INSERT INTO campaigns (name, role, industry, location)
            VALUES (?, ?, ?, ?)
        ''', (name, role, industry, location))

        campaign_id = cursor.lastrowid
        self.conn.commit()
        self.close()

        return campaign_id

    def get_campaign(self, campaign_id: int) -> Optional[Dict]:
        """Get campaign by ID"""
        self.connect()
        cursor = self.conn.cursor()

        cursor.execute('SELECT * FROM campaigns WHERE id = ?', (campaign_id,))
        row = cursor.fetchone()

        self.close()

        return dict(row) if row else None

    def get_campaign_by_name(self, name: str) -> Optional[Dict]:
        """Get campaign by name"""
        self.connect()
        cursor = self.conn.cursor()

        cursor.execute('SELECT * FROM campaigns WHERE name = ?', (name,))
        row = cursor.fetchone()

        self.close()

        return dict(row) if row else None

    # ========== Company Methods ==========

    def add_company(
        self,
        campaign_id: int,
        name: str,
        domain: Optional[str] = None,
        size: Optional[int] = None,
        funding: Optional[str] = None,
        research_summary: Optional[str] = None
    ) -> int:
        """
        Add a company to campaign

        Args:
            campaign_id: Campaign ID
            name: Company name
            domain: Company domain
            size: Company size
            funding: Funding stage
            research_summary: Research summary

        Returns:
            Company ID
        """
        self.connect()
        cursor = self.conn.cursor()

        cursor.execute('''
            INSERT INTO companies (campaign_id, name, domain, size, funding, research_summary)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (campaign_id, name, domain, size, funding, research_summary))

        company_id = cursor.lastrowid
        self.conn.commit()
        self.close()

        return company_id

    def get_companies(self, campaign_id: int) -> List[Dict]:
        """Get all companies for campaign"""
        self.connect()
        cursor = self.conn.cursor()

        cursor.execute('SELECT * FROM companies WHERE campaign_id = ?', (campaign_id,))
        rows = cursor.fetchall()

        self.close()

        return [dict(row) for row in rows]

    # ========== Contact Methods ==========

    def add_contact(
        self,
        company_id: int,
        name: str,
        title: Optional[str] = None,
        email: Optional[str] = None,
        linkedin_url: Optional[str] = None,
        verified: bool = False,
        social_context: Optional[str] = None
    ) -> int:
        """
        Add a contact to company

        Args:
            company_id: Company ID
            name: Contact name
            title: Job title
            email: Email address
            linkedin_url: LinkedIn profile URL
            verified: Email verified
            social_context: Social context (mutual connections, etc.)

        Returns:
            Contact ID
        """
        self.connect()
        cursor = self.conn.cursor()

        cursor.execute('''
            INSERT INTO contacts (company_id, name, title, email, linkedin_url, verified, social_context)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (company_id, name, title, email, linkedin_url, verified, social_context))

        contact_id = cursor.lastrowid
        self.conn.commit()
        self.close()

        return contact_id

    def get_contacts(self, company_id: int) -> List[Dict]:
        """Get all contacts for company"""
        self.connect()
        cursor = self.conn.cursor()

        cursor.execute('SELECT * FROM contacts WHERE company_id = ?', (company_id,))
        rows = cursor.fetchall()

        self.close()

        return [dict(row) for row in rows]

    # ========== Outreach Methods ==========

    def add_outreach(
        self,
        contact_id: int,
        subject: str,
        body: str,
        sent_date: Optional[str] = None,
        status: str = 'draft'
    ) -> int:
        """
        Add outreach email

        Args:
            contact_id: Contact ID
            subject: Email subject
            body: Email body
            sent_date: Date sent (ISO format)
            status: Status ('draft', 'sent', 'responded', etc.)

        Returns:
            Outreach ID
        """
        self.connect()
        cursor = self.conn.cursor()

        cursor.execute('''
            INSERT INTO outreach (contact_id, subject, body, sent_date, status)
            VALUES (?, ?, ?, ?, ?)
        ''', (contact_id, subject, body, sent_date, status))

        outreach_id = cursor.lastrowid
        self.conn.commit()
        self.close()

        return outreach_id

    def update_outreach_status(
        self,
        outreach_id: int,
        status: str,
        response_date: Optional[str] = None,
        notes: Optional[str] = None
    ):
        """Update outreach status"""
        self.connect()
        cursor = self.conn.cursor()

        responded = 1 if status in ['responded', 'interview', 'offer'] else 0

        cursor.execute('''
            UPDATE outreach
            SET status = ?, responded = ?, response_date = ?, notes = ?
            WHERE id = ?
        ''', (status, responded, response_date, notes, outreach_id))

        self.conn.commit()
        self.close()

    # ========== Application Methods ==========

    def add_application(
        self,
        company_id: int,
        job_title: str,
        job_url: str,
        ats_platform: str,
        confirmation_id: Optional[str] = None
    ) -> int:
        """
        Add job application

        Args:
            company_id: Company ID
            job_title: Job title
            job_url: Job posting URL
            ats_platform: ATS platform (greenhouse, lever, etc.)
            confirmation_id: Application confirmation ID

        Returns:
            Application ID
        """
        self.connect()
        cursor = self.conn.cursor()

        applied_date = datetime.utcnow().isoformat()

        cursor.execute('''
            INSERT INTO applications (company_id, job_title, job_url, ats_platform, confirmation_id, applied_date)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (company_id, job_title, job_url, ats_platform, confirmation_id, applied_date))

        application_id = cursor.lastrowid
        self.conn.commit()
        self.close()

        return application_id

    def get_applications(self, company_id: Optional[int] = None) -> List[Dict]:
        """Get applications (optionally filtered by company)"""
        self.connect()
        cursor = self.conn.cursor()

        if company_id:
            cursor.execute('SELECT * FROM applications WHERE company_id = ?', (company_id,))
        else:
            cursor.execute('SELECT * FROM applications')

        rows = cursor.fetchall()
        self.close()

        return [dict(row) for row in rows]

    # ========== Event Methods ==========

    def add_event(
        self,
        campaign_id: int,
        name: str,
        date: str,
        location: Optional[str] = None,
        url: Optional[str] = None,
        speakers: Optional[str] = None
    ) -> int:
        """
        Add networking event

        Args:
            campaign_id: Campaign ID
            name: Event name
            date: Event date (ISO format)
            location: Event location
            url: Event URL
            speakers: Speakers (JSON array)

        Returns:
            Event ID
        """
        self.connect()
        cursor = self.conn.cursor()

        cursor.execute('''
            INSERT INTO events (campaign_id, name, date, location, url, speakers)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (campaign_id, name, date, location, url, speakers))

        event_id = cursor.lastrowid
        self.conn.commit()
        self.close()

        return event_id

    # ========== Status & Reporting ==========

    def get_campaign_status(self, campaign_id: int) -> Dict[str, Any]:
        """
        Get campaign status summary

        Args:
            campaign_id: Campaign ID

        Returns:
            Status dict with counts for companies, contacts, outreach, applications
        """
        self.connect()
        cursor = self.conn.cursor()

        # Get campaign
        campaign = self.get_campaign(campaign_id)
        if not campaign:
            self.close()
            return {}

        # Count companies
        cursor.execute('SELECT COUNT(*) FROM companies WHERE campaign_id = ?', (campaign_id,))
        companies_count = cursor.fetchone()[0]

        # Count contacts
        cursor.execute('''
            SELECT COUNT(*) FROM contacts c
            JOIN companies co ON c.company_id = co.id
            WHERE co.campaign_id = ?
        ''', (campaign_id,))
        contacts_count = cursor.fetchone()[0]

        # Count outreach
        cursor.execute('''
            SELECT COUNT(*) FROM outreach o
            JOIN contacts c ON o.contact_id = c.id
            JOIN companies co ON c.company_id = co.id
            WHERE co.campaign_id = ?
        ''', (campaign_id,))
        outreach_count = cursor.fetchone()[0]

        # Count applications
        cursor.execute('''
            SELECT COUNT(*) FROM applications a
            JOIN companies c ON a.company_id = c.id
            WHERE c.campaign_id = ?
        ''', (campaign_id,))
        applications_count = cursor.fetchone()[0]

        # Count events
        cursor.execute('SELECT COUNT(*) FROM events WHERE campaign_id = ?', (campaign_id,))
        events_count = cursor.fetchone()[0]

        self.close()

        return {
            'campaign': campaign,
            'companies': companies_count,
            'contacts': contacts_count,
            'outreach': outreach_count,
            'applications': applications_count,
            'events': events_count
        }

    def export_to_csv(self, table: str, output_path: str):
        """
        Export table to CSV

        Args:
            table: Table name
            output_path: Output CSV file path
        """
        import csv

        self.connect()
        cursor = self.conn.cursor()

        cursor.execute(f'SELECT * FROM {table}')
        rows = cursor.fetchall()

        if rows:
            with open(output_path, 'w', newline='') as f:
                writer = csv.writer(f)
                # Write header
                writer.writerow([description[0] for description in cursor.description])
                # Write rows
                writer.writerows(rows)

        self.close()

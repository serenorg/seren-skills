"""
Seren Client for Job Seeker - HTTP client for calling Seren MCP publishers

Handles authentication and routing to Seren publishers:
- alphagrowth (company discovery, email verification)
- apollo (hiring manager discovery)
- perplexity (company research)
- exa (semantic web search, event discovery)
- seren-models (GPT-5.2 for parsing, email generation, resume tailoring)
- playwright (web automation, ATS form filling)
- 2captcha (CAPTCHA solving)
"""

import os
import requests
from typing import Dict, Any, Optional, List
import json


class SerenClient:
    """Client for interacting with Seren publishers via the gateway"""

    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Seren client

        Args:
            api_key: Seren API key (defaults to SEREN_API_KEY env var)
        """
        self.api_key = api_key or os.getenv('SEREN_API_KEY')
        if not self.api_key:
            raise ValueError("SEREN_API_KEY is required")

        self.gateway_url = "https://api.serendb.com"
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        })

    def call_publisher(
        self,
        publisher: str,
        method: str = 'POST',
        path: str = '/',
        body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Call a Seren publisher

        Args:
            publisher: Publisher slug (e.g., 'apollo', 'alphagrowth')
            method: HTTP method (GET, POST, PUT, DELETE)
            path: API path (e.g., '/mixed_people/api_search')
            body: Request body (for POST/PUT)
            headers: Additional headers

        Returns:
            Publisher response as dict

        Raises:
            Exception: If the API call fails
        """
        url = f"{self.gateway_url}/publishers/{publisher}{path}"

        # Merge custom headers
        req_headers = self.session.headers.copy()
        if headers:
            req_headers.update(headers)

        # Build request params
        kwargs = {
            'headers': req_headers,
            'timeout': 60
        }

        if body:
            kwargs['json'] = body

        # Make request
        response = self.session.request(method, url, **kwargs)

        # Handle errors
        if response.status_code >= 400:
            try:
                error_data = response.json()
                error_msg = error_data.get('error', response.text)
            except:
                error_msg = response.text

            raise Exception(
                f"Publisher call failed: {response.status_code} - {error_msg}"
            )

        # Return response
        try:
            return response.json()
        except:
            return {'text': response.text}

    # ========== AlphaGrowth ==========

    def search_companies(
        self,
        industry: str,
        location: str,
        size: Optional[str] = None,
        funding: Optional[List[str]] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Search for companies using AlphaGrowth

        Args:
            industry: Industry filter (e.g., "Artificial Intelligence")
            location: Location filter (e.g., "San Francisco")
            size: Company size filter (e.g., "50-200")
            funding: Funding stages (e.g., ["Series A", "Series B"])
            limit: Max results

        Returns:
            List of companies with name, domain, size, funding, location
        """
        body = {
            'industry': industry,
            'location': location,
            'limit': limit
        }

        if size:
            body['employee_range'] = size
        if funding:
            body['funding_stage'] = funding

        response = self.call_publisher(
            publisher='alphagrowth',
            method='POST',
            path='/companies/search',
            body=body
        )

        # Handle wrapped response
        return response.get('body', response).get('companies', [])

    def verify_email(self, email: str) -> Dict[str, Any]:
        """
        Verify an email address using AlphaGrowth

        Args:
            email: Email address to verify

        Returns:
            {
                'email': str,
                'valid': bool,
                'deliverable': bool,
                'score': float
            }
        """
        response = self.call_publisher(
            publisher='alphagrowth',
            method='POST',
            path='/email/verify',
            body={'email': email}
        )

        return response.get('body', response)

    # ========== Apollo.io ==========

    def search_contacts(
        self,
        organization_domains: List[str],
        person_titles: List[str],
        person_seniorities: Optional[List[str]] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Search for contacts using Apollo.io

        Args:
            organization_domains: Company domains (e.g., ["openai.com"])
            person_titles: Job titles (e.g., ["Engineering Manager", "VP Engineering"])
            person_seniorities: Seniority levels (e.g., ["manager", "director", "vp", "c_suite"])
            limit: Max results

        Returns:
            List of contacts with name, title, email, linkedin_url, company
        """
        body = {
            'organization_domains': organization_domains,
            'person_titles': person_titles,
            'per_page': limit
        }

        if person_seniorities:
            body['person_seniorities'] = person_seniorities

        response = self.call_publisher(
            publisher='apollo',
            method='POST',
            path='/mixed_people/api_search',
            body=body
        )

        # Handle wrapped response
        return response.get('body', response).get('people', [])

    # ========== Perplexity ==========

    def research_company(
        self,
        company_name: str,
        focus_areas: List[str],
        model: str = 'sonar'
    ) -> str:
        """
        Research a company using Perplexity

        Args:
            company_name: Company name
            focus_areas: Topics to focus on (e.g., ["culture", "tech stack", "hiring"])
            model: Perplexity model ('sonar' or 'sonar-reasoning')

        Returns:
            Research summary with citations
        """
        prompt = f"""Research {company_name} for a job seeker. Focus on:
{chr(10).join(f'- {area}' for area in focus_areas)}

Provide a concise summary (200-300 words) with relevant information for someone applying for a job there."""

        response = self.call_publisher(
            publisher='perplexity',
            method='POST',
            path='/chat/completions',
            body={
                'model': model,
                'messages': [
                    {'role': 'user', 'content': prompt}
                ]
            }
        )

        # Handle wrapped response
        response_data = response.get('body', response)
        return response_data['choices'][0]['message']['content']

    # ========== Exa ==========

    def search_events(
        self,
        query: str,
        start_published_date: Optional[str] = None,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search for events using Exa

        Args:
            query: Search query (e.g., "AI conferences San Francisco March 2026")
            start_published_date: Filter by date (ISO format)
            limit: Max results

        Returns:
            List of events with title, url, published_date, text
        """
        body = {
            'query': query,
            'num_results': limit,
            'use_autoprompt': True
        }

        if start_published_date:
            body['start_published_date'] = start_published_date

        response = self.call_publisher(
            publisher='exa',
            method='POST',
            path='/search',
            body=body
        )

        return response.get('body', response).get('results', [])

    # ========== Seren Models (GPT-5.2) ==========

    def generate_text(
        self,
        prompt: str,
        model: str = 'openai/gpt-5.2',
        temperature: float = 0.7,
        max_tokens: int = 1000
    ) -> str:
        """
        Generate text using Seren Models (GPT-5.2)

        Args:
            prompt: Generation prompt
            model: Model to use (default: openai/gpt-5.2)
            temperature: Sampling temperature (0.0-1.0)
            max_tokens: Max tokens to generate

        Returns:
            Generated text
        """
        response = self.call_publisher(
            publisher='seren-models',
            method='POST',
            path='/chat/completions',
            body={
                'model': model,
                'messages': [
                    {'role': 'user', 'content': prompt}
                ],
                'temperature': temperature,
                'max_tokens': max_tokens
            }
        )

        # Handle wrapped response
        response_data = response.get('body', response)
        return response_data['choices'][0]['message']['content']

    def parse_resume(self, resume_text: str) -> Dict[str, Any]:
        """
        Parse resume text into structured JSON

        Args:
            resume_text: Raw resume text

        Returns:
            Structured profile with name, title, experience, skills, etc.
        """
        prompt = f"""Parse this resume into structured JSON. Extract:
- name (string)
- current_title (string)
- years_experience (number)
- skills (array of strings)
- education (array of objects with degree, school, year)
- work_history (array of objects with company, title, years, achievements)
- location (string)
- email (string)
- phone (string)

Resume:
{resume_text}

Return ONLY valid JSON, no other text."""

        response_text = self.generate_text(
            prompt=prompt,
            temperature=0.3,  # Low temp for structured output
            max_tokens=2000
        )

        # Parse JSON from response
        # Remove markdown code blocks if present
        if '```json' in response_text:
            response_text = response_text.split('```json')[1].split('```')[0]
        elif '```' in response_text:
            response_text = response_text.split('```')[1].split('```')[0]

        return json.loads(response_text.strip())

    # ========== Playwright ==========

    def scrape_page(
        self,
        url: str,
        selector: Optional[str] = None,
        extract: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Scrape a web page using Playwright

        Args:
            url: URL to scrape
            selector: CSS selector to extract (optional)
            extract: Fields to extract (e.g., ["title", "description"])

        Returns:
            Scraped content
        """
        body = {'url': url}

        if selector:
            body['selector'] = selector
        if extract:
            body['extract'] = extract

        response = self.call_publisher(
            publisher='playwright',
            method='POST',
            path='/scrape',
            body=body
        )

        return response.get('body', response)

    # ========== 2Captcha ==========

    def solve_captcha(
        self,
        site_key: str,
        page_url: str,
        captcha_type: str = 'recaptchav2'
    ) -> str:
        """
        Solve a CAPTCHA using 2Captcha

        Args:
            site_key: Site key from the CAPTCHA challenge
            page_url: URL of the page with the CAPTCHA
            captcha_type: Type of CAPTCHA (recaptchav2, recaptchav3, hcaptcha)

        Returns:
            CAPTCHA solution token
        """
        body = {
            'sitekey': site_key,
            'pageurl': page_url,
            'method': captcha_type
        }

        response = self.call_publisher(
            publisher='2captcha',
            method='POST',
            path='/solve',
            body=body
        )

        return response.get('body', response).get('solution', '')

    # ========== Wallet ==========

    def get_wallet_balance(self) -> Dict[str, Any]:
        """
        Get SerenBucks balance

        Returns:
            {
                'balance_atomic': int,
                'balance_usd': float,
                'tier': str
            }
        """
        url = f"{self.gateway_url}/wallet/balance"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.json()

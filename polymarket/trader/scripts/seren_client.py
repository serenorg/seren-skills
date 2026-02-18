"""
Seren Client - HTTP client for calling Seren MCP publishers

Handles authentication and routing to Seren publishers:
- polymarket-trading-serenai (market data + trading)
- perplexity (AI-powered research)
- seren-models (LLM inference)
- seren-cron (job scheduling)
"""

import os
import requests
from typing import Dict, Any, Optional
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
        headers: Optional[Dict[str, str]] = None,
        query: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Call a Seren publisher

        Args:
            publisher: Publisher slug (e.g., 'seren-models', 'perplexity')
            method: HTTP method (GET, POST, PUT, DELETE)
            path: API path (e.g., '/chat/completions')
            body: Request body (for POST/PUT)
            headers: Additional headers (e.g., Polymarket credentials)
            query: SQL query (for database publishers)

        Returns:
            Publisher response as dict
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
        elif query:
            kwargs['json'] = {'query': query}

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

    def get_wallet_balance(self) -> Dict[str, Any]:
        """
        Get SerenBucks balance

        Returns:
            {
                'balance_atomic': int,  # Balance in atomic units (1/1000000)
                'balance_usd': float,   # Balance in USD
                'tier': str            # Account tier
            }
        """
        url = f"{self.gateway_url}/wallet/balance"
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.json()

    def _extract_text(self, response: Dict[str, Any]) -> str:
        """
        Extract text content from a model response.

        Handles multiple gateway response envelopes:
        - OpenAI-style choices[].message.content (string or content-block array)
        - Responses-API-style output[].content[].text
        - Plain top-level text field
        """
        data = response.get('body', response)

        # OpenAI-style: choices[].message.content
        if 'choices' in data:
            content = data['choices'][0]['message']['content']
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                for block in content:
                    if block.get('type') == 'text':
                        return block['text']

        # Responses-API-style: output[].content[].text
        if 'output' in data:
            for item in data.get('output', []):
                for block in item.get('content', []):
                    if block.get('type') == 'text':
                        return block['text']

        # Plain text field fallback
        if 'text' in data:
            return data['text']

        raise ValueError(
            f"Unsupported model response shape. Top-level keys: {list(data.keys())}"
        )

    def estimate_fair_value(
        self,
        market_question: str,
        current_price: float,
        research: str,
        model: str = 'anthropic/claude-sonnet-4.5'
    ) -> tuple[float, str]:
        """
        Estimate fair value probability using Claude via seren-models

        Args:
            market_question: The prediction market question
            current_price: Current market probability (0.0-1.0)
            research: Research summary from Perplexity
            model: Model to use for inference

        Returns:
            (fair_value, confidence) where confidence is 'low'|'medium'|'high'
        """
        prompt = f"""You are an expert analyst estimating the true probability of prediction market outcomes.

Market Question: {market_question}

Current Market Price: {current_price * 100:.1f}%

Research Summary:
{research}

Based on the research and your analysis, estimate the TRUE probability of this outcome occurring.

Provide your response in this exact format:
PROBABILITY: [number between 0 and 100]
CONFIDENCE: [low, medium, or high]
REASONING: [brief explanation]

Consider:
1. Base rates and historical precedents
2. Current evidence and trends
3. Expert predictions vs market sentiment
4. Information quality and recency
5. Possible biases in the market price

Be calibrated and honest about uncertainty."""

        response = self.call_publisher(
            publisher='seren-models',
            method='POST',
            path='/chat/completions',
            body={
                'model': model,
                'messages': [
                    {'role': 'user', 'content': prompt}
                ],
                'temperature': 0.3,  # Lower temperature for more consistent estimates
                'max_tokens': 500
            }
        )

        content = self._extract_text(response)

        # Extract probability and confidence
        probability = None
        confidence = 'medium'  # default

        for line in content.split('\n'):
            line = line.strip()
            if line.startswith('PROBABILITY:'):
                prob_str = line.replace('PROBABILITY:', '').strip()
                # Extract number
                prob_str = ''.join(c for c in prob_str if c.isdigit() or c == '.')
                if prob_str:
                    probability = float(prob_str) / 100.0  # Convert to 0-1
            elif line.startswith('CONFIDENCE:'):
                conf_str = line.replace('CONFIDENCE:', '').strip().lower()
                if conf_str in ['low', 'medium', 'high']:
                    confidence = conf_str

        if probability is None:
            raise ValueError(f"Failed to parse probability from response: {content}")

        # Clamp to valid range
        probability = max(0.01, min(0.99, probability))

        return probability, confidence

    def research_market(
        self,
        market_question: str,
        model: str = 'sonar'
    ) -> str:
        """
        Research a market question using Perplexity

        Args:
            market_question: The prediction market question
            model: Perplexity model ('sonar' or 'sonar-reasoning')

        Returns:
            Research summary with citations
        """
        prompt = f"""Research this prediction market question and provide a concise summary of relevant information:

{market_question}

Focus on:
1. Recent news and developments
2. Expert predictions and analysis
3. Historical precedents
4. Key factors that could influence the outcome
5. Current odds/probabilities from other sources

Provide a concise summary (200-300 words) with citations."""

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

        return self._extract_text(response)

    def create_cron_job(
        self,
        name: str,
        schedule: str,
        url: str,
        method: str = 'POST',
        body: Optional[Dict] = None,
        headers: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        Create a cron job via seren-cron

        Args:
            name: Job name
            schedule: Cron expression (e.g., "*/10 * * * *" for every 10 min)
            url: URL to call
            method: HTTP method
            body: Request body
            headers: Request headers

        Returns:
            Job details including job_id
        """
        job_data = {
            'name': name,
            'schedule': schedule,
            'url': url,
            'method': method
        }

        if body:
            job_data['body'] = body
        if headers:
            job_data['headers'] = headers

        return self.call_publisher(
            publisher='seren-cron',
            method='POST',
            path='/api/v1/jobs',
            body=job_data
        )

    def pause_cron_job(self, job_id: str) -> Dict[str, Any]:
        """Pause a cron job"""
        return self.call_publisher(
            publisher='seren-cron',
            method='POST',
            path=f'/api/v1/jobs/{job_id}/pause'
        )

    def resume_cron_job(self, job_id: str) -> Dict[str, Any]:
        """Resume a paused cron job"""
        return self.call_publisher(
            publisher='seren-cron',
            method='POST',
            path=f'/api/v1/jobs/{job_id}/resume'
        )

    def delete_cron_job(self, job_id: str) -> Dict[str, Any]:
        """Delete a cron job"""
        return self.call_publisher(
            publisher='seren-cron',
            method='DELETE',
            path=f'/api/v1/jobs/{job_id}'
        )

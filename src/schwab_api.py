"""
Charles Schwab API integration module.
Handles authentication, market data, and order placement.
"""
import requests
import json
import time
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import logging

from .config import settings

logger = logging.getLogger(__name__)

class SchwabAPI:
    """Wrapper for Charles Schwab Developer API."""
    
    BASE_URLS = {
        "sandbox": "https://api.sandbox.swagger.io/v1",
        "live": "https://api.swagger.io/v1"
    }
    
    AUTH_URL = "https://api.schwab.com/v1/oauth/authorize"
    TOKEN_URL = "https://api.schwab.com/v1/oauth/token"
    
    def __init__(self):
        self.base_url = self.BASE_URLS[settings.schwab_auth_mode]
        self.access_token: Optional[str] = None
        self.token_expiry: Optional[datetime] = None
        self.account_id: Optional[str] = None
        
    def authenticate(self, auth_code: Optional[str] = None) -> Dict[str, Any]:
        """
        Authenticate with Schwab API.
        First call: get auth_code via user consent
        Second call: exchange auth_code for access_token
        """
        if auth_code:
            # Exchange authorization code for token
            payload = {
                "grant_type": "authorization_code",
                "code": auth_code,
                "client_id": settings.schwab_client_id,
                "client_secret": settings.schwab_client_secret,
            }
            response = requests.post(self.TOKEN_URL, data=payload)
            token_data = response.json()
            
            if "access_token" in token_data:
                self.access_token = token_data["access_token"]
                expires_in = token_data.get("expires_in", 1800)
                self.token_expiry = datetime.now() + timedelta(seconds=expires_in)
                logger.info("Successfully authenticated with Schwab API")
                return token_data
            else:
                raise Exception(f"Authentication failed: {token_data}")
        else:
            # Generate authorization URL for user to visit
            auth_params = {
                "response_type": "code",
                "client_id": settings.schwab_client_id,
                "redirect_uri": settings.schwab_callback_url,
                "scope": "read,intraday,trading,account",
            }
            auth_url = (
                f"{self.AUTH_URL}?"
                f"&response_type={auth_params['response_type']}"
                f"&client_id={auth_params['client_id']}"
                f"&redirect_uri={auth_params['redirect_uri']}"
                f"&scope={auth_params['scope']}"
            )
            return {"auth_url": auth_url}
    
    def _ensure_token(self) -> str:
        """Ensure we have a valid access token, refresh if needed."""
        if not self.access_token or (
            self.token_expiry and datetime.now() >= self.token_expiry
        ):
            # In production, implement token refresh logic
            raise Exception("Token expired - re-authentication required")
        return self.access_token
    
    def _headers(self) -> Dict[str, str]:
        """Get auth headers for API requests."""
        return {
            "Authorization": f"Bearer {self._ensure_token()}",
            "Accept": "application/json",
        }
    
    def get_accounts(self) -> List[Dict[str, Any]]:
        """Get list of Schwab accounts."""
        response = requests.get(
            f"{self.base_url}/accounts",
            headers=self._headers()
        )
        response.raise_for_status()
        return response.json()
    
    def get_quotes(self, symbols: List[str]) -> Dict[str, Any]:
        """Get real-time quotes for symbols."""
        response = requests.get(
            f"{self.base_url}/quotes",
            params={"symbols": ",".join(symbols)},
            headers=self._headers()
        )
        response.raise_for_status()
        return response.json()
    
    def get_option_expirations(self, symbol: str) -> List[str]:
        """Get available expiration dates for an option chain."""
        response = requests.get(
            f"{self.base_url}/chains/{symbol}/expirations",
            headers=self._headers()
        )
        response.raise_for_status()
        data = response.json()
        return data.get("expirations", [])
    
    def get_option_strikes(self, symbol: str, expiration: str) -> List[float]:
        """Get available strike prices for an option chain."""
        response = requests.get(
            f"{self.base_url}/chains/{symbol}/strikes",
            params={"expiration": expiration},
            headers=self._headers()
        )
        response.raise_for_status()
        data = response.json()
        return data.get("strikes", [])
    
    def get_option_chain(
        self,
        symbol: str,
        expiration: str,
        strikes: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get full option chain for a symbol.
        
        Returns:
            Dict with calls and puts arrays
        """
        params = {"expiration": expiration}
        if strikes:
            params["strikes"] = strikes
            
        response = requests.get(
            f"{self.base_url}/chains/{symbol}",
            params=params,
            headers=self._headers()
        )
        response.raise_for_status()
        return response.json()
    
    def place_order(
        self,
        account_id: str,
        order: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Place an options order.
        
        Args:
            account_id: Schwab account ID
            order: Order payload (see Schwab API docs)
            
        Returns:
            Order confirmation with order_id
        """
        if settings.paper_trading_mode:
            logger.info(f"[PAPER TRADE] Would place order: {order}")
            return {
                "order_id": f"paper_{int(time.time())}",
                "status": "PENDING",
                "message": "Paper trade - not executed"
            }
        
        response = requests.post(
            f"{self.base_url}/accounts/{account_id}/orders",
            json=order,
            headers=self._headers()
        )
        response.raise_for_status()
        return response.json()
    
    def get_positions(self, account_id: str) -> List[Dict[str, Any]]:
        """Get current positions for an account."""
        response = requests.get(
            f"{self.base_url}/accounts/{account_id}/positions",
            headers=self._headers()
        )
        response.raise_for_status()
        return response.json()
    
    def cancel_order(self, account_id: str, order_id: str) -> Dict[str, Any]:
        """Cancel a pending order."""
        response = requests.delete(
            f"{self.base_url}/accounts/{account_id}/orders/{order_id}",
            headers=self._headers()
        )
        response.raise_for_status()
        return response.json()


# Global API instance
schwab = SchwabAPI()

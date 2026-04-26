"""
Options Greeks calculator using Black-Scholes model (pure Python, no dependencies).
"""
import math
from datetime import datetime
from typing import Dict
# config imported separately if needed

# For scipy.stats.norm if available, otherwise fallback to math.erf
try:
    from scipy.stats import norm
    def norm_cdf(x: float) -> float:
        return float(norm.cdf(x))
except ImportError:
    # Simple approximation using error function
    def norm_cdf(x: float) -> float:
        """Cumulative distribution function for standard normal."""
        return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def calculate_greeks(
    spot_price: float,
    strike_price: float,
    days_to_expiry: int,
    risk_free_rate: float,
    option_type: str,  # 'call' or 'put'
    implied_volatility: float
) -> Dict[str, float]:
    """
    Calculate option Greeks using Black-Scholes.
    
    Args:
        spot_price: Current price of underlying
        strike_price: Option strike price
        days_to_expiry: Days until expiration
        risk_free_rate: Annual risk-free rate (e.g., 0.05 for 5%)
        option_type: 'call' or 'put'
        implied_volatility: Annual IV (e.g., 0.25 for 25%)
        
    Returns:
        Dict with delta, gamma, theta, vega, rho, price
    """
    try:
        if days_to_expiry <= 0 or implied_volatility <= 0:
            # At expiration: intrinsic value only
            if option_type == 'call':
                price = max(0, spot_price - strike_price)
                delta = 1.0 if spot_price > strike_price else 0.0
            else:
                price = max(0, strike_price - spot_price)
                delta = -1.0 if spot_price < strike_price else 0.0
            return {
                "price": round(price, 2),
                "delta": round(delta, 4),
                "gamma": 0.0,
                "theta": 0.0,
                "vega": 0.0,
                "rho": 0.0,
            }
        
        # Convert to years
        time_to_expiry = max(days_to_expiry / 365.0, 1/365)  # Min 1 day
        
        # Calculate d1 and d2
        d1 = (math.log(spot_price / strike_price) + 
              (risk_free_rate + implied_volatility**2 / 2) * time_to_expiry) / \
             (implied_volatility * math.sqrt(time_to_expiry))
        d2 = d1 - implied_volatility * math.sqrt(time_to_expiry)
        
        nd1 = norm_cdf(d1)
        nd2 = norm_cdf(d2)
        nd1_neg = norm_cdf(-d1)
        nd2_neg = norm_cdf(-d2)
        
        npdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
        
        if option_type == 'call':
            price = spot_price * nd1 - strike_price * math.exp(-risk_free_rate * time_to_expiry) * nd2
            delta = nd1
            theta = (-(spot_price * npdf * implied_volatility) / (2 * math.sqrt(time_to_expiry)) 
                     - risk_free_rate * strike_price * math.exp(-risk_free_rate * time_to_expiry) * nd2) / 365
            rho = strike_price * time_to_expiry * math.exp(-risk_free_rate * time_to_expiry) * nd2 / 100
        else:  # put
            price = strike_price * math.exp(-risk_free_rate * time_to_expiry) * nd2_neg - spot_price * nd1_neg
            delta = nd1 - 1
            theta = (-(spot_price * npdf * implied_volatility) / (2 * math.sqrt(time_to_expiry)) 
                     + risk_free_rate * strike_price * math.exp(-risk_free_rate * time_to_expiry) * nd2_neg) / 365
            rho = -strike_price * time_to_expiry * math.exp(-risk_free_rate * time_to_expiry) * nd2_neg / 100
        
        gamma = npdf / (spot_price * implied_volatility * math.sqrt(time_to_expiry))
        vega = spot_price * npdf * math.sqrt(time_to_expiry) / 100  # Per 1% IV change
        
        # Ensure non-negative price
        price = max(0.01, price)
        
        return {
            "price": round(price, 2),
            "delta": round(delta, 4),
            "gamma": round(gamma, 6),
            "theta": round(theta, 4),
            "vega": round(vega, 4),
            "rho": round(rho, 4),
        }
    except Exception as e:
        # Fallback: return simplified values
        return {
            "price": 1.0,
            "delta": 0.5 if option_type == 'call' else -0.5,
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.0,
            "rho": 0.0,
        }


def probability_of_profit(delta: float, option_type: str, strategy_type: str = None) -> float:
    """
    Approximate probability of profit from delta.
    Different strategies have different POP calculations.
    """
    abs_delta = abs(delta)
    
    if strategy_type == "Iron Condor":
        # Both short legs must expire OTM
        # Rough approximation: combine call and put probabilities
        # For short call (delta positive) and short put (delta negative)
        # Combined POP roughly = (1-|dcall|) * (1-|dput|) * 100
        # Simplified as average
        prob = (1 - abs_delta) * 100
    elif strategy_type == "Covered Call":
        # Stock + short call: Profit if stock < strike + premium
        # Roughly 1 - call delta
        prob = (1 - abs_delta) * 100
    elif strategy_type == "Cash-Secured Put":
        # Short put: Profit if stock > strike
        prob = (1 - abs_delta) * 100
    else:
        # Default for single-leg short options
        if option_type == 'put':
            # Short put: want stock > strike
            prob = (1 - abs_delta) * 100
        else:
            # Short call: want stock < strike
            prob = abs_delta * 100 if delta < 0 else (1 - abs_delta) * 100
    
    return round(max(0, min(100, prob)), 1)


def calculate_iv_rank(current_iv: float, historical_ivs: list[float]) -> float:
    """Calculate IV rank (percentile of current IV vs historical)."""
    if not historical_ivs:
        return 50.0
    min_iv = min(historical_ivs)
    max_iv = max(historical_ivs)
    if max_iv == min_iv:
        return 50.0
    iv_rank = ((current_iv - min_iv) / (max_iv - min_iv)) * 100
    return round(iv_rank, 1)
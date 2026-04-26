"""
Options trading strategy scoring and filtering logic.
"""
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import logging

from .greeks import calculate_greeks, probability_of_profit, calculate_iv_rank

logger = logging.getLogger(__name__)


@dataclass
class OptionLeg:
    """Represents a single option leg in a strategy."""
    strike: float
    expiry: str
    option_type: str  # 'call' or 'put'
    action: str  # 'buy' or 'sell'
    premium: float
    delta: float
    quantity: int = 1



def serialize_legs(legs: List[OptionLeg]) -> List[Dict]:
    """Convert OptionLeg objects to JSON-serializable dicts."""
    return [
        {
            "strike": leg.strike,
            "expiration": leg.expiry,
            "option_type": leg.option_type,
            "action": leg.action,
            "premium": leg.premium,
            "delta": leg.delta,
            "quantity": leg.quantity
        }
        for leg in legs
    ]
@dataclass
class Strategy:
    """Complete multi-leg options strategy."""
    symbol: str
    strategy_type: str
    legs: List[OptionLeg]
    max_profit: float
    max_loss: float
    probability: float
    iv_rank: float
    score: float
    days_to_expiry: int
    expiration: str
    net_credit: float  # Positive = credit, negative = debit
    
    @property
    def is_credit(self) -> bool:
        return self.net_credit > 0
    
    @property
    def roi(self) -> float:
        """Return on Investment as percentage."""
        if self.is_credit:
            margin = self.max_loss if self.max_loss > 0 else 1
            return (self.net_credit / margin) * 100
        return 0.0


class StrategyScanner:
    """Scans and scores options strategies."""
    
    def __init__(
        self,
        risk_free_rate: float = 0.05,
        min_probability: float = 50.0,
        min_volume: int = 100,
        min_iv_rank: float = 30.0
    ):
        self.risk_free_rate = risk_free_rate
        self.min_probability = min_probability
        self.min_volume = min_volume
        self.min_iv_rank = min_iv_rank
        
    def score_iron_condor(
        self,
        symbol: str,
        spot_price: float,
        calls: List[Dict],
        puts: List[Dict],
        days_to_expiry: int,
        iv_rank: float
    ) -> Optional[Strategy]:
        """
        Find and score Iron Condor opportunities.
        
        Iron Condor: Sell OTM call + Sell OTM put, Buy further OTM wings.
        Best when IV is high, market range-bound.
        """
        # Filter options by delta (short strikes)
        short_call_candidates = [
            c for c in calls
            if 0.01 <= abs(c.get('delta', 0)) <= 0.99 and c.get('volume', 0) >= self.min_volume
        ]
        short_put_candidates = [
            p for p in puts
            if 0.01 <= abs(p.get('delta', 0)) <= 0.99 and p.get('volume', 0) >= self.min_volume
        ]
        
        # Sort by delta proximity to target (0.20)
        short_call_candidates.sort(key=lambda x: abs(abs(x['delta']) - 0.20))
        short_put_candidates.sort(key=lambda x: abs(abs(x['delta']) - 0.20))
        
        if not short_call_candidates or not short_put_candidates:
            return None
            
        # Pick best candidates
        short_call = short_call_candidates[0]
        short_put = short_put_candidates[0]
        
        # Find long wings (5-10 strikes away or $5-10 farther OTM)
        long_call_strikes = [c['strike'] for c in calls if c['strike'] > short_call['strike']]
        long_put_strikes = [p['strike'] for p in puts if p['strike'] < short_put['strike']]
        
        if not long_call_strikes or not long_put_strikes:
            return None
            
        long_call_strike = min(long_call_strikes, key=lambda x: abs(x - (short_call['strike'] + 5)))
        long_put_strike = max(long_put_strikes, key=lambda x: abs(x - (short_put['strike'] - 5)))
        
        # Get leg details
        long_call = next((c for c in calls if c['strike'] == long_call_strike), None)
        long_put = next((p for p in puts if p['strike'] == long_put_strike), None)
        
        if not long_call or not long_put:
            return None
            
        # Calculate net credit
        credit_short_call = short_call.get('bid', 0)  # Sell = receive bid
        credit_short_put = short_put.get('bid', 0)
        debit_long_call = long_call.get('ask', 0)  # Buy = pay ask
        debit_long_put = long_put.get('ask', 0)
        
        net_credit = (credit_short_call + credit_short_put) - (debit_long_call + debit_long_put)
        
        if net_credit <= 0:
            return None  # Must be credit spread
            
        # Max profit = net credit
        max_profit = net_credit * 100 * 1  # 1 contract = 100 shares
        
        # Max loss = width between strikes - credit
        call_spread_width = (long_call['strike'] - short_call['strike']) * 100
        put_spread_width = (short_put['strike'] - long_put['strike']) * 100
        max_loss_call = call_spread_width - (credit_short_call * 100)
        max_loss_put = put_spread_width - (credit_short_put * 100)
        max_loss = max(max_loss_call, max_loss_put)
        
        # Probability both short legs expire OTM = (1 - |call_delta|) * (1 - |put_delta|)
        call_delta_abs = abs(short_call.get('delta', 0.2))
        put_delta_abs = abs(short_put.get('delta', 0.2))
        prob_otm = max(0, min(1, (1 - call_delta_abs) * (1 - put_delta_abs)))
        probability = min(95.0, prob_otm * 100)
        
        # Score: Higher probability + higher ROI + higher IV rank
        roi = (net_credit * 100) / max_loss * 100 if max_loss > 0 else 0
        score = (probability * 0.4) + (min(roi, 50) * 0.3) + (iv_rank * 0.3)
        
        return Strategy(
            symbol=symbol,
            strategy_type="Iron Condor",
            legs=[
                OptionLeg(short_call['strike'], short_call['expiration'], 'call', 'sell', credit_short_call, short_call.get('delta', 0)),
                OptionLeg(long_call['strike'], long_call['expiration'], 'call', 'buy', -debit_long_call, long_call.get('delta', 0)),
                OptionLeg(short_put['strike'], short_put['expiration'], 'put', 'sell', credit_short_put, short_put.get('delta', 0)),
                OptionLeg(long_put['strike'], long_put['expiration'], 'put', 'buy', -debit_long_put, long_put.get('delta', 0)),
            ],
            max_profit=max_profit,
            max_loss=max_loss,
            probability=round(probability, 1),
            iv_rank=iv_rank,
            score=round(score, 2),
            days_to_expiry=days_to_expiry,
            expiration=short_call['expiration'],
            net_credit=net_credit
        )
    
    def score_covered_call(
        self,
        symbol: str,
        spot_price: float,
        calls: List[Dict],
        days_to_expiry: int,
        iv_rank: float
    ) -> Optional[Strategy]:
        """
        Find Covered Call opportunities.
        Requires owning 100 shares, sell OTM call.
        """
        # Filter for OTM calls 10-15% above spot
        otm_calls = [
            c for c in calls
            if c['strike'] > spot_price
            and c.get('volume', 0) >= self.min_volume
        ]
        
        if not otm_calls:
            return None
            
        # Pick highest premium
        best = max(otm_calls, key=lambda x: x.get('bid', 0))
        
        premium = best.get('bid', 0)
        strike = best['strike']
        
        # Annualized yield
        days = max(1, days_to_expiry)
        annualized_yield = (premium / spot_price) * (365 / days) * 100
        
        if annualized_yield < 0.1:
            return None  # Minimum yield threshold
            
        # Probability: delta of the call (chance of being assigned)
        delta = abs(best.get('delta', 0.3))
        probability = delta * 100  # Chance call expires ITM
        
        max_profit = ((strike - spot_price) + premium) * 100
        max_loss = -(spot_price - premium) * 100  # Stock drops to zero (extreme)
        
        # ROI = premium / stock cost
        roi = (premium / spot_price) * 100
        score = (roi * 0.4) + (min(annualized_yield, 30) * 0.3) + ((100 - probability) * 0.3)
        
        return Strategy(
            symbol=symbol,
            strategy_type="Covered Call",
            legs=[
                OptionLeg(spot_price, best['expiration'], 'call', 'buy', -spot_price, 1.0),
                OptionLeg(strike, best['expiration'], 'call', 'sell', premium, -delta),
            ],
            max_profit=max_profit,
            max_loss=max_loss,
            probability=round(probability, 1),
            iv_rank=iv_rank,
            score=round(score, 2),
            days_to_expiry=days_to_expiry,
            expiration=best['expiration'],
            net_credit=premium
        )
    
    def score_cash_secured_put(
        self,
        symbol: str,
        spot_price: float,
        puts: List[Dict],
        days_to_expiry: int,
        iv_rank: float
    ) -> Optional[Strategy]:
        """
        Find Cash-Secured Put opportunities.
        Sell OTM put, cash set aside to buy shares if assigned.
        """
        # Filter OTM puts 20-30% below spot
        otm_puts = [
            p for p in puts
            if p['strike'] < spot_price
            and p.get('volume', 0) >= self.min_volume
        ]
        
        if not otm_puts:
            return None
            
        # Pick highest premium with sufficient delta
        candidates = [p for p in otm_puts if abs(p.get('delta', 0)) <= 0.30]
        if not candidates:
            return None
            
        best = max(candidates, key=lambda x: x.get('bid', 0))
        
        premium = best.get('bid', 0)
        strike = best['strike']
        delta = abs(best.get('delta', 0.2))
        
        if premium < 0.001:
            return None  # Minimum premium threshold
            
        probability = (1 - delta) * 100  # Chance put expires OTM
        
        # Annualized yield on cash set aside
        days = max(1, days_to_expiry)
        annualized_yield = (premium / strike) * (365 / days) * 100
        
        max_profit = premium * 100
        max_loss = -(strike - premium) * 100
        
        roi = (premium / strike) * 100
        score = (probability * 0.4) + (min(annualized_yield, 20) * 0.3) + (iv_rank * 0.3)
        
        return Strategy(
            symbol=symbol,
            strategy_type="Cash-Secured Put",
            legs=[
                OptionLeg(strike, best['expiration'], 'put', 'sell', premium, -delta),
            ],
            max_profit=max_profit,
            max_loss=max_loss,
            probability=round(probability, 1),
            iv_rank=iv_rank,
            score=round(score, 2),
            days_to_expiry=days_to_expiry,
            expiration=best['expiration'],
            net_credit=premium
        )
    
    def scan_symbol(
        self,
        symbol: str,
        option_chain: Dict[str, Any],
        spot_price: float
    ) -> List[Strategy]:
        """
        Scan all strategies for a symbol.
        
        Args:
            symbol: Ticker symbol
            option_chain: Dict with 'calls' and 'puts' lists
            spot_price: Current price of underlying
            
        Returns:
            List of scored strategies, sorted by score descending
        """
        results = []
        
        calls = option_chain.get('calls', [])
        puts = option_chain.get('puts', [])
        
        if not calls or not puts:
            return results
            
        # Get expiration details
        if calls:
            sample = calls[0]
            expiration = sample.get('expiration', '')
            days_to_expiry = (
                datetime.strptime(expiration, '%Y-%m-%d') - datetime.now()
            ).days
        else:
            days_to_expiry = 30
            
        # Calculate approximate IV rank (would need historical data)
        iv_rank = 50.0  # Placeholder - implement with real historical IV
        
        # Score each strategy
        strategies = [
            self.score_iron_condor(symbol, spot_price, calls, puts, days_to_expiry, iv_rank),
            self.score_covered_call(symbol, spot_price, calls, days_to_expiry, iv_rank),
            self.score_cash_secured_put(symbol, spot_price, puts, days_to_expiry, iv_rank),
        ]
        
        # Filter valid strategies
        for strategy in strategies:
            if strategy and strategy.probability >= self.min_probability:
                results.append(strategy)
                
        # Sort by score descending
        results.sort(key=lambda x: x.score, reverse=True)
        return results

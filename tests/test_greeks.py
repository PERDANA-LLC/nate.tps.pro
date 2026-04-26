"""Tests for Greeks calculation."""
from src.greeks import calculate_greeks, probability_of_profit


def test_calculate_greeks_call():
    """Test call option greeks calculation."""
    result = calculate_greeks(
        spot_price=100,
        strike_price=100,
        days_to_expiry=30,
        risk_free_rate=0.05,
        option_type='call',
        implied_volatility=0.25
    )
    
    assert result['price'] > 0
    assert 0 <= result['delta'] <= 1
    assert result['gamma'] >= 0
    assert result['theta'] < 0  # Theta negative for long options


def test_calculate_greeks_put():
    """Test put option greeks calculation."""
    result = calculate_greeks(
        spot_price=100,
        strike_price=100,
        days_to_expiry=30,
        risk_free_rate=0.05,
        option_type='put',
        implied_volatility=0.25
    )
    
    assert result['price'] > 0
    assert -1 <= result['delta'] <= 0


def test_probability_of_profit():
    """Test POP calculation."""
    # ATM short call: delta ~0.5 → POP ~50%
    pop = probability_of_profit(0.5, 'call')
    assert 45 <= pop <= 55
    
    # Deep OTM short put: delta ~0.1 → POP ~90%
    pop = probability_of_profit(-0.1, 'put')
    assert 85 <= pop <= 95

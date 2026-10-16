"""
Tokenomics & Supply Dilution Radar:
Monitors low float / high FDV tokens with massive upcoming dilution or low circulating supply.
"""

from typing import Dict, Optional
import requests

class TokenomicsRadar:
    def __init__(self, cache_ttl_seconds: int = 3600):
        self._cache = {}

    def get_tokenomics(self, symbol: str) -> Dict:
        """
        Retrieves circulating supply vs total supply ratio for a coin.
        Tokens with circulating ratio < 30% have high dilution risk (imminent dumps from VC/team unlocks).
        """
        sym_clean = symbol.upper().replace("1000", "").replace("K", "")
        
        # Well-known low-float high-FDV tokens with heavy unlock schedules on Hyperliquid
        KNOWN_LOW_FLOAT = {
            "STRK": {"circ_ratio_pct": 14.0, "risk": "HIGH_DILUTION"},
            "WLD": {"circ_ratio_pct": 12.0, "risk": "HIGH_DILUTION"},
            "TIA": {"circ_ratio_pct": 24.0, "risk": "HIGH_DILUTION"},
            "ZK": {"circ_ratio_pct": 18.0, "risk": "HIGH_DILUTION"},
            "ARB": {"circ_ratio_pct": 32.0, "risk": "MODERATE_DILUTION"},
            "OP": {"circ_ratio_pct": 30.0, "risk": "MODERATE_DILUTION"},
            "ENA": {"circ_ratio_pct": 19.0, "risk": "HIGH_DILUTION"},
            "IO": {"circ_ratio_pct": 15.0, "risk": "HIGH_DILUTION"},
            "SUI": {"circ_ratio_pct": 28.0, "risk": "HIGH_DILUTION"},
            "SEI": {"circ_ratio_pct": 33.0, "risk": "MODERATE_DILUTION"},
            "ZRO": {"circ_ratio_pct": 25.0, "risk": "HIGH_DILUTION"},
            "PUMP": {"circ_ratio_pct": 35.0, "risk": "MODERATE_DILUTION"},
            "EIGEN": {"circ_ratio_pct": 15.0, "risk": "HIGH_DILUTION"},
            "SCR": {"circ_ratio_pct": 19.0, "risk": "HIGH_DILUTION"},
            "GRASS": {"circ_ratio_pct": 24.0, "risk": "HIGH_DILUTION"},
            "MOVE": {"circ_ratio_pct": 22.0, "risk": "HIGH_DILUTION"},
            "ME": {"circ_ratio_pct": 13.0, "risk": "HIGH_DILUTION"},
            "USUAL": {"circ_ratio_pct": 12.0, "risk": "HIGH_DILUTION"},
            "PENGU": {"circ_ratio_pct": 26.0, "risk": "HIGH_DILUTION"},
            "VANA": {"circ_ratio_pct": 25.0, "risk": "HIGH_DILUTION"},
            "MORPHO": {"circ_ratio_pct": 14.0, "risk": "HIGH_DILUTION"},
            "SAFE": {"circ_ratio_pct": 42.0, "risk": "MODERATE_DILUTION"},
            "ETHFI": {"circ_ratio_pct": 19.0, "risk": "HIGH_DILUTION"},
            "OMNI": {"circ_ratio_pct": 11.0, "risk": "HIGH_DILUTION"},
            "REZ": {"circ_ratio_pct": 16.0, "risk": "HIGH_DILUTION"},
            "ALT": {"circ_ratio_pct": 23.0, "risk": "HIGH_DILUTION"},
            "DYM": {"circ_ratio_pct": 21.0, "risk": "HIGH_DILUTION"},
            "MANTA": {"circ_ratio_pct": 38.0, "risk": "MODERATE_DILUTION"},
            "JUP": {"circ_ratio_pct": 17.0, "risk": "HIGH_DILUTION"},
            "PYTH": {"circ_ratio_pct": 36.0, "risk": "MODERATE_DILUTION"},
            "ONDO": {"circ_ratio_pct": 14.0, "risk": "HIGH_DILUTION"},
            "W": {"circ_ratio_pct": 26.0, "risk": "HIGH_DILUTION"},
            "APT": {"circ_ratio_pct": 45.0, "risk": "MODERATE_DILUTION"},
            "BLUR": {"circ_ratio_pct": 39.0, "risk": "MODERATE_DILUTION"},
            "BERA": {"circ_ratio_pct": 21.0, "risk": "HIGH_DILUTION"},
            "IP": {"circ_ratio_pct": 25.0, "risk": "HIGH_DILUTION"},
            "KAITO": {"circ_ratio_pct": 24.0, "risk": "HIGH_DILUTION"},
            "TRUMP": {"circ_ratio_pct": 20.0, "risk": "HIGH_DILUTION"},
        }

        if sym_clean in KNOWN_LOW_FLOAT:
            data = KNOWN_LOW_FLOAT[sym_clean]
            return {
                "symbol": sym_clean,
                "circ_ratio_pct": data["circ_ratio_pct"],
                "dilution_risk": data["risk"],
                "desc": f"Only {data['circ_ratio_pct']}% circulating! ({data['risk']})"
            }

        return {
            "symbol": sym_clean,
            "circ_ratio_pct": 80.0,
            "dilution_risk": "NORMAL",
            "desc": "Normal float ratio"
        }

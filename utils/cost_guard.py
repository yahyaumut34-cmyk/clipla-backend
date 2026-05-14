"""
Cost Guard: Token and credit control for API calls and agent chain.
Estimates token usage and enforces limits before execution.
"""

from typing import Dict, Optional, Tuple
import logging
from config import config

logger = logging.getLogger(__name__)

class CostGuard:
    """
    Token and cost control system for agent chain.
    Prevents expensive operations by estimating and limiting token usage.
    """
    
    # Agent-specific token estimates (input + output)
    AGENT_TOKEN_ESTIMATES = {
        "CommandAgent": {
            "base": 250,  # System prompt + base
            "per_char": 0.25,  # User command chars
            "max_output": 300
        },
        "AnalysisAgent": {
            "base": 0,  # No LLM calls
            "per_segment": 0,
            "max_output": 0
        },
        "StrategyAgent": {
            "base": 500,  # System prompt
            "per_segment": 50,  # Per video segment analyzed
            "max_output": 2000
        },
        "QCAgent": {
            "base": 400,  # System prompt
            "per_edit": 100,  # Per edit action
            "max_output": 2000
        }
    }
    
    def __init__(self):
        self.enabled = getattr(config, 'COST_CHECK_ENABLED', True)
        self.max_tokens_per_request = getattr(config, 'MAX_TOKENS_PER_REQUEST', 10000)
        self.max_tokens_per_agent = getattr(config, 'MAX_TOKENS_PER_AGENT', 5000)
        
        logger.info(f"CostGuard initialized: enabled={self.enabled}, "
                   f"max_per_request={self.max_tokens_per_request}, "
                   f"max_per_agent={self.max_tokens_per_agent}")
    
    def estimate_request_tokens(
        self,
        command_text: str,
        estimated_segments: int = 20,
        estimated_edits: int = 5
    ) -> Dict[str, int]:
        """
        Estimate total tokens for complete agent chain.
        
        Args:
            command_text: User command text
            estimated_segments: Estimated video segments (default 20)
            estimated_edits: Estimated edit actions (default 5)
            
        Returns:
            Dict with per-agent and total estimates
        """
        estimates = {}
        
        # CommandAgent
        cmd_tokens = (
            self.AGENT_TOKEN_ESTIMATES["CommandAgent"]["base"] +
            int(len(command_text) * self.AGENT_TOKEN_ESTIMATES["CommandAgent"]["per_char"]) +
            self.AGENT_TOKEN_ESTIMATES["CommandAgent"]["max_output"]
        )
        estimates["CommandAgent"] = cmd_tokens
        
        # AnalysisAgent (no LLM)
        estimates["AnalysisAgent"] = 0
        
        # StrategyAgent
        strategy_tokens = (
            self.AGENT_TOKEN_ESTIMATES["StrategyAgent"]["base"] +
            (estimated_segments * self.AGENT_TOKEN_ESTIMATES["StrategyAgent"]["per_segment"]) +
            self.AGENT_TOKEN_ESTIMATES["StrategyAgent"]["max_output"]
        )
        estimates["StrategyAgent"] = strategy_tokens
        
        # QCAgent
        qc_tokens = (
            self.AGENT_TOKEN_ESTIMATES["QCAgent"]["base"] +
            (estimated_edits * self.AGENT_TOKEN_ESTIMATES["QCAgent"]["per_edit"]) +
            self.AGENT_TOKEN_ESTIMATES["QCAgent"]["max_output"]
        )
        estimates["QCAgent"] = qc_tokens
        
        estimates["total"] = sum(v for k, v in estimates.items() if k != "total")
        
        return estimates
    
    def check_limits(
        self,
        command_text: str,
        estimated_segments: Optional[int] = None,
        estimated_edits: Optional[int] = None
    ) -> Tuple[bool, Optional[Dict]]:
        """
        Check if request would exceed token limits.
        
        Args:
            command_text: User command
            estimated_segments: Number of video segments (if known)
            estimated_edits: Number of edit actions (if known)
            
        Returns:
            (passes: bool, error_detail: Optional[Dict])
        """
        if not self.enabled:
            logger.debug("CostGuard disabled, skipping checks")
            return True, None
        
        # Use conservative estimates if not provided
        if estimated_segments is None:
            estimated_segments = 30  # Conservative estimate
        if estimated_edits is None:
            estimated_edits = 10  # Conservative estimate
        
        estimates = self.estimate_request_tokens(
            command_text, estimated_segments, estimated_edits
        )
        
        logger.info(f"CostGuard: Estimated tokens: {estimates}")
        
        # Check total request limit
        if estimates["total"] > self.max_tokens_per_request:
            error = {
                "error": "token_limit_exceeded",
                "message": f"Estimated tokens ({estimates['total']}) exceed request limit ({self.max_tokens_per_request})",
                "estimated_tokens": estimates["total"],
                "limit": self.max_tokens_per_request,
                "breakdown": {k: v for k, v in estimates.items() if k != "total"}
            }
            logger.warning(f"CostGuard: Request limit exceeded: {error}")
            return False, error
        
        # Check per-agent limits
        for agent, tokens in estimates.items():
            if agent == "total":
                continue
            if tokens > self.max_tokens_per_agent:
                error = {
                    "error": "agent_token_limit_exceeded",
                    "message": f"{agent} estimated tokens ({tokens}) exceed agent limit ({self.max_tokens_per_agent})",
                    "estimated_tokens": tokens,
                    "limit": self.max_tokens_per_agent,
                    "agent": agent
                }
                logger.warning(f"CostGuard: Agent limit exceeded: {error}")
                return False, error
        
        logger.info(f"CostGuard: Checks passed (total={estimates['total']} tokens)")
        return True, None
    
    def check_agent_limit(self, agent_name: str, estimated_tokens: int) -> Tuple[bool, Optional[Dict]]:
        """
        Check if single agent would exceed limit.
        
        Args:
            agent_name: Name of agent
            estimated_tokens: Estimated token count
            
        Returns:
            (passes: bool, error_detail: Optional[Dict])
        """
        if not self.enabled:
            return True, None
        
        if estimated_tokens > self.max_tokens_per_agent:
            error = {
                "error": "agent_token_limit_exceeded",
                "message": f"{agent_name} estimated tokens ({estimated_tokens}) exceed limit ({self.max_tokens_per_agent})",
                "estimated_tokens": estimated_tokens,
                "limit": self.max_tokens_per_agent,
                "agent": agent_name
            }
            logger.warning(f"CostGuard: {agent_name} limit exceeded")
            return False, error
        
        return True, None
    
    def estimate_compile_tokens(self, edit_count: int) -> int:
        """
        Estimate tokens for timeline compilation (no LLM, just validation).
        
        Args:
            edit_count: Number of edit actions
            
        Returns:
            Estimated token count (0 for no LLM)
        """
        # Timeline compiler doesn't use LLM
        return 0
    
    def get_cost_summary(self, estimates: Dict[str, int]) -> Dict:
        """
        Generate cost summary for logging/response.
        
        Args:
            estimates: Token estimates dict
            
        Returns:
            Summary dict with costs and limits
        """
        total = estimates.get("total", 0)
        
        # Rough cost estimation (based on OpenAI pricing)
        # Input: ~$0.01 per 1K tokens, Output: ~$0.03 per 1K tokens
        # Using blended rate of ~$0.02 per 1K tokens
        estimated_cost_usd = (total / 1000) * 0.02
        
        return {
            "estimated_tokens": total,
            "max_tokens_per_request": self.max_tokens_per_request,
            "utilization_percentage": round((total / self.max_tokens_per_request) * 100, 2),
            "estimated_cost_usd": round(estimated_cost_usd, 4),
            "breakdown": {k: v for k, v in estimates.items() if k != "total"},
            "guard_enabled": self.enabled
        }

# Global instance
cost_guard = CostGuard()

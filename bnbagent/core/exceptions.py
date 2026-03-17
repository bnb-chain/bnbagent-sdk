"""
Custom exceptions for bnbagent SDK.

Provides a hierarchy of domain-specific exceptions for better error handling
and propagation throughout the SDK.
"""


class BNBAgentError(Exception):
    """Base exception for all bnbagent SDK errors."""
    pass


class ContractError(BNBAgentError):
    """
    Contract call or transaction failed.
    
    Raised when a smart contract interaction fails, including:
    - Transaction reverts
    - Gas estimation failures
    - Invalid function calls
    """
    pass


class StorageError(BNBAgentError):
    """
    Storage operation failed.
    
    Raised when file or IPFS storage operations fail, including:
    - File not found
    - Upload/download failures
    - Invalid data format
    """
    pass


class ConfigurationError(BNBAgentError):
    """
    Missing or invalid configuration.
    
    Raised when required configuration is missing or invalid, including:
    - Missing environment variables
    - Invalid contract addresses
    - Missing private keys
    """
    pass


class ABILoadError(BNBAgentError):
    """
    Failed to load contract ABI.
    
    Raised when ABI file loading fails, including:
    - File not found
    - Invalid JSON format
    """
    pass


class NetworkError(BNBAgentError):
    """
    Network or RPC communication failed.
    
    Raised when network operations fail, including:
    - RPC connection errors
    - Rate limiting (429)
    - Timeouts
    """
    pass


class JobError(BNBAgentError):
    """
    Job operation failed.
    
    Raised when job-related operations fail, including:
    - Job not found
    - Invalid job state
    - Unauthorized access
    """
    pass


class NegotiationError(BNBAgentError):
    """
    Negotiation failed.
    
    Raised when negotiation operations fail, including:
    - Price validation errors
    - Invalid terms
    - Unsupported service types
    """
    pass

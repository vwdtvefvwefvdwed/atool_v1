class WorkflowError(Exception):
    pass

class RetryableError(WorkflowError):
    def __init__(
        self, 
        message: str, 
        error_type: str = 'generic_api_error', 
        retry_count: int = 0, 
        retry_after: int = None,
        model: str = None,
        provider: str = None
    ):
        super().__init__(message)
        self.error_type = error_type
        self.retry_count = retry_count
        self.retry_after = retry_after
        self.model = model
        self.provider = provider

class HardError(WorkflowError):
    pass

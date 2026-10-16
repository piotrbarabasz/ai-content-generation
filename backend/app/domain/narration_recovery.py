"""Provider-neutral preflight failure delivered to the workflow without re-approval."""
class NarrationPreflightError(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__(report['summary'])

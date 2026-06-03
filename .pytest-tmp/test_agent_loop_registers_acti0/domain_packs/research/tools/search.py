from OpenHome.agent.tools.base import Tool

class ResearchSearchTool(Tool):
    name = 'research_search'
    @property
    def description(self):
        return 'search'
    @property
    def parameters(self):
        return {'type': 'object', 'properties': {}, 'additionalProperties': False}
    @property
    def read_only(self):
        return True
    async def execute(self, **kwargs):
        return 'ok'


import asyncio
from unittest.mock import MagicMock, AsyncMock
from dotagent.agent.executor import _run_checked, _LOW_RISK_TOOLS, _TOOL_FAILOVERS
from dotagent.tools.context import ToolContext
from dotagent.agent.gate import RunGate
from dotagent.tools.base import RegisteredTool, ToolSpec, ToolResult, ok, fail
from pydantic import BaseModel

class MockRuntime:
    def __init__(self):
        self.peers = MagicMock()
        self.peers.conflict.return_value = None
        self.peers.take.return_value = None
        self.store = MagicMock()
        self.store.save_images = AsyncMock(return_value=[])
        self.config = MagicMock()

class MockToolSpec(ToolSpec):
    def __init__(self, name, timeout_s=10, **kwargs):
        super().__init__(name=name, timeout_s=timeout_s, **kwargs)

class MockTool(RegisteredTool):
    def __init__(self, spec, run_result=None):
        self.spec = spec
        self.run_result = run_result
    async def prepare(self, args, ctx):
        return MagicMock(action=MagicMock(kind="test", target="test", risk="low", flags=[]), weighed=0)
    async def run(self, ctx):
        if self.run_result is None:
            return ok("Success")
        return self.run_result

class MockTool:
    def __init__(self, spec_name, run_result=None):
        self.spec = MagicMock()
        self.spec.name = spec_name
        self.spec.timeout_s = 10.0
        self.timeout_s = 10.0
        self.run_result = run_result
    async def prepare(self, args, ctx):
        prepared = MagicMock()
        prepared.action = MagicMock(kind="test", target="test", risk="low", flags=[])
        prepared.weighed = 0
        prepared.run = AsyncMock(side_effect=self._run_impl)
        return prepared
    async def _run_impl(self, ctx):
        if self.run_result is None:
            return ok("Success")
        return self.run_result

async def test_speculative_execution():
    print("Testing Speculative Execution...")
    runtime = MockRuntime()
    # Create a real ToolContext instance with minimal mocks for required fields.
    ctx = ToolContext(
        runtime=runtime,
        run_id="test",
        mode="auto",
        output_chars=1000,
        browser=MagicMock(),
        agent_id="test-agent",
        thread_id="test-thread",
        approvals="auto",
        questions=[],
        workspace="/tmp/workspace",
        gate=MagicMock(spec=RunGate),
        cancel=AsyncMock(),
        facts=[]
    )
    gate = ctx.gate
    gate.authorize_recorded = AsyncMock(return_value=MagicMock(note="", decision="approved"))
    gate.wait_free = AsyncMock(return_value=0.0)
    gate.observe = MagicMock()
    gate.tainted = False

    # Low risk tool
    tool = MockTool("web_search")
    call = MagicMock(name="web_search", arguments={})
    
    result = await _run_checked(call, tool, ctx, gate)
    assert result.result.text == "Success"
    print("✅ Speculative Execution Passed")

async def test_failover():
    print("Testing Tool-Chain Failover...")
    runtime = MockRuntime()
    ctx = ToolContext(
        runtime=runtime,
        run_id="test",
        mode="auto",
        output_chars=1000,
        browser=MagicMock(),
        agent_id="test-agent",
        thread_id="test-thread",
        approvals="auto",
        questions=[],
        workspace="/tmp/workspace",
        gate=MagicMock(spec=RunGate),
        cancel=AsyncMock(),
        facts=[]
    )
    gate = ctx.gate
    gate.authorize_recorded = AsyncMock(return_value=MagicMock(note="", decision="approved"))
    gate.wait_free = AsyncMock(return_value=0.0)
    gate.observe = MagicMock()
    gate.tainted = False

    # browser_act fails first, then we want to see it try something else
    import dotagent.agent.executor
    original_registry = dotagent.agent.executor.ToolRegistry
    
    class MockRegistry:
        def __init__(self):
            self.tools = {}
            
            t1 = MockTool("browser_act", run_result=fail("Element not found"))
            t2 = MockTool("browser_find", run_result=ok("Found it!"))
            
            self.tools = {"browser_act": t1, "browser_find": t2}
    
    dotagent.agent.executor.ToolRegistry = MockRegistry
    
    tool = MockRegistry().tools["browser_act"]
    call = MagicMock(name="browser_act", arguments={})
    
    result = await _run_checked(call, tool, ctx, gate)
    assert result.result.text == "Found it!"
    print("✅ Tool-Chain Failover Passed")
    
    dotagent.agent.executor.ToolRegistry = original_registry

async def main():
    try:
        await test_speculative_execution()
        await test_failover()
        print("\nAll tests passed successfully!")
    except Exception as e:
        print(f"\n❌ Tests failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())

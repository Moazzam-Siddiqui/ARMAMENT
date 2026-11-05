from sentinel_ops.tools.write import register_write_tools


class ToolRegistry:
    def __init__(self) -> None:
        self.registered = {}

    def tool(self, *, annotations, description):
        def register(function):
            self.registered[function.__name__] = {
                "annotations": annotations,
                "description": description,
            }
            return function

        return register


def test_every_state_changing_tool_is_marked_destructive() -> None:
    registry = ToolRegistry()
    register_write_tools(registry, docker=object())

    assert set(registry.registered) == {
        "restart_service",
        "raise_memory_limit",
        "rollback_deploy",
    }
    for tool in registry.registered.values():
        annotations = tool["annotations"]
        assert annotations.destructive_hint is True
        assert annotations.read_only_hint is False
        assert annotations.idempotent_hint is False
        assert tool["description"]

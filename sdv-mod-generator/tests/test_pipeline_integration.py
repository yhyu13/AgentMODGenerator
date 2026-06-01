"""Pipeline integration tests — full end-to-end pipeline."""
import pytest

try:
    from orchestrator.pipeline import run_pipeline, node_route, node_generate, node_t1_gate, node_package
    from orchestrator.state import PipelineState
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    pytestmark = pytest.mark.skip(reason="langgraph not installed")


class TestPipelineState:
    def test_initial_state(self):
        state = PipelineState(
            request_id="req_test",
            user_id="test_user",
            prompt="make a tv shopping channel",
        )
        assert state.request_id == "req_test"
        assert state.status == "pending"
        assert state.game == "stardew_valley"
        assert state.generators == []
        assert state.outputs == {}

    def test_state_with_game_and_phase(self):
        state = PipelineState(
            request_id="req_test",
            user_id="test_user",
            prompt="texture mod",
            game="stardew_valley",
            phase="texture",
        )
        assert state.game == "stardew_valley"
        assert state.phase == "texture"


class TestNodeRoute:
    def test_route_sets_game_and_phase(self):
        state = PipelineState(
            request_id="req_test",
            user_id="test_user",
            prompt="make a tv shopping channel",
        )
        result = node_route(state)
        assert result.game == "stardew_valley"
        assert result.phase == "shop_channel"
        assert len(result.generators) > 0
        assert result.status == "routing"

    def test_route_texture(self):
        state = PipelineState(
            request_id="req_test",
            user_id="test_user",
            prompt="replace a crop texture",
        )
        result = node_route(state)
        assert result.phase == "texture"
        assert result.generators == ["texture_generator"]


class TestNodeGenerate:
    @pytest.mark.asyncio
    async def test_generate_shop_channel(self):
        state = PipelineState(
            request_id="req_test",
            user_id="test_user",
            prompt="tv shopping channel",
            game="stardew_valley",
            phase="shop_channel",
            generators=["manifest_generator", "shop_item_pool_generator"],
        )
        result = await node_generate(state)
        assert "manifest_generator" in result.outputs
        assert "shop_item_pool_generator" in result.outputs
        assert result.status == "generating"
        assert len(result.errors) == 0

    @pytest.mark.asyncio
    async def test_generate_texture(self):
        state = PipelineState(
            request_id="req_test",
            user_id="test_user",
            prompt="replace sprite",
            game="stardew_valley",
            phase="texture",
            generators=["texture_generator"],
        )
        result = await node_generate(state)
        assert "texture_generator" in result.outputs
        assert result.status == "generating"


class TestNodeT1Gate:
    def test_t1_gate_passes_valid_output(self):
        from generators.core import GeneratorOutput
        state = PipelineState(
            request_id="req_test",
            user_id="test_user",
            prompt="shop",
            game="stardew_valley",
            phase="shop_channel",
        )
        manifest_out = GeneratorOutput()
        manifest_out.add_file("manifest.json", {
            "Format": "1.29.0",
            "UniqueID": "test_mod",
            "Name": "Test",
            "Version": "1.0.0",
            "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"},
        })
        shop_out = GeneratorOutput()
        shop_out.add_file("assets/data/shops.tsv", "ItemType\tItemName\tItemName2\tPrice\tStock\nObject\tTest\t\t100\t1")
        config_out = GeneratorOutput()
        config_out.add_file("config.json", {"Enabled": True})
        trigger_out = GeneratorOutput()
        trigger_out.add_file("data/trigger_actions.json", {"OnShopOpen": []})
        mail_out = GeneratorOutput()
        mail_out.add_file("mail/tv_shopping_broadcast.json", {"tv_shopping_broadcast": "Dear @, ^Welcome!^  - TVSN"})
        tv_out = GeneratorOutput()
        tv_out.add_file("assets/data/tv_channels.json", {"channels": [{"Name": "TV Shopping Network", "ChannelID": "tv_shopping_network"}]})
        content_out = GeneratorOutput()
        content_out.add_file("content.json", [
            {"Action": "EditData", "Target": "Data/tvChannels", "Entries": {"tv_shopping_network": {"Name": "TV Shopping Network", "ChannelID": "tv_shopping_network"}}},
            {"Action": "EditData", "Target": "Data/mail", "Entries": {"tv_shopping_broadcast": {"text": "Dear @, ^Welcome!^  - TVSN", "broadcast": True}}},
        ])
        state.outputs = {
            "manifest_generator": manifest_out,
            "shop_item_pool_generator": shop_out,
            "config_schema_generator": config_out,
            "trigger_logic_generator": trigger_out,
            "mail_system_generator": mail_out,
            "tv_channel_generator": tv_out,
            "content_json_generator": content_out,
        }
        result = node_t1_gate(state)
        assert result.t1_passed is True
        assert result.status != "failed"


class TestNodePackage:
    @pytest.mark.asyncio
    async def test_package_creates_zip_key(self):
        from generators.core import GeneratorOutput
        state = PipelineState(
            request_id="req_test",
            user_id="test_user",
            prompt="shop",
            game="stardew_valley",
            phase="shop_channel",
        )
        out = GeneratorOutput()
        out.add_file("manifest.json", {"Format": "1.29.0"})
        state.outputs = {"manifest_generator": out}
        result = await node_package(state)
        assert result.zip_key is not None
        assert "req_test" in result.zip_key
        assert result.zip_key.endswith(".zip")


class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_full_pipeline_shop_channel(self):
        result = await run_pipeline("req_pipeline_test", "test_user", "tv shopping channel")
        assert result.status == "done"
        assert result.zip_key is not None
        assert len(result.outputs) > 0
        assert result.t1_passed is True
        assert result.t2_score is not None
        assert 0 <= result.t2_score <= 10

    @pytest.mark.asyncio
    async def test_full_pipeline_texture(self):
        result = await run_pipeline("req_tex_test", "test_user", "replace crop texture")
        assert result.status == "done"
        assert result.zip_key is not None
        assert "texture_generator" in result.outputs
        assert result.t1_passed is True

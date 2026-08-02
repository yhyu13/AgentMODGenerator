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
        # Manifest-first order since the MVP audit (texture was
        # manifestless and produced unloadable zips standalone).
        assert result.generators == ["manifest_generator", "texture_generator"]


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

    @pytest.mark.asyncio
    async def test_package_fails_on_missing_asset(self):
        from generators.core import GeneratorOutput
        state = PipelineState(
            request_id="req_missing_asset",
            user_id="test_user",
            prompt="shop",
            game="stardew_valley",
            phase="shop_channel",
        )
        out = GeneratorOutput()
        out.add_file("manifest.json", {"Format": "1.29.0"})
        out.add_asset("/nonexistent/path/sprite.png")
        state.outputs = {"manifest_generator": out}
        result = await node_package(state)
        assert result.status == "failed"
        assert result.zip_key is None
        assert any("sprite.png" in e for e in result.errors), f"error should include asset path, got: {result.errors}"

    @pytest.mark.asyncio
    async def test_package_fails_on_absolute_asset_path(self):
        import os
        from generators.core import GeneratorOutput
        state = PipelineState(
            request_id="req_abs_asset",
            user_id="test_user",
            prompt="shop",
            game="stardew_valley",
            phase="shop_channel",
        )
        out = GeneratorOutput()
        out.add_file("manifest.json", {"Format": "1.29.0"})
        # A path that is absolute on the running platform: /etc/passwd on
        # POSIX, a Windows system file on nt. /etc/passwd is NOT absolute
        # under ntpath, so the packager's isabs check wouldn't fire.
        if os.name == "nt":
            absolute_asset = os.path.abspath(
                os.path.join(os.environ.get("WINDIR", "C:/Windows"), "win.ini")
            )
        else:
            absolute_asset = "/etc/passwd"
        out.add_asset(absolute_asset)
        state.outputs = {"manifest_generator": out}
        result = await node_package(state)
        assert result.status == "failed"
        assert any("absolute" in e.lower() for e in result.errors)


class TestNodeGenerateFailureHandling:
    """Verify a single bad generator does not fail-stop the pipeline.

    Replaces the old behavior where the first generator exception aborted
    the whole pipeline (the "swallowed errors" pattern from AGENTS.md).
    """

    @pytest.mark.asyncio
    async def test_failed_generator_surfaced_not_swallowed(self):
        from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput
        from orchestrator.pipeline import node_generate

        class GoodGenerator(BaseGenerator):
            name = "good_generator"
            phase = "shop_channel"
            game = "stardew_valley"

            async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
                out = GeneratorOutput()
                out.add_file("manifest.json", {"Format": "1.29.0"})
                return out

        class BadGenerator(BaseGenerator):
            name = "bad_tv_generator"
            phase = "shop_channel"
            game = "stardew_valley"

            async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
                from pydantic import ValidationError
                raise ValidationError.from_exception_data(
                    "TVChannelOutput",
                    [{"type": "missing", "loc": ("Channels",), "input": {}}],
                )

        from unittest.mock import patch
        with patch("orchestrator.pipeline.get_game_pack") as mock_pack:
            mock_pack.return_value.get_generator.side_effect = lambda n, p: {
                "good_generator": GoodGenerator,
                "bad_tv_generator": BadGenerator,
            }[n]
            state = PipelineState(
                request_id="req_iter3",
                user_id="test_user",
                prompt="make a tv shopping channel",
                game="stardew_valley",
                phase="shop_channel",
                generators=["good_generator", "bad_tv_generator"],
            )
            result = await node_generate(state)

        assert "good_generator" in result.outputs, f"good gen should run, got outputs: {list(result.outputs.keys())}"
        assert "bad_tv_generator" in result.generators_failed, f"bad gen should be in failed list, got: {result.generators_failed}"
        assert "good_generator" in result.generators_succeeded
        assert "bad_tv_generator" not in result.generators_succeeded
        assert any("bad_tv_generator" in e for e in result.errors), f"errors should include generator name, got: {result.errors}"
        assert any("ValidationError" in e for e in result.errors), f"errors should include exception type, got: {result.errors}"
        assert result.status != "failed", f"status should not be failed (pipeline continues), got: {result.status}"

    @pytest.mark.asyncio
    async def test_all_generators_failed_means_status_failed(self):
        from generators.core import BaseGenerator, GeneratorInput, GeneratorOutput
        from orchestrator.pipeline import node_generate

        class BadGenerator(BaseGenerator):
            name = "bad_one"
            phase = "shop_channel"
            game = "stardew_valley"

            async def generate(self, inp: GeneratorInput) -> GeneratorOutput:
                raise RuntimeError("intentional failure")

        from unittest.mock import patch
        with patch("orchestrator.pipeline.get_game_pack") as mock_pack:
            mock_pack.return_value.get_generator.return_value = BadGenerator
            state = PipelineState(
                request_id="req_all_bad",
                user_id="test_user",
                prompt="x",
                game="stardew_valley",
                phase="shop_channel",
                generators=["bad_one"],
            )
            result = await node_generate(state)

        assert result.status == "failed"
        assert "bad_one" in result.generators_failed
        assert not result.outputs


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

    @pytest.mark.asyncio
    async def test_full_pipeline_custom_crafting(self):
        result = await run_pipeline("req_crafting_test", "test_user", "add custom crafting recipes")
        assert result.status == "done"
        assert result.zip_key is not None
        assert "crafting_recipe_generator" in result.outputs
        assert "cooking_recipe_generator" in result.outputs
        assert "crafting_content_json_generator" in result.outputs
        assert result.t1_passed is True

"""NPC schedule generator tests — unit tests with mocked LLM calls."""

import pytest

from generators.core import GeneratorInput, GeneratorOutput
from generators.packs.stardew_valley.features.npc_schedule import (
    NPCScheduleGenerator,
    NPCDialogueGenerator,
    NPCGiftTasteGenerator,
    NPCContentJsonGenerator,
)


def make_input(prompt: str = "test prompt", prior: dict | None = None) -> GeneratorInput:
    return {
        "prompt": prompt,
        "hint": {},
        "request_id": "req_test",
        "game": "stardew_valley",
        "prior_outputs": prior or {},
        "t2_feedback": "",
    }


class TestNPCScheduleGenerator:
    @pytest.mark.asyncio
    async def test_npc_schedule_fallback_no_llm(self):
        gen = NPCScheduleGenerator()
        out = await gen.generate(make_input("create NPC schedule"))
        schedule_files = [k for k in out.files if k.startswith("assets/schedules/")]
        assert len(schedule_files) >= 1
        schedule = out.files[schedule_files[0]]
        assert "name" in schedule

    @pytest.mark.asyncio
    async def test_schedule_fallback_defaults_to_real_npc(self):
        gen = NPCScheduleGenerator()
        out = await gen.generate(make_input("create NPC schedule"))
        assert out.metadata.get("npc_name") == "Linus"

    @pytest.mark.asyncio
    async def test_validate_passes_with_schedule(self):
        gen = NPCScheduleGenerator()
        out = await gen.generate(make_input())
        errors = gen.validate_output(out)
        assert errors == []

    @pytest.mark.asyncio
    async def test_validate_fails_on_missing(self):
        gen = NPCScheduleGenerator()
        out = GeneratorOutput()
        errors = gen.validate_output(out)
        assert len(errors) >= 1


class TestNPCDialogueGenerator:
    @pytest.mark.asyncio
    async def test_npc_dialogue_fallback_no_llm(self):
        gen = NPCDialogueGenerator()
        out = await gen.generate(make_input("create NPC dialogue"))
        dialogue_files = [k for k in out.files if k.startswith("assets/dialogue/")]
        assert len(dialogue_files) >= 1
        dialogue = out.files[dialogue_files[0]]
        assert isinstance(dialogue, dict)

    @pytest.mark.asyncio
    async def test_dialogue_uses_prior_npc_name(self):
        prior_out = GeneratorOutput()
        prior_out.metadata["npc_name"] = "TestNPC"
        prior = {"npc_schedule_generator": prior_out}
        gen = NPCDialogueGenerator()
        out = await gen.generate(make_input("dialogue for TestNPC", prior))
        assert out.metadata.get("npc_name") == "TestNPC"


class TestNPCGiftTasteGenerator:
    @pytest.mark.asyncio
    async def test_npc_gift_taste_fallback_no_llm(self):
        gen = NPCGiftTasteGenerator()
        out = await gen.generate(make_input("create gift tastes"))
        taste_files = [k for k in out.files if k.startswith("assets/gift_tastes/")]
        assert len(taste_files) >= 1
        tastes = out.files[taste_files[0]]
        assert "Loves" in tastes
        assert "Hates" in tastes


class TestNPCContentJsonGenerator:
    @pytest.mark.asyncio
    async def test_npc_content_json_builds_changes(self):
        manifest_out = GeneratorOutput()
        manifest_out.add_file("manifest.json", {
            "Format": "1.29.0",
            "UniqueID": "TestNPCMod",
            "Name": "Test NPC Mod",
            "Version": "1.0.0",
            "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"},
        })
        schedule_out = GeneratorOutput()
        schedule_out.add_file("assets/schedules/TestNPC.json", {"name": "TestNPC"})
        schedule_out.metadata["npc_name"] = "TestNPC"
        dialogue_out = GeneratorOutput()
        dialogue_out.add_file("assets/dialogue/TestNPC.json", {"Mon": "Hi!"})
        dialogue_out.metadata["npc_name"] = "TestNPC"
        taste_out = GeneratorOutput()
        taste_out.add_file("assets/gift_tastes/TestNPC.json", {"NPCName": "TestNPC"})
        taste_out.metadata["npc_name"] = "TestNPC"

        prior = {
            "manifest_generator": manifest_out,
            "npc_schedule_generator": schedule_out,
            "npc_dialogue_generator": dialogue_out,
            "npc_gift_taste_generator": taste_out,
        }
        gen = NPCContentJsonGenerator()
        out = await gen.generate(make_input("full npc mod", prior))
        assert "content.json" in out.files
        content = out.files["content.json"]
        assert isinstance(content, dict)
        assert "Changes" in content
        assert len(content["Changes"]) >= 1

    @pytest.mark.asyncio
    async def test_validate_passes(self):
        manifest_out = GeneratorOutput()
        manifest_out.add_file("manifest.json", {"UniqueID": "TestNPCMod"})
        schedule_out = GeneratorOutput()
        schedule_out.add_file("assets/schedules/TestNPC.json", {"name": "TestNPC"})
        schedule_out.metadata["npc_name"] = "TestNPC"
        prior = {
            "manifest_generator": manifest_out,
            "npc_schedule_generator": schedule_out,
        }
        gen = NPCContentJsonGenerator()
        out = await gen.generate(make_input("npc mod", prior))
        errors = gen.validate_output(out)
        assert errors == []

    @pytest.mark.asyncio
    async def test_schedule_dialogue_patches_use_load(self):
        manifest_out = GeneratorOutput()
        manifest_out.add_file("manifest.json", {"UniqueID": "TestNPCMod"})
        schedule_out = GeneratorOutput()
        schedule_out.add_file("assets/schedules/Linus.json", {"name": "Linus"})
        schedule_out.metadata["npc_name"] = "Linus"
        dialogue_out = GeneratorOutput()
        dialogue_out.add_file("assets/dialogue/Linus.json", {"Mon": "Hi!"})
        dialogue_out.metadata["npc_name"] = "Linus"
        taste_out = GeneratorOutput()
        taste_out.add_file("assets/gift_tastes/Linus.json", {"NPCName": "Linus"})
        taste_out.metadata["npc_name"] = "Linus"

        prior = {
            "manifest_generator": manifest_out,
            "npc_schedule_generator": schedule_out,
            "npc_dialogue_generator": dialogue_out,
            "npc_gift_taste_generator": taste_out,
        }
        gen = NPCContentJsonGenerator()
        out = await gen.generate(make_input("full npc mod", prior))
        content = out.files["content.json"]
        schedule_targets = []
        for change in content["Changes"]:
            target = change.get("Target", "")
            assert "UnknownNPC" not in target
            if "FromFile" in change:
                assert change["Action"] == "Load"
                if target.startswith("Characters/Schedules/"):
                    schedule_targets.append(target)
        assert "Characters/Schedules/Linus" in schedule_targets

    @pytest.mark.asyncio
    async def test_content_json_defaults_to_real_npc_without_metadata(self):
        manifest_out = GeneratorOutput()
        manifest_out.add_file("manifest.json", {"UniqueID": "TestNPCMod"})
        schedule_out = GeneratorOutput()
        schedule_out.add_file("assets/schedules/Linus.json", {"name": "Linus"})
        dialogue_out = GeneratorOutput()
        dialogue_out.add_file("assets/dialogue/Linus.json", {"Mon": "Hi!"})

        prior = {
            "manifest_generator": manifest_out,
            "npc_schedule_generator": schedule_out,
            "npc_dialogue_generator": dialogue_out,
        }
        gen = NPCContentJsonGenerator()
        out = await gen.generate(make_input("npc mod", prior))
        content = out.files["content.json"]
        targets = [c.get("Target", "") for c in content["Changes"]]
        assert "Characters/Schedules/Linus" in targets
        assert all("UnknownNPC" not in t for t in targets)

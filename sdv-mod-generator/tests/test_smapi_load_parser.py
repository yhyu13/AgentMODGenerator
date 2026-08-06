"""Unit tests for the SMAPI log parser — no game install required.

Pins ``tests/smapi_load_parser.py`` to the real failure phrases SMAPI
and Content Patcher emit, including the CP skip warnings the current
buggy demo mods produce at load time.
"""
from tests.smapi_load_parser import find_smapi_failures


def test_empty_log_has_no_failures() -> None:
    assert find_smapi_failures("") == []


def test_clean_load_has_no_failures() -> None:
    log = (
        "[10:00:00 INFO  SMAPI] SMAPI 4.5.2 with Stardew Valley 1.6.15 build 24356 on Windows 10\n"
        "[10:00:06 DEBUG SMAPI] Mods loaded and ready!\n"
        "[10:00:07 TRACE game] Successfully set GOG Galaxy profile name.\n"
    )
    assert find_smapi_failures(log) == []


def test_this_mod_failed() -> None:
    log = "[10:00:05 ERROR SMAPI] Some Mod: this mod failed. Its error log contains more details.\n"
    assert find_smapi_failures(log) == [log.strip()]


def test_could_not_be_loaded() -> None:
    log = "[10:00:05 ERROR SMAPI] Some Mod: this mod could not be loaded.\n"
    assert len(find_smapi_failures(log)) == 1


def test_error_loading_mod() -> None:
    log = "[10:00:05 ERROR SMAPI] Error loading mod 'foo' from Mods/foo: ...\n"
    assert len(find_smapi_failures(log)) == 1


def test_mod_failed_to_load() -> None:
    log = "[10:00:05 ERROR SMAPI] A mod failed to load. Try removing it and restarting.\n"
    assert len(find_smapi_failures(log)) == 1


def test_cant_apply_data_patch() -> None:
    log = (
        "[10:00:05 WARN  Content Patcher] Can't apply data patch \"Foo > EditData Data/Weapons > entry #1\""
        " to Data/Weapons: failed converting entry to the expected type 'System.String'.\n"
    )
    assert len(find_smapi_failures(log)) == 1


def test_ignored_fromfile_with_editdata() -> None:
    log = (
        "[10:00:05 WARN  Content Patcher] Ignored Foo > EditData Data/Shops/Bar: "
        "the FromFile field can't be used with an 'EditData' patch.\n"
    )
    assert len(find_smapi_failures(log)) == 1


def test_ignored_position_is_invalid() -> None:
    log = (
        "[10:00:05 WARN  Content Patcher] Ignored Foo > EditMap Maps/Farm #1: "
        "MapTiles > entry #1 > Position is invalid: the tile position is required.\n"
    )
    assert len(find_smapi_failures(log)) == 1


def test_ignored_when_token_could_not_be_found() -> None:
    log = (
        "[10:00:05 WARN  Content Patcher] Ignored Foo > Load mods/foo/bar: the When field is invalid: "
        "'DayOfMonth' can't be used as a token because that token could not be found.\n"
    )
    assert len(find_smapi_failures(log)) == 1


def test_ignored_without_invalid_marker_is_benign() -> None:
    log = "[10:00:05 INFO  SMAPI] Ignored a duplicate mod entry with no update key.\n"
    assert find_smapi_failures(log) == []


def test_runtime_migration_warning_is_not_failed() -> None:
    log = (
        "[10:00:05 WARN  Content Patcher] Data patch \"Foo > EditData Data/Weapons\" reported warnings "
        "when applying runtime migration 2.0.0.\n"
    )
    assert find_smapi_failures(log) == []


def test_update_check_errors_are_not_failed() -> None:
    log = (
        "[10:00:05 TRACE SMAPI] Got update-check errors for some mods:\n"
        "   BroadcastAPI: The CurseForge mod with ID '1475866' has no valid versions.\n"
    )
    assert find_smapi_failures(log) == []


def test_case_insensitive() -> None:
    log = "THIS MOD FAILED TO LOAD.\n"
    assert len(find_smapi_failures(log)) == 1


def test_returns_all_offending_lines_in_order() -> None:
    log = (
        "line one: this mod failed\n"
        "clean line\n"
        "line three: Can't apply data patch X\n"
        "line four: Ignored Y > EditMap > Position is invalid\n"
    )
    failures = find_smapi_failures(log)
    assert len(failures) == 3
    assert "line one" in failures[0]
    assert "line three" in failures[1]
    assert "line four" in failures[2]

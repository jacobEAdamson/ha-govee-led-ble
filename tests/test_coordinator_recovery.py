"""Coordinator control-state capture and restoration tests."""

from unittest.mock import AsyncMock, call, patch

import pytest

from custom_components.ha_govee_led_ble.const import MODEL_PROFILES, MUSIC_MODE_SLUGS
from custom_components.ha_govee_led_ble.control_arbiter import ControlIntent
from custom_components.ha_govee_led_ble.coordinator import GoveeBLECoordinator
from custom_components.ha_govee_led_ble.coordinator_status import ParsedMode
from custom_components.ha_govee_led_ble.effect_commands import build_h617a_diy_activation
from custom_components.ha_govee_led_ble.effect_deployments import PriorControlState
from custom_components.ha_govee_led_ble.generated_protocol_adapter import (
    build_blank_screen,
    build_brightness,
    build_power,
    build_scene_activation,
)
from custom_components.ha_govee_led_ble.scenes import MODEL_SCENES, scene_selector_code
from custom_components.ha_govee_led_ble.transport import WRITE_UUID
from custom_components.ha_govee_led_ble.video_applicability import video_identity_fields
from tests.coordinator_test_support import _CONFIGURATION_URL, _c, _packet

M = "custom_components.ha_govee_led_ble.coordinator"


def test_capture_effect_control_state(coord):
    coord.is_on = True
    coord.brightness_pct = 72
    coord.rgb_color = (1, 2, 3)
    coord.music_sensitivity = 50

    state = coord.capture_effect_control_state()

    assert state == PriorControlState(
        mode="colour",
        music_model="H617A",
        music_parameters={},
        is_on=True,
        brightness_pct=72,
        rgb_color=(1, 2, 3),
        music_sensitivity=50,
    )


async def test_restore_effect_control_state_legacy_static_never_flattens(coord):
    state = PriorControlState(
        mode="colour",
        is_on=True,
        brightness_pct=72,
        rgb_color=(1, 2, 3),
    )

    with (
        patch.object(coord, "send_command", new_callable=AsyncMock) as send,
        patch.object(coord, "refresh_state", new_callable=AsyncMock, return_value=True) as refresh,
    ):
        recovered = await coord.async_restore_effect_control_state(
            state,
            overwritten_diy_code=800,
        )

    assert recovered is False
    send.assert_not_awaited()
    refresh.assert_not_awaited()


async def test_restore_effect_control_state_cannot_recover_overwritten_diy_slot(coord):
    state = PriorControlState(
        mode="custom",
        is_on=True,
        brightness_pct=72,
        rgb_color=(1, 2, 3),
        diy_code=800,
    )

    with patch.object(coord, "send_command", new_callable=AsyncMock) as send:
        recovered = await coord.async_restore_effect_control_state(
            state,
            overwritten_diy_code=800,
        )

    assert recovered is False
    send.assert_not_awaited()


async def test_restore_effect_control_state_reapplies_model_scene(coord, h6199):
    for coordinator in (coord, h6199):
        effect, scene = next((name, entry) for name, entry in MODEL_SCENES[coordinator.model].items() if entry.param)
        state = PriorControlState(
            mode="scene",
            is_on=True,
            brightness_pct=72,
            rgb_color=(1, 2, 3),
            effect=effect,
        )
        expected = [
            build_power(True, coordinator.model),
            build_brightness(72, coordinator.model),
            build_scene_activation(coordinator.model, scene.code, scene.music_code),
        ]

        with (
            patch.object(coordinator, "send_command", new_callable=AsyncMock) as send,
            patch.object(coordinator, "refresh_state", new_callable=AsyncMock, return_value=True) as refresh,
        ):
            recovered = await coordinator.async_restore_effect_control_state(
                state,
                overwritten_diy_code=-1,
            )

        assert recovered is False  # original authored body/speed is unknown
        assert send.await_args_list == [call(packet) for packet in expected]
        refresh.assert_awaited_once_with(expected_on=True, expected_brightness=72, expected_scene_code=scene.code)


async def test_restore_effect_control_state_reapplies_powered_off_state(coord):
    state = PriorControlState(
        mode="colour",
        is_on=False,
        brightness_pct=72,
        rgb_color=(1, 2, 3),
    )

    with (
        patch.object(coord, "send_command", new_callable=AsyncMock) as send,
        patch.object(coord, "refresh_state", new_callable=AsyncMock, return_value=True) as refresh,
    ):
        recovered = await coord.async_restore_effect_control_state(
            state,
            overwritten_diy_code=None,
        )

    assert recovered is False  # power alone cannot recover the missing hidden layout
    send.assert_awaited_once_with(build_power(False), state_values={"is_on": False})
    refresh.assert_awaited_once_with(expected_on=False)


@pytest.mark.parametrize("model", ["H617A", "H9999"])
async def test_restore_effect_control_state_reactivates_unmodified_diy_slot(coord, monkeypatch, model):
    monkeypatch.setitem(MODEL_PROFILES, model, coord.profile)
    coord.model = model
    state = PriorControlState(
        mode="custom",
        is_on=True,
        brightness_pct=72,
        rgb_color=(1, 2, 3),
        diy_code=700,
    )

    with (
        patch.object(coord, "send_command", wraps=coord.send_command) as send,
        patch.object(coord, "_ensure_connected", return_value=_c(write_gatt_char=AsyncMock())),
        patch.object(coord, "async_observe_effect", new_callable=AsyncMock, return_value=True) as refresh,
        patch.object(coord, "refresh_state", new=AsyncMock(return_value=True)),
    ):
        recovered = await coord.async_restore_effect_control_state(
            state,
            overwritten_diy_code=800,
        )

    assert recovered is True
    assert send.await_args_list == [
        call(build_power(True, model)),
        call(build_brightness(72, model)),
        call(build_h617a_diy_activation(700), state_values={"diy_code": 700}),
    ]
    refresh.assert_awaited_once_with({"is_on": True, "diy_code": 700})
    assert coord.diy_code == 700


async def test_restore_effect_control_state_reapplies_complete_music_profile(coord):
    from custom_components.ha_govee_led_ble.music_commands import prepare_music_body_writes
    from custom_components.ha_govee_led_ble.music_semantics import music_variant

    body = music_variant(coord.profile, MUSIC_MODE_SLUGS["separation"]).template
    state = PriorControlState(
        mode="music",
        is_on=True,
        brightness_pct=72,
        rgb_color=(1, 2, 3),
        music_mode="separation",
        music_model="H617A",
        music_body=body,
        music_sensitivity=50,
        music_color=(4, 5, 6),
        music_separation_point=4,
        music_separation_gradient=False,
    )
    with (
        patch.object(coord, "send_command", new=AsyncMock()) as send,
        patch.object(coord, "async_write_music_sequence", new=AsyncMock()) as sequence,
        patch.object(coord, "refresh_state", new_callable=AsyncMock, return_value=True) as refresh,
    ):
        recovered = await coord.async_restore_effect_control_state(
            state,
            overwritten_diy_code=None,
        )

    assert recovered is False  # accepted bytes are not full-settings readback
    sequence.assert_awaited_once_with(
        prepare_music_body_writes("H617A", "separation", 50, body, profile=coord.profile),
        mode_code=MUSIC_MODE_SLUGS["separation"],
        physical_ic_count=None,
        intent=ControlIntent.APPLY,
    )
    send.assert_awaited_once()
    assert send.await_args.args == (build_brightness(72, "H617A"),)
    send.await_args.kwargs["write_guard"]()
    refresh.assert_awaited_once_with(
        expected_on=True, expected_brightness=72, expected_music_mode="separation", expected_music_sensitivity=50
    )


async def test_restore_effect_control_state_reapplies_complete_video_profile(h6199):
    from tests.test_h6199_capabilities import set_qualified_identity

    set_qualified_identity(h6199)
    initial = h6199.capture_effect_control_state()
    state = PriorControlState(
        mode="video",
        is_on=True,
        brightness_pct=72,
        rgb_color=(1, 2, 3),
        video_mode="game",
        video_full_screen=False,
        video_saturation=63,
        video_sound_effects=True,
        video_sound_effects_softness=27,
        white_balance_red=21,
        white_balance_blue=5,
        white_balance_flag=1,
        relative_brightness_left=20,
        relative_brightness_top=30,
        relative_brightness_right=40,
        relative_brightness_bottom=50,
        blank_screen=True,
        blank_screen_detection=2,
        blank_screen_low_brightness_duration_seconds=10,
        blank_screen_same_tone_duration_seconds=120,
    )

    with (
        patch(f"{M}_recovery.apply_white_balance", new_callable=AsyncMock, return_value=True) as white_balance,
        patch(
            f"{M}_recovery.apply_relative_brightness", new_callable=AsyncMock, return_value=True
        ) as relative_brightness,
        patch(f"{M}_recovery.apply_blank_screen", new_callable=AsyncMock, return_value=True) as blank_screen,
        patch(f"{M}_recovery.apply_active_video_mode", new_callable=AsyncMock, return_value=True) as video_mode,
        patch.object(h6199, "send_command", new=AsyncMock()),
        patch.object(h6199, "refresh_state", new=AsyncMock(return_value=True)),
    ):
        recovered = await h6199.async_restore_effect_control_state(
            state,
            overwritten_diy_code=None,
        )

    assert recovered is True
    white_balance.assert_awaited_once_with(h6199, (21, 5), flag=1)
    relative_brightness.assert_awaited_once_with(h6199, (20, 30, 40, 50))
    blank_screen.assert_awaited_once_with(h6199, True)
    video_mode.assert_awaited_once_with(
        h6199,
        parameters=None,
        mode="game",
        requested_values={"full_screen": False, "saturation": 63, "sound_effects": True, "sound_effects_softness": 27},
    )
    assert h6199.capture_effect_control_state() == initial


@pytest.mark.parametrize(
    ("enabled", "policy", "change_during_restore"),
    [
        pytest.param(False, (1, 30, 240), False, id="restore-enable-with-live-policy"),
        pytest.param(True, (1, 30, 240), False, id="policy-only-no-blank-write"),
        pytest.param(False, (None, 30, 240), False, id="unknown-detection"),
        pytest.param(False, (1, None, 240), False, id="unknown-low-duration"),
        pytest.param(False, (1, 30, None), False, id="unknown-same-duration"),
        pytest.param(False, (1, 30, 240), True, id="policy-changes-at-physical-boundary"),
    ],
)
async def test_blank_screen_recovery_preserves_live_policy(h6199, enabled, policy, change_during_restore):
    from tests.test_h6199_capabilities import QUALIFIED

    vars(h6199).update(QUALIFIED)
    h6199._confirmed_identity_fields.update(video_identity_fields(h6199.profile))
    h6199.is_on = True
    h6199.color_mode = ParsedMode.SCENE
    h6199.effect = "candlelight"
    h6199._scene_code = MODEL_SCENES["H6199"]["candlelight"].code
    h6199._notify_callback(None, bytearray(_packet(0xAA, 0xA9, [0x0A, 0x06, 1, 2, 10, 0, 120, 0])))
    state = h6199.capture_effect_control_state()
    h6199.blank_screen = enabled
    (
        h6199.blank_screen_detection,
        h6199.blank_screen_low_brightness_duration_seconds,
        h6199.blank_screen_same_tone_duration_seconds,
    ) = policy
    client = _c(write_gatt_char=AsyncMock())

    async def connect():
        if not client.write_gatt_char.await_count:
            assert h6199.blank_screen is enabled
            assert "blank_screen" not in h6199._expected_state
        if change_during_restore:
            h6199._notify_callback(None, bytearray(_packet(0xAA, 0xA9, [0x0A, 0x06, 0, 0, 60, 0, 44, 1])))
        return client

    async def refresh_policy(**kwargs):
        if kwargs.get("refresh_display_settings") and None not in policy:
            h6199._notify_callback(
                None, bytearray(_packet(0xAA, 0xA9, [0x0A, 0x06, int(enabled), policy[0], policy[1], 0, policy[2], 0]))
            )
        return True

    with (
        patch.object(h6199, "_ensure_connected", new=AsyncMock(side_effect=connect)),
        patch.object(h6199, "refresh_state", new=AsyncMock(side_effect=refresh_policy)) as refresh,
    ):
        if None in policy or change_during_restore:
            message = "policy changed before write" if change_during_restore else "policy state has not been read"
            with pytest.raises(ValueError, match=message):
                await h6199.async_restore_effect_control_state(state, overwritten_diy_code=None)
            client.write_gatt_char.assert_not_awaited()
            refresh.assert_awaited_once_with(refresh_display_settings=frozenset({"blank_screen"}))
            assert h6199.blank_screen is enabled
            assert "blank_screen" not in h6199._expected_state
        else:
            recovered = await h6199.async_restore_effect_control_state(state, overwritten_diy_code=None)
            scene = MODEL_SCENES["H6199"]["candlelight"]
            assert recovered is (not bool(scene.param))
            packets = ([] if enabled else [build_blank_screen(True, "H6199", *policy)]) + [
                build_power(True, "H6199"),
                build_brightness(state.brightness_pct, "H6199"),
                build_scene_activation("H6199", scene.code, scene.music_code),
            ]
            assert client.write_gatt_char.await_args_list == [
                call(WRITE_UUID, packet, response=False) for packet in packets
            ]
            assert refresh.await_args_list == (
                []
                if enabled
                else [call(refresh_display_settings=frozenset({"blank_screen"})), call(expected_blank_screen=True)]
            ) + [call(expected_on=True, expected_brightness=state.brightness_pct, expected_scene_code=scene.code)]
            assert h6199.blank_screen is True
    assert (
        h6199.blank_screen_detection,
        h6199.blank_screen_low_brightness_duration_seconds,
        h6199.blank_screen_same_tone_duration_seconds,
    ) == ((0, 60, 300) if change_during_restore else policy)


async def test_restore_effect_control_state_reapplies_h6199_scene(h6199):
    state = PriorControlState(
        mode="scene",
        is_on=True,
        brightness_pct=72,
        rgb_color=(1, 2, 3),
        effect="forest",
    )
    scene = MODEL_SCENES["H6199"]["forest"]

    with (
        patch.object(h6199, "send_command", new_callable=AsyncMock) as send,
        patch.object(h6199, "refresh_state", new_callable=AsyncMock, return_value=True) as refresh,
    ):
        recovered = await h6199.async_restore_effect_control_state(
            state,
            overwritten_diy_code=None,
        )

    assert recovered is (not bool(scene.param))
    assert send.await_args_list == [
        call(build_power(True, "H6199")),
        call(build_brightness(72, "H6199")),
        call(build_scene_activation("H6199", scene.code, scene.music_code)),
    ]
    refresh.assert_awaited_once_with(expected_on=True, expected_brightness=72, expected_scene_code=scene.code)


async def test_restore_h6179_scene_converts_legacy_catalogue_code_to_wire_selector(hass):
    coordinator = GoveeBLECoordinator(
        hass,
        "22:33:44:55:66:78",
        "H6179",
        configuration_url=_CONFIGURATION_URL,
    )
    scene = MODEL_SCENES["H6179"]["karst cave"]
    selector = scene_selector_code("H6179", scene)
    state = PriorControlState(
        mode="scene",
        is_on=True,
        brightness_pct=72,
        rgb_color=(1, 2, 3),
        effect="karst cave",
        scene_code=scene.code,
    )

    with (
        patch.object(coordinator, "send_command", new_callable=AsyncMock) as send,
        patch.object(coordinator, "refresh_state", new_callable=AsyncMock, return_value=True) as refresh,
    ):
        recovered = await coordinator.async_restore_effect_control_state(state, overwritten_diy_code=None)

    assert recovered is False
    assert send.await_args_list == [
        call(build_power(True, "H6179")),
        call(build_brightness(72, "H6179")),
        call(build_scene_activation("H6179", selector, scene.music_code)),
    ]
    refresh.assert_awaited_once_with(expected_on=True, expected_brightness=72, expected_scene_code=selector)


async def test_restore_effect_control_state_maps_h617e_legacy_scene_name(hass):
    coordinator = GoveeBLECoordinator(
        hass,
        "22:33:44:55:66:78",
        "H617E",
        configuration_url=_CONFIGURATION_URL,
    )
    state = PriorControlState(
        mode="scene",
        is_on=True,
        brightness_pct=72,
        rgb_color=(1, 2, 3),
        effect="aurora",
    )
    scene = MODEL_SCENES["H617E"]["aurora-a"]

    with (
        patch.object(coordinator, "send_command", new_callable=AsyncMock) as send,
        patch.object(coordinator, "refresh_state", new_callable=AsyncMock, return_value=True) as refresh,
    ):
        assert await coordinator.async_restore_effect_control_state(state, overwritten_diy_code=None) is (
            not bool(scene.param)
        )

    assert send.await_args_list == [
        call(build_power(True, "H617E")),
        call(build_brightness(72, "H617E")),
        call(build_scene_activation("H617E", scene.code, scene.music_code)),
    ]
    refresh.assert_awaited_once_with(expected_on=True, expected_brightness=72, expected_scene_code=scene.code)


async def test_restore_effect_control_state_preserves_h617e_raw_legacy_scene(hass):
    coordinator = GoveeBLECoordinator(
        hass,
        "22:33:44:55:66:79",
        "H617E",
        configuration_url=_CONFIGURATION_URL,
    )
    exact_codes = {scene.code for scene in MODEL_SCENES["H617E"].values()}
    legacy_name, legacy = next(
        (name, scene) for name, scene in MODEL_SCENES["H617A"].items() if scene.code not in exact_codes
    )
    coordinator.is_on = True
    coordinator.color_mode = ParsedMode.SCENE
    coordinator._scene_code = legacy.code
    coordinator.effect = None
    state = coordinator.capture_effect_control_state()

    assert state.mode == "scene"
    assert state.scene_code == legacy.code
    with (
        patch.object(coordinator, "send_command", new_callable=AsyncMock) as send,
        patch.object(coordinator, "refresh_state", new_callable=AsyncMock, return_value=True) as refresh,
    ):
        assert await coordinator.async_restore_effect_control_state(state, overwritten_diy_code=None) is (
            not bool(legacy.param)
        )

    assert send.await_args_list == [
        call(build_power(True, "H617E")),
        call(build_brightness(state.brightness_pct, "H617E")),
        call(build_scene_activation("H617E", legacy.code, legacy.music_code)),
    ]
    refresh.assert_awaited_once_with(
        expected_on=True, expected_brightness=state.brightness_pct, expected_scene_code=legacy.code
    )


async def test_restore_effect_control_state_uses_legacy_identity_to_disambiguate_scene_code(hass):
    coordinator = GoveeBLECoordinator(
        hass,
        "22:33:44:55:66:80",
        "H617E",
        configuration_url=_CONFIGURATION_URL,
    )
    legacy = MODEL_SCENES["H617A"]["aurora"]
    state = PriorControlState(
        mode="scene",
        is_on=True,
        brightness_pct=72,
        rgb_color=(1, 2, 3),
        effect="aurora",
        scene_code=legacy.code,
    )

    with (
        patch.object(coordinator, "send_command", new_callable=AsyncMock) as send,
        patch.object(coordinator, "refresh_state", new_callable=AsyncMock, return_value=True),
    ):
        assert await coordinator.async_restore_effect_control_state(state, overwritten_diy_code=None) is (
            not bool(legacy.param)
        )

    assert send.await_args_list == [
        call(build_power(True, "H617E")),
        call(build_brightness(72, "H617E")),
        call(build_scene_activation("H617E", legacy.code, legacy.music_code)),
    ]

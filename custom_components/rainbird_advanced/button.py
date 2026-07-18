"""Button platform: stop all, advance zone, and run a program."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import RainbirdAdvEntity, RainbirdControlMixin
from .models import RainbirdAdvConfigEntry, RainbirdAdvData
from .program_infer import program_name

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: RainbirdAdvConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    data = entry.runtime_data
    entities: list[ButtonEntity] = [
        RainbirdStopButton(data),
        RainbirdAdvanceButton(data),
    ]
    entities.extend(
        RainbirdRunProgramButton(data, program)
        for program in range(data.model_info.model_info.max_programs)
    )
    async_add_entities(entities)


class RainbirdStopButton(RainbirdControlMixin, RainbirdAdvEntity, ButtonEntity):
    """Stop all irrigation."""

    _attr_translation_key = "stop"

    def __init__(self, data: RainbirdAdvData) -> None:
        """Initialize the button."""
        super().__init__(data)
        self._attr_unique_id = f"{data.mac_address}_stop"

    async def async_press(self) -> None:
        """Stop irrigation."""
        await self._async_control("stop", self._data.api.stop_irrigation())


class RainbirdAdvanceButton(RainbirdControlMixin, RainbirdAdvEntity, ButtonEntity):
    """Advance irrigation to the next zone."""

    _attr_translation_key = "advance"

    def __init__(self, data: RainbirdAdvData) -> None:
        """Initialize the button."""
        super().__init__(data)
        self._attr_unique_id = f"{data.mac_address}_advance"

    async def async_press(self) -> None:
        """Advance one zone."""
        await self._async_control("advance", self._data.api.advance_zone(1))


class RainbirdRunProgramButton(RainbirdControlMixin, RainbirdAdvEntity, ButtonEntity):
    """Manually run a full program (A, B, C ...)."""

    _attr_translation_key = "run_program"

    def __init__(self, data: RainbirdAdvData, program: int) -> None:
        """Initialize the button."""
        super().__init__(data)
        self._program = program
        letter = program_name(program).removeprefix("PGM ")
        self._attr_unique_id = f"{data.mac_address}_run_program_{program}"
        self._attr_translation_placeholders = {"program": letter}

    async def async_press(self) -> None:
        """Run the program."""
        await self._async_control(
            "run program", self._data.api.run_program(self._program)
        )

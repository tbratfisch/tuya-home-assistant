#!/usr/bin/env python3
"""Support for Tuya Fan."""
from __future__ import annotations

import json
import logging
from typing import Any
from math import ceil

from homeassistant.util.percentage import int_states_in_range, ranged_value_to_percentage, percentage_to_ranged_value

from homeassistant.components.fan import (
    DIRECTION_FORWARD,
    DIRECTION_REVERSE,
    DOMAIN as DEVICE_DOMAIN,
    SUPPORT_DIRECTION,
    SUPPORT_OSCILLATE,
    SUPPORT_PRESET_MODE,
    SUPPORT_SET_SPEED,
    FanEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .base import TuyaHaDevice
from .const import (
    DOMAIN,
    TUYA_DEVICE_MANAGER,
    TUYA_DISCOVERY_NEW,
    TUYA_HA_DEVICES,
    TUYA_HA_TUYA_MAP
)

_LOGGER = logging.getLogger(__name__)


# Fan
# https://developer.tuya.com/en/docs/iot/f?id=K9gf45vs7vkge
DPCODE_SWITCH = "switch"
DPCODE_MODE = "mode"
DPCODE_FAN_SPEED = "fan_speed_percent"
DPCODE_FAN_SPEED_LEGACY = "fan_speed"
DPCODE_SWITCH_HORIZONTAL = "switch_horizontal"
DPCODE_FAN_DIRECTION = "fan_direction"

# Air Purifier
# https://developer.tuya.com/en/docs/iot/s?id=K9gf48r41mn81
DPCODE_AP_FAN_SPEED = "speed"
DPCODE_AP_FAN_SPEED_ENUM = "fan_speed_enum"

TUYA_SUPPORT_TYPE = {
    "fs",    # Fan
    "kj",    # Air Purifier
}


async def async_setup_entry(
    hass: HomeAssistant, _entry: ConfigEntry, async_add_entities
):
    """Set up tuya fan dynamically through tuya discovery."""
    _LOGGER.info("fan init")

    hass.data[DOMAIN][TUYA_HA_TUYA_MAP].update(
        {DEVICE_DOMAIN: TUYA_SUPPORT_TYPE})

    async def async_discover_device(dev_ids):
        """Discover and add a discovered tuya fan."""
        _LOGGER.info(f"fan add-> {dev_ids}")
        if not dev_ids:
            return
        entities = await hass.async_add_executor_job(_setup_entities, hass, dev_ids)
        hass.data[DOMAIN][TUYA_HA_DEVICES].extend(entities)
        async_add_entities(entities)

    async_dispatcher_connect(
        hass, TUYA_DISCOVERY_NEW.format(DEVICE_DOMAIN), async_discover_device
    )

    device_manager = hass.data[DOMAIN][TUYA_DEVICE_MANAGER]
    device_ids = []
    for (device_id, device) in device_manager.device_map.items():
        if device.category in TUYA_SUPPORT_TYPE:
            device_ids.append(device_id)
    await async_discover_device(device_ids)


def _setup_entities(hass, device_ids: list):
    """Set up Tuya Fan."""
    device_manager = hass.data[DOMAIN][TUYA_DEVICE_MANAGER]
    entities = []
    for device_id in device_ids:
        device = device_manager.device_map[device_id]
        if device is None:
            continue
        entities.append(TuyaHaFan(device, device_manager))
    return entities


class TuyaHaFan(TuyaHaDevice, FanEntity):
    """Tuya Fan Device."""

    def __init__(self, device: TuyaDevice, device_manager: TuyaDeviceManager):
        """Init Tuya Fan Device."""
        super().__init__(device, device_manager)

        # Air purifier fan can be controlled either via the ranged values or via the enum.
        # We will always prefer the enumeration if available
        #   Enum is used for e.g. MEES SmartHIMOX-H06
        #   Range is used for e.g. Concept CA3000
        self.air_purifier_speed_range = (0, 0)
        self.air_purifier_speed_range_len = 0
        self.air_purifier_speed_range_enum = []
        self.legacy_speed_range = (0, 0)
        self.legacy_speed_range_len = 0
        self.legacy_speed_range_enum = []
        if self.tuya_device.category == "kj":
            try:
                if DPCODE_AP_FAN_SPEED_ENUM in self.tuya_device.status:
                    data = json.loads(self.tuya_device.function.get(
                        DPCODE_AP_FAN_SPEED_ENUM, {}).values).get("range")
                    if data:
                        self.air_purifier_speed_range = (1, len(data))
                        self.air_purifier_speed_range_len = len(data)
                        self.air_purifier_speed_range_enum = data
                elif DPCODE_AP_FAN_SPEED in self.tuya_device.status:
                    data = json.loads(self.tuya_device.function.get(
                        DPCODE_AP_FAN_SPEED, {}).values).get("range")
                    if data:
                        self.air_purifier_speed_range = (
                            int(data[0]), int(data[-1]))
                        self.air_purifier_speed_range_len = len(data)
            except:
                _LOGGER.warn("Cannot parse the air-purifier speed range")
        elif self.tuya_device.category == "fs":
            try:
                if DPCODE_FAN_SPEED_LEGACY in self.tuya_device.status:
                    data = json.loads(self.tuya_device.function.get(
                        DPCODE_FAN_SPEED_LEGACY, {}).values).get("range")
                    if data:
                        # the API reports an array between 1 and 26, but that seems to be wrong?!
                        # see https://developer.tuya.com/en/docs/iot/f?id=K9gf45vs7vkge
                        # self.legacy_speed_range = (1, len(data))
                        # self.legacy_speed_range_len = len(data)
                        # self.legacy_speed_range_enum = data
                        self.legacy_speed_range = (1, 4)
                        self.legacy_speed_range_len = 4
                        self.legacy_speed_range_enum = ['1', '2', '3', '4'] 
            except:
                _LOGGER.warn("Cannot parse the legacy fan speed range")

    def set_preset_mode(self, preset_mode: str) -> None:
        """Set the preset mode of the fan."""
        self._send_command([{"code": DPCODE_MODE, "value": preset_mode}])

    def set_direction(self, direction: str) -> None:
        """Set the direction of the fan."""
        self._send_command(
            [{"code": DPCODE_FAN_DIRECTION, "value": direction}])

    def set_percentage(self, percentage: int) -> None:
        """Set the speed of the fan, as a percentage."""
        if self.tuya_device.category == "kj":
            value_in_range = ceil(percentage_to_ranged_value(
                self.air_purifier_speed_range, percentage))
            if len(self.air_purifier_speed_range_enum):
                # if air-purifier speed enumeration is supported we will prefer it.
                self._send_command([{"code": DPCODE_AP_FAN_SPEED_ENUM, "value": str(
                    self.air_purifier_speed_range_enum[value_in_range - 1])}])
            else:
                self._send_command(
                    [{"code": DPCODE_AP_FAN_SPEED, "value": str(value_in_range)}])
        elif self.tuya_device.category == "fs" and self.legacy_speed_range_len > 1:
            value_in_range = ceil(percentage_to_ranged_value(self.legacy_speed_range, percentage))
            if len(self.legacy_speed_range_enum):
                # if speed enumeration is supported we will prefer it.
                self._send_command([{"code": DPCODE_FAN_SPEED_LEGACY, "value": str(self.legacy_speed_range_enum[value_in_range - 1])}])
            else:
                self._send_command([{"code": DPCODE_FAN_SPEED, "value": str(value_in_range)}])
        else:
            super().set_percentage(percentage)

    def turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        self._send_command([{"code": DPCODE_SWITCH, "value": False}])

    def turn_on(
        self,
        speed: str = None,
        percentage: int = None,
        preset_mode: str = None,
        **kwargs: Any,
    ) -> None:
        """Turn on the fan."""
        self._send_command([{"code": DPCODE_SWITCH, "value": True}])

    def oscillate(self, oscillating: bool) -> None:
        """Oscillate the fan."""
        self._send_command(
            [{"code": DPCODE_SWITCH_HORIZONTAL, "value": oscillating}])

    # property
    @property
    def is_on(self) -> bool:
        """Return true if fan is on."""
        return self.tuya_device.status.get(DPCODE_SWITCH, False)

    @property
    def current_direction(self) -> str:
        """Return the current direction of the fan."""
        return (
            DIRECTION_FORWARD
            if self.tuya_device.status.get(DPCODE_FAN_DIRECTION)
            else DIRECTION_REVERSE
        )

    @property
    def oscillating(self) -> bool:
        """Return true if the fan is oscillating."""
        return self.tuya_device.status.get(DPCODE_SWITCH_HORIZONTAL, False)

    @property
    def preset_modes(self) -> list:
        """Return the list of available preset_modes."""
        try:
            data = json.loads(self.tuya_device.function.get(DPCODE_MODE, {}).values).get(
                "range"
            )
            return data
        except:
            _LOGGER.warn("Cannot parse the preset modes")
        return []

    @property
    def preset_mode(self) -> str:
        """Return the current preset_mode."""
        return self.tuya_device.status.get(DPCODE_MODE)

    @property
    def percentage(self) -> int:
        """Return the current speed."""
        if not self.is_on:
            return 0

        if self.tuya_device.category == "kj":
            if self.air_purifier_speed_range_len > 1:
                if len(self.air_purifier_speed_range_enum):
                    # if air-purifier speed enumeration is supported we will prefer it.
                    index = self.air_purifier_speed_range_enum.index(
                        self.tuya_device.status.get(
                            DPCODE_AP_FAN_SPEED_ENUM, 0)
                    )
                    return ranged_value_to_percentage(self.air_purifier_speed_range,
                                                      index + 1
                                                      )
                else:
                    return ranged_value_to_percentage(self.air_purifier_speed_range,
                                                      int(self.tuya_device.status.get(DPCODE_AP_FAN_SPEED, 0)
                                                          )
                                                      )
        elif self.tuya_device.category == "fs":
            if self.legacy_speed_range_len > 1:
                if len(self.legacy_speed_range_enum):
                    # if speed enumeration is supported we will prefer it.
                    if int(self.tuya_device.status.get(DPCODE_FAN_SPEED_LEGACY, 0)) == 0:
                        return 0
                    index = self.legacy_speed_range_enum.index(
                        self.tuya_device.status.get(
                            DPCODE_FAN_SPEED_LEGACY, 0)
                    )
                    return ranged_value_to_percentage(self.legacy_speed_range, index + 1)
                else:
                    return ranged_value_to_percentage(self.legacy_speed_range,
                                                      int(self.tuya_device.status.get(DPCODE_AP_FAN_SPEED_LEGACY, 0)
                                                          )
                                                      )
            else:
                return self.tuya_device.status.get(DPCODE_FAN_SPEED, 0)

    @property
    def speed_count(self) -> int:
        """Return the number of speeds the fan supports."""
        if self.tuya_device.category == "kj":
            return self.air_purifier_speed_range_len
        elif self.tuya_device.category == "fs" and self.legacy_speed_range_len > 1:
            return self.legacy_speed_range_len
        return super().speed_count()

    @property
    def supported_features(self):
        """Flag supported features."""
        supports = 0
        if DPCODE_MODE in self.tuya_device.status:
            supports = supports | SUPPORT_PRESET_MODE
        if DPCODE_FAN_SPEED in self.tuya_device.status:
            supports = supports | SUPPORT_SET_SPEED
        if DPCODE_FAN_SPEED_LEGACY in self.tuya_device.status:
            supports = supports | SUPPORT_SET_SPEED
        if DPCODE_SWITCH_HORIZONTAL in self.tuya_device.status:
            supports = supports | SUPPORT_OSCILLATE
        if DPCODE_FAN_DIRECTION in self.tuya_device.status:
            supports = supports | SUPPORT_DIRECTION

        # Air Purifier specific
        if DPCODE_AP_FAN_SPEED in self.tuya_device.status or DPCODE_AP_FAN_SPEED_ENUM in self.tuya_device.status:
            supports = supports | SUPPORT_SET_SPEED
        return supports

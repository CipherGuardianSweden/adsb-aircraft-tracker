"""Config flow for ADSB Aircraft Tracker integration."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    DOMAIN,
    CONF_ADSB_HOST,
    CONF_ADSB_PORT,
    CONF_ADSB_FILE_PATH,
    CONF_DATA_SOURCE,
    CONF_UPDATE_INTERVAL,
    CONF_DISTANCE_LIMIT,
    CONF_NOTIFICATION_DEVICE,
    CONF_EXTERNAL_URL,
    CONF_MILITARY_NOTIFICATIONS,
    CONF_CLOSE_AIRCRAFT_ENABLED,
    CONF_CLOSE_AIRCRAFT_DISTANCE,
    CONF_CLOSE_AIRCRAFT_ALTITUDE,
    CONF_EMERGENCY_NOTIFICATIONS,
    DATA_SOURCE_HTTP,
    DATA_SOURCE_LOCAL_FILE,
    DEFAULT_ADSB_PORT,
    DEFAULT_ADSB_FILE_PATH,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_DISTANCE_LIMIT,
    DEFAULT_MILITARY_NOTIFICATIONS,
    DEFAULT_CLOSE_AIRCRAFT_ENABLED,
    DEFAULT_CLOSE_AIRCRAFT_DISTANCE,
    DEFAULT_CLOSE_AIRCRAFT_ALTITUDE,
    DEFAULT_EMERGENCY_NOTIFICATIONS,
)

_LOGGER = logging.getLogger(__name__)


def get_user_data_schema(hass=None):
    """Get the user data schema."""
    distance_unit = "miles"
    max_distance = 1000

    if hass and hass.config.units.length == "km":
        distance_unit = "kilometers"
        max_distance = 1600

    return vol.Schema(
        {
            vol.Required(
                CONF_DATA_SOURCE,
                default=DATA_SOURCE_LOCAL_FILE,
            ): vol.In(
                {
                    DATA_SOURCE_LOCAL_FILE: "📁 Local file",
                    DATA_SOURCE_HTTP: "🌐 HTTP",
                }
            ),
            vol.Optional(
                CONF_ADSB_FILE_PATH,
                default=DEFAULT_ADSB_FILE_PATH,
            ): str,
            vol.Optional(
                CONF_ADSB_HOST,
                default="",
            ): str,
            vol.Optional(
                CONF_ADSB_PORT,
                default=DEFAULT_ADSB_PORT,
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=1, max=65535),
            ),
            vol.Optional(
                CONF_UPDATE_INTERVAL,
                default=DEFAULT_UPDATE_INTERVAL,
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=5, max=300),
            ),
            vol.Optional(
                CONF_DISTANCE_LIMIT,
                default=DEFAULT_DISTANCE_LIMIT,
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=0, max=max_distance),
            ),
        }
    )


async def validate_input(
    hass: Any,
    data: dict[str, Any],
) -> dict[str, Any]:
    """Validate the user input."""

    data_source = data.get(CONF_DATA_SOURCE, DATA_SOURCE_LOCAL_FILE)

    if data_source == DATA_SOURCE_LOCAL_FILE:
        file_path = data.get(
            CONF_ADSB_FILE_PATH,
            DEFAULT_ADSB_FILE_PATH,
        ).strip()

        try:
            file_data = await hass.async_add_executor_job(
                _read_aircraft_file,
                file_path,
            )
        except FileNotFoundError:
            raise InvalidHost(
                f"File not found: {file_path}"
            )
        except PermissionError:
            raise InvalidHost(
                f"Permission denied: {file_path}"
            )
        except json.JSONDecodeError as err:
            raise InvalidADSBData(
                f"Invalid JSON in {file_path}: {err}"
            )
        except OSError as err:
            raise InvalidHost(
                f"Cannot read {file_path}: {err}"
            )

        if "aircraft" not in file_data:
            raise InvalidADSBData(
                "Missing aircraft data in file"
            )

        if not isinstance(file_data["aircraft"], list):
            raise InvalidADSBData(
                "Aircraft data is not a list"
            )

        return {
            "title": "ADSB Tracker (Local file)",
            "aircraft_count": len(file_data["aircraft"]),
            "last_update": file_data.get("now"),
        }

    host = data.get(CONF_ADSB_HOST, "").strip()
    port = data.get(CONF_ADSB_PORT, DEFAULT_ADSB_PORT)

    if not host:
        raise InvalidHost("Hostname is required for HTTP mode")

    url = f"http://{host}:{port}/data/aircraft.json"

    session = async_get_clientsession(hass)

    try:
        async with asyncio.timeout(10):
            async with session.get(url) as response:
                if response.status != 200:
                    raise InvalidHost(f"HTTP {response.status}")

                json_data = await response.json()

                if "aircraft" not in json_data:
                    raise InvalidADSBData(
                        "Missing aircraft data in response"
                    )

                if not isinstance(json_data["aircraft"], list):
                    raise InvalidADSBData(
                        "Aircraft data is not a list"
                    )

                return {
                    "title": f"ADSB Tracker ({host}:{port})",
                    "aircraft_count": len(json_data["aircraft"]),
                    "last_update": json_data.get("now"),
                }

    except asyncio.TimeoutError:
        raise ConnectionTimeout(
            f"Timed out connecting to {host}:{port}"
        )

    except aiohttp.ClientConnectorError as err:
        if isinstance(err.os_error, ConnectionRefusedError):
            raise ConnectionRefused(
                f"Connection refused by {host}:{port}"
            )

        err_msg = str(err).lower()

        if (
            "not found" in err_msg
            or "not known" in err_msg
            or "nodename" in err_msg
        ):
            raise CannotResolve(
                f"Cannot resolve hostname: {host}"
            )

        raise CannotConnect(
            f"Network error connecting to {host}:{port}: {err}"
        )

    except aiohttp.ClientError as err:
        raise CannotConnect(
            f"Network error connecting to {host}:{port}: {err}"
        )

    except (InvalidHost, InvalidADSBData):
        raise

    except Exception as err:
        _LOGGER.exception(
            "Unexpected error validating ADSB connection"
        )
        raise CannotConnect(
            f"Unexpected error: {err}"
        )


def _read_aircraft_file(file_path: str) -> dict[str, Any]:
    """Read and parse the local aircraft JSON file."""
    with open(file_path, "r", encoding="utf-8") as file:
        return json.load(file)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for ADSB Aircraft Tracker."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Create the options flow."""
        return OptionsFlowHandler()

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the initial step."""

        errors: dict[str, str] = {}

        if user_input is not None:
            try:
                info = await validate_input(
                    self.hass,
                    user_input,
                )

                data_source = user_input.get(
                    CONF_DATA_SOURCE,
                    DATA_SOURCE_LOCAL_FILE,
                )

                if data_source == DATA_SOURCE_LOCAL_FILE:
                    unique_id = (
                        f"local_file:"
                        f"{user_input.get(CONF_ADSB_FILE_PATH, DEFAULT_ADSB_FILE_PATH)}"
                    )
                else:
                    host = user_input[CONF_ADSB_HOST]
                    port = user_input[CONF_ADSB_PORT]
                    unique_id = f"{host}:{port}"

                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                self._user_input = user_input
                self._title = info["title"]
                self._validation_info = info

                return await self.async_step_summary()

            except ConnectionRefused:
                errors["base"] = "connection_refused"

            except ConnectionTimeout:
                errors["base"] = "timeout"

            except CannotResolve:
                errors[CONF_ADSB_HOST] = "cannot_resolve"

            except CannotConnect:
                errors["base"] = "cannot_connect"

            except InvalidHost:
                errors["base"] = "invalid_host"

            except InvalidADSBData:
                errors["base"] = "invalid_adsb_data"

            except Exception:
                _LOGGER.exception(
                    "Unexpected exception"
                )
                errors["base"] = "unknown"

        return self.async_show_form(
            step_id="user",
            data_schema=get_user_data_schema(self.hass),
            errors=errors,
        )

    async def async_step_summary(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Show connection summary and setup choice."""

        if user_input is not None:
            if user_input.get("setup_choice") == "configure_now":
                return await self.async_step_notifications()

            return self.async_create_entry(
                title=self._title,
                data=self._user_input,
            )

        info = self._validation_info
        aircraft_count = info.get("aircraft_count", 0)
        last_update = info.get("last_update")

        data_source = self._user_input.get(
            CONF_DATA_SOURCE,
            DATA_SOURCE_LOCAL_FILE,
        )

        if data_source == DATA_SOURCE_LOCAL_FILE:
            source_text = (
                f"📁 **Source:** "
                f"{self._user_input.get(CONF_ADSB_FILE_PATH, DEFAULT_ADSB_FILE_PATH)}"
            )
        else:
            source_text = (
                f"📡 **Source:** "
                f"{self._user_input.get(CONF_ADSB_HOST)}:"
                f"{self._user_input.get(CONF_ADSB_PORT)}"
            )

        summary_text = (
            "✅ **Connection Successful!**\n\n"
            f"🛩️ **Found:** {aircraft_count} aircraft currently tracked\n"
        )

        if last_update:
            summary_text += (
                f"🕐 **Last Update:** {last_update}\n"
            )

        summary_text += (
            f"{source_text}\n\n"
            "You can set up notifications and alerts now, "
            "or configure them later in the integration options."
        )

        schema = vol.Schema(
            {
                vol.Required(
                    "setup_choice",
                    default="configure_later",
                ): vol.In(
                    {
                        "configure_now": (
                            "Configure notifications now"
                        ),
                        "configure_later": (
                            "Finish setup "
                            "(configure notifications later)"
                        ),
                    }
                )
            }
        )

        return self.async_show_form(
            step_id="summary",
            data_schema=schema,
            description_placeholders={
                "summary_text": summary_text
            },
        )

    async def async_step_notifications(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle notification configuration during initial setup."""

        if user_input is not None:
            final_data = {
                **self._user_input,
                **user_input,
            }

            return self.async_create_entry(
                title=self._title,
                data=final_data,
            )

        notification_devices = (
            self._get_notification_devices()
        )

        notifications_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_NOTIFICATION_DEVICE,
                    default="",
                ): vol.In(
                    [""] + notification_devices
                ),
                vol.Optional(
                    CONF_EXTERNAL_URL,
                    default="",
                ): str,
                vol.Optional(
                    CONF_MILITARY_NOTIFICATIONS,
                    default=DEFAULT_MILITARY_NOTIFICATIONS,
                ): bool,
                vol.Optional(
                    CONF_EMERGENCY_NOTIFICATIONS,
                    default=DEFAULT_EMERGENCY_NOTIFICATIONS,
                ): bool,
                vol.Optional(
                    CONF_CLOSE_AIRCRAFT_ENABLED,
                    default=DEFAULT_CLOSE_AIRCRAFT_ENABLED,
                ): bool,
            }
        )

        return self.async_show_form(
            step_id="notifications",
            data_schema=notifications_schema,
        )

    def _get_notification_devices(self) -> list[str]:
        """Get available mobile app notification devices."""

        devices = []

        for domain, services in (
            self.hass.services.async_services().items()
        ):
            if domain == "notify":
                for service_name in services:
                    if service_name.startswith(
                        "mobile_app_"
                    ):
                        devices.append(service_name)

        return sorted(devices)


class CannotConnect(Exception):
    """Error to indicate we cannot connect."""


class ConnectionRefused(Exception):
    """Error to indicate the connection was actively refused."""


class ConnectionTimeout(Exception):
    """Error to indicate the connection timed out."""


class CannotResolve(Exception):
    """Error to indicate hostname resolution failed."""


class InvalidHost(Exception):
    """Error to indicate there is an invalid hostname."""


class InvalidADSBData(Exception):
    """Error to indicate invalid ADSB data format."""


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for ADSB Aircraft Tracker."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle the initial options step."""

        if user_input is not None:
            if user_input.get("config_area") == "basic":
                return await self.async_step_basic_settings()

            return await self.async_step_notifications_settings()

        schema = vol.Schema(
            {
                vol.Required(
                    "config_area",
                    default="basic",
                ): vol.In(
                    {
                        "basic": (
                            "📊 Basic Settings "
                            "(connection, updates, range)"
                        ),
                        "notifications": (
                            "🔔 Notifications & Alerts"
                        ),
                    }
                )
            }
        )

        current_source = self.config_entry.data.get(
            CONF_DATA_SOURCE,
            DATA_SOURCE_LOCAL_FILE,
        )

        current_host = self.config_entry.data.get(
            CONF_ADSB_HOST,
            "unknown",
        )

        current_port = self.config_entry.data.get(
            CONF_ADSB_PORT,
            DEFAULT_ADSB_PORT,
        )

        current_file = self.config_entry.data.get(
            CONF_ADSB_FILE_PATH,
            DEFAULT_ADSB_FILE_PATH,
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            description_placeholders={
                "current_source": current_source,
                "current_host": current_host,
                "current_port": str(current_port),
                "current_file": current_file,
            },
        )

    async def async_step_basic_settings(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle basic connection and data settings."""

        errors: dict[str, str] = {}

        if user_input is not None:
            if (
                self.hass.config.units.length == "km"
                and user_input.get(CONF_DISTANCE_LIMIT, 0) > 0
            ):
                user_input[CONF_DISTANCE_LIMIT] = int(
                    user_input[CONF_DISTANCE_LIMIT] / 1.60934
                )

            return self.async_create_entry(
                title="",
                data={
                    **self.config_entry.options,
                    **user_input,
                },
            )

        current_data = self.config_entry.data
        current_options = self.config_entry.options

        current_update = current_options.get(
            CONF_UPDATE_INTERVAL,
            current_data.get(
                CONF_UPDATE_INTERVAL,
                DEFAULT_UPDATE_INTERVAL,
            ),
        )

        current_distance = current_options.get(
            CONF_DISTANCE_LIMIT,
            current_data.get(
                CONF_DISTANCE_LIMIT,
                DEFAULT_DISTANCE_LIMIT,
            ),
        )

        distance_unit = "miles"
        max_distance = 1000

        if self.hass.config.units.length == "km":
            distance_unit = "kilometers"
            max_distance = 1600

            if current_distance > 0:
                current_distance = int(
                    current_distance * 1.60934
                )

        basic_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=current_update,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=5, max=300),
                ),
                vol.Optional(
                    CONF_DISTANCE_LIMIT,
                    default=current_distance,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=0,
                        max=max_distance,
                    ),
                ),
            }
        )

        return self.async_show_form(
            step_id="basic_settings",
            data_schema=basic_schema,
            errors=errors,
        )

    async def async_step_notifications_settings(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle notification and alert settings."""

        errors: dict[str, str] = {}

        if user_input is not None:
            if (
                self.hass.config.units.length == "km"
                and user_input.get(
                    CONF_CLOSE_AIRCRAFT_DISTANCE,
                    0,
                ) > 0
            ):
                user_input[
                    CONF_CLOSE_AIRCRAFT_DISTANCE
                ] = (
                    user_input[
                        CONF_CLOSE_AIRCRAFT_DISTANCE
                    ] / 1.60934
                )

            return self.async_create_entry(
                title="",
                data={
                    **self.config_entry.options,
                    **user_input,
                },
            )

        current_data = self.config_entry.data
        current_options = self.config_entry.options

        notification_devices = (
            self._get_notification_devices()
        )

        current_device = current_options.get(
            CONF_NOTIFICATION_DEVICE,
            "",
        )

        current_url = current_options.get(
            CONF_EXTERNAL_URL,
            "",
        )

        current_military = current_options.get(
            CONF_MILITARY_NOTIFICATIONS,
            current_data.get(
                CONF_MILITARY_NOTIFICATIONS,
                DEFAULT_MILITARY_NOTIFICATIONS,
            ),
        )

        current_close_enabled = current_options.get(
            CONF_CLOSE_AIRCRAFT_ENABLED,
            current_data.get(
                CONF_CLOSE_AIRCRAFT_ENABLED,
                DEFAULT_CLOSE_AIRCRAFT_ENABLED,
            ),
        )

        current_close_distance = current_options.get(
            CONF_CLOSE_AIRCRAFT_DISTANCE,
            current_data.get(
                CONF_CLOSE_AIRCRAFT_DISTANCE,
                DEFAULT_CLOSE_AIRCRAFT_DISTANCE,
            ),
        )

        current_close_altitude = current_options.get(
            CONF_CLOSE_AIRCRAFT_ALTITUDE,
            current_data.get(
                CONF_CLOSE_AIRCRAFT_ALTITUDE,
                DEFAULT_CLOSE_AIRCRAFT_ALTITUDE,
            ),
        )

        current_emergency = current_options.get(
            CONF_EMERGENCY_NOTIFICATIONS,
            current_data.get(
                CONF_EMERGENCY_NOTIFICATIONS,
                DEFAULT_EMERGENCY_NOTIFICATIONS,
            ),
        )

        close_distance_display = current_close_distance

        if self.hass.config.units.length == "km":
            close_distance_display = (
                current_close_distance * 1.60934
            )

        notifications_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_NOTIFICATION_DEVICE,
                    default=current_device,
                ): vol.In(
                    [""] + notification_devices
                ),
                vol.Optional(
                    CONF_EXTERNAL_URL,
                    default=current_url,
                ): str,
                vol.Optional(
                    CONF_MILITARY_NOTIFICATIONS,
                    default=current_military,
                ): bool,
                vol.Optional(
                    CONF_CLOSE_AIRCRAFT_ENABLED,
                    default=current_close_enabled,
                ): bool,
                vol.Optional(
                    CONF_CLOSE_AIRCRAFT_DISTANCE,
                    default=close_distance_display,
                ): vol.All(
                    vol.Coerce(float),
                    vol.Range(
                        min=0.1,
                        max=50,
                    ),
                ),
                vol.Optional(
                    CONF_CLOSE_AIRCRAFT_ALTITUDE,
                    default=current_close_altitude,
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(
                        min=100,
                        max=10000,
                    ),
                ),
                vol.Optional(
                    CONF_EMERGENCY_NOTIFICATIONS,
                    default=current_emergency,
                ): bool,
            }
        )

        return self.async_show_form(
            step_id="notifications_settings",
            data_schema=notifications_schema,
            errors=errors,
        )

    def _get_notification_devices(self) -> list[str]:
        """Get available mobile app notification devices."""

        devices = []

        for domain, services in (
            self.hass.services.async_services().items()
        ):
            if domain == "notify":
                for service_name in services:
                    if service_name.startswith(
                        "mobile_app_"
                    ):
                        devices.append(service_name)

        return sorted(devices)

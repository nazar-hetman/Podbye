"""Mock startup data for Vigil prototype."""

STARTUPS = [
    {
        "program": "Adobe Creative Cloud",
        "publisher": "Adobe Inc.",
        "cpu_start": "4.2%",
        "ram_idle": "214 MB",
        "boot_delay": "+2.1s",
        "recommendation": "Disable",
        "why": "Heavy resource usage at boot. Launch on demand instead. Creative Cloud "
               "updater can run separately when needed.",
        "enabled": True,
    },
    {
        "program": "NVIDIA Container",
        "publisher": "NVIDIA Corporation",
        "cpu_start": "1.1%",
        "ram_idle": "84 MB",
        "boot_delay": "+0.4s",
        "recommendation": "Keep",
        "why": "Required for GPU driver functionality. Minimal impact. Disabling may "
               "affect display and CUDA operations.",
        "enabled": True,
    },
    {
        "program": "Steam Client Bootstrap",
        "publisher": "Valve Corporation",
        "cpu_start": "6.8%",
        "ram_idle": "312 MB",
        "boot_delay": "+3.4s",
        "recommendation": "Delay",
        "why": "Largest startup contributor. Delaying by 30s keeps Steam available "
               "without blocking login.",
        "enabled": True,
    },
    {
        "program": "Spotify",
        "publisher": "Spotify AB",
        "cpu_start": "0.8%",
        "ram_idle": "168 MB",
        "boot_delay": "+1.2s",
        "recommendation": "Disable",
        "why": "Music streaming not needed at boot. Launches quickly on demand. "
               "Background process consumes RAM unnecessarily.",
        "enabled": True,
    },
    {
        "program": "Synaptics TouchPad",
        "publisher": "Synaptics Inc.",
        "cpu_start": "0.2%",
        "ram_idle": "24 MB",
        "boot_delay": "+0.1s",
        "recommendation": "Keep",
        "why": "Touchpad driver component. Negligible resource impact. Required for "
               "advanced touchpad gestures and configuration.",
        "enabled": True,
    },
    {
        "program": "OneDrive",
        "publisher": "Microsoft Corporation",
        "cpu_start": "1.6%",
        "ram_idle": "142 MB",
        "boot_delay": "+0.9s",
        "recommendation": "Keep",
        "why": "Cloud sync starts quickly and ensures file availability. "
               "Moderate footprint, important for document access.",
        "enabled": True,
    },
    {
        "program": "Discord",
        "publisher": "Discord Inc.",
        "cpu_start": "2.4%",
        "ram_idle": "256 MB",
        "boot_delay": "+1.6s",
        "recommendation": "Keep Off",
        "why": "Currently disabled. Chat client not needed at startup. Electron-based, "
               "relatively heavy. Open manually when needed.",
        "enabled": False,
    },
]

STARTUP_SUMMARY = {
    "boot_impact": "8.1",
    "active_entries": "6 of 7",
    "ram_at_idle": "924",
    "disable_count": 2,
    "delay_count": 1,
    "keep_count": 3,
    "keep_off_count": 1,
}

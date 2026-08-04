#!/usr/bin/env python3
"""
Daemon configuration for Kuza-v2.

Loads configuration from KUZA_STATE_DIR/config.json (default: ~/.kuza-v2).
Provides defaults for all settings.
"""

import copy
import json
from pathlib import Path
from typing import Dict, Any, Optional
from utils.config import KUZA_STATE_DIR

# Configuration directory
CONFIG_DIR = KUZA_STATE_DIR
CONFIG_FILE = CONFIG_DIR / "config.json"

# Default configuration
DEFAULT_CONFIG: Dict[str, Any] = {
    # Daemon settings
    "daemon": {
        "pid_file": str(KUZA_STATE_DIR / "kuza-v2.pid"),
        "socket_file": str(KUZA_STATE_DIR / "kuza-v2.sock"),
        "log_file": str(KUZA_STATE_DIR / "kuza-v2.log"),
        "log_level": "INFO",  # DEBUG, INFO, WARNING, ERROR
    },
    
    # Task processing settings
    "tasks": {
        "max_concurrent": 1,
        "task_timeout": 1800,  # 30 minutes
        "max_retries": 3,
    },
    
    # Health check settings
    "health": {
        "check_interval": 60,  # seconds
        "max_memory_mb": 1500,
        "stuck_task_threshold": 1800,  # 30 minutes
    },
    
    # State database settings
    "state": {
        "db_path": str(KUZA_STATE_DIR / "state.db"),
        "cleanup_old_actions_hours": 24,
    },
}


class DaemonConfig:
    """
    Daemon configuration manager.
    
    Loads from config file, falls back to defaults.
    Provides get/set methods for configuration access.
    """
    
    def __init__(self, config_file: Path = CONFIG_FILE):
        self.config_file = config_file
        self._config: Dict[str, Any] = self._load_config()
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file or return defaults."""
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    user_config = json.load(f)
                # Merge with defaults
                return self._merge_configs(DEFAULT_CONFIG, user_config)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Warning: Could not load config file: {e}")
                return copy.deepcopy(DEFAULT_CONFIG)
        return copy.deepcopy(DEFAULT_CONFIG)
    
    def _merge_configs(self, base: Dict, override: Dict) -> Dict:
        """Recursively merge override config into base config."""
        result = copy.deepcopy(base)
        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key] = self._merge_configs(result[key], value)
            else:
                result[key] = value
        return result
    
    def get(self, *keys: str, default: Any = None) -> Any:
        """
        Get a configuration value by nested keys.

        Example: config.get("daemon", "log_level")
        """
        current = self._config
        for key in keys:
            if isinstance(current, dict) and key in current:
                current = current[key]
            else:
                return default
        
        # Expand tilde paths for string values
        if isinstance(current, str) and current.startswith("~"):
            return str(Path(current).expanduser())
        return current
    
    def set(self, *keys: str, value: Any):
        """
        Set a configuration value by nested keys.
        
        Example: config.set("daemon", "log_level", value="DEBUG")
        """
        current = self._config
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        current[keys[-1]] = value
    
    def save(self):
        """Save current configuration to file."""
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.config_file.parent.chmod(0o700)
        with open(self.config_file, 'w') as f:
            json.dump(self._config, f, indent=2)
        self.config_file.chmod(0o600)
    
    def create_default_config(self) -> Path:
        """Create a default config file if it doesn't exist."""
        self.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.config_file.parent.chmod(0o700)
        if not self.config_file.exists():
            with open(self.config_file, 'w') as f:
                json.dump(DEFAULT_CONFIG, f, indent=2)
        self.config_file.chmod(0o600)
        return self.config_file
    
    @property
    def all(self) -> Dict[str, Any]:
        """Get all configuration as a dictionary."""
        return self._config.copy()


# Global configuration instance
_config: Optional[DaemonConfig] = None


def get_config() -> DaemonConfig:
    """Get the global configuration instance."""
    global _config
    if _config is None:
        _config = DaemonConfig()
    return _config


def reset_config():
    """Reset the global configuration (for testing)."""
    global _config
    if _config:
        _config = None


def create_default_config() -> Path:
    """Create a default config file."""
    config = get_config()
    return config.create_default_config()

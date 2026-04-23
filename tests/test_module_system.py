"""Tests for the module system: ModuleRegistry, BNBAgentModule, BNBAgentConfig."""

import pytest

from bnbagent.config import BNBAgentConfig, NetworkConfig
from bnbagent.core.module import BNBAgentModule, ModuleInfo
from bnbagent.core.registry import ModuleRegistry


class DummyModule(BNBAgentModule):
    """Minimal test module."""

    def info(self) -> ModuleInfo:
        return ModuleInfo(
            name="dummy",
            version="0.1.0",
            description="Test module",
        )

    def default_config(self):
        return {"dummy.key": "value"}


class DependentModule(BNBAgentModule):
    """Module that depends on dummy."""

    def info(self) -> ModuleInfo:
        return ModuleInfo(
            name="dependent",
            version="0.1.0",
            description="Depends on dummy",
            dependencies=("dummy",),
        )

    def default_config(self):
        return {"dependent.key": "dep_value"}


class TestModuleInfo:
    def test_create_module_info(self):
        info = ModuleInfo(name="test", version="1.0.0", description="Test")
        assert info.name == "test"
        assert info.version == "1.0.0"
        assert info.dependencies == ()

    def test_module_info_is_frozen(self):
        info = ModuleInfo(name="test", version="1.0.0", description="Test")
        with pytest.raises(AttributeError):
            info.name = "changed"

    def test_module_info_with_dependencies(self):
        info = ModuleInfo(
            name="apex",
            version="0.1.0",
            description="APEX",
            dependencies=("erc8004",),
        )
        assert info.dependencies == ("erc8004",)


class TestModuleRegistry:
    def test_register_module(self):
        registry = ModuleRegistry()
        registry.register(DummyModule())
        assert "dummy" in registry.module_names

    def test_register_duplicate_raises(self):
        registry = ModuleRegistry()
        registry.register(DummyModule())
        with pytest.raises(ValueError, match="already registered"):
            registry.register(DummyModule())

    def test_get_module(self):
        registry = ModuleRegistry()
        mod = DummyModule()
        registry.register(mod)
        assert registry.get("dummy") is mod
        assert registry.get("nonexistent") is None

    def test_list_modules(self):
        registry = ModuleRegistry()
        registry.register(DummyModule())
        infos = registry.list_modules()
        assert len(infos) == 1
        assert infos[0].name == "dummy"

    def test_unregister(self):
        registry = ModuleRegistry()
        registry.register(DummyModule())
        registry.unregister("dummy")
        assert "dummy" not in registry.module_names

    def test_validate_dependencies_ok(self):
        registry = ModuleRegistry()
        registry.register(DummyModule())
        registry.register(DependentModule())
        errors = registry.validate_dependencies()
        assert errors == []

    def test_validate_dependencies_missing(self):
        registry = ModuleRegistry()
        registry.register(DependentModule())
        errors = registry.validate_dependencies()
        assert len(errors) == 1
        assert "dummy" in errors[0]

    def test_merge_default_configs(self):
        registry = ModuleRegistry()
        registry.register(DummyModule())
        registry.register(DependentModule())
        merged = registry.merge_default_configs()
        assert merged["dummy.key"] == "value"
        assert merged["dependent.key"] == "dep_value"

    def test_initialize_all(self):
        registry = ModuleRegistry()
        registry.register(DummyModule())
        registry.initialize_all({"extra": "config"})
        mod = registry.get("dummy")
        assert mod._config["dummy.key"] == "value"
        assert mod._config["extra"] == "config"

    def test_initialize_all_with_dependencies(self):
        registry = ModuleRegistry()
        registry.register(DependentModule())
        registry.register(DummyModule())
        registry.initialize_all()
        # Both should be initialized
        assert registry.get("dummy")._config is not None
        assert registry.get("dependent")._config is not None

    def test_initialize_missing_dependency_raises(self):
        from bnbagent.exceptions import ConfigurationError

        registry = ModuleRegistry()
        registry.register(DependentModule())
        with pytest.raises(ConfigurationError, match="dependency"):
            registry.initialize_all()

    def test_get_all_actions_empty(self):
        registry = ModuleRegistry()
        registry.register(DummyModule())
        actions = registry.get_all_actions()
        assert actions == []

    def test_shutdown_all(self):
        registry = ModuleRegistry()
        registry.register(DummyModule())
        registry.initialize_all()
        registry.shutdown_all()
        # Should not raise

    def test_discover_builtin_modules(self):
        registry = ModuleRegistry()
        registry.discover(include_entry_points=False)
        names = registry.module_names
        assert "erc8004" in names
        assert "apex" in names

    def test_topological_sort_order(self):
        registry = ModuleRegistry()
        registry.register(DependentModule())
        registry.register(DummyModule())
        order = registry._topological_sort()
        assert order.index("dummy") < order.index("dependent")


class TestBNBAgentConfig:
    def test_default_config(self):
        config = BNBAgentConfig()
        assert config.network == "bsc-testnet"
        assert config.wallet_provider is None

    def test_private_key_requires_password(self):
        with pytest.raises(ValueError, match="wallet_password is required"):
            BNBAgentConfig(private_key="0x" + "ab" * 32)

    def test_private_key_with_password_auto_wraps(self):
        config = BNBAgentConfig(
            private_key="0x" + "ab" * 32,
            wallet_password="test-pw",
        )
        assert config.wallet_provider is not None
        assert config.private_key == ""  # cleared

    def test_get_flat_key(self):
        config = BNBAgentConfig(settings={"debug": True})
        assert config.get("debug") is True
        assert config.get("missing", "default") == "default"

    def test_get_dotted_key(self):
        config = BNBAgentConfig(modules={"apex": {"erc8183": "0x123"}})
        assert config.get("apex.erc8183") == "0x123"
        assert config.get("apex.missing", "default") == "default"

    def test_to_flat_dict(self):
        from unittest.mock import MagicMock

        mock_wallet = MagicMock()
        config = BNBAgentConfig(
            network="bsc-testnet",
            wallet_provider=mock_wallet,
            settings={"debug": True},
            modules={"apex": {"evaluator": "0x456"}},
        )
        flat = config.to_flat_dict()
        assert flat["network"] == "bsc-testnet"
        assert flat["wallet_provider"] is mock_wallet
        assert flat["debug"] is True
        assert flat["apex.evaluator"] == "0x456"

    def test_to_flat_dict_no_plaintext_key(self):
        config = BNBAgentConfig(
            private_key="0x" + "ab" * 32,
            wallet_password="test-pw",
        )
        flat = config.to_flat_dict()
        assert "private_key" not in flat

    def test_network_config(self):
        config = BNBAgentConfig(network="bsc-testnet")
        nc = config.network_config
        assert isinstance(nc, NetworkConfig)
        assert nc.chain_id == 97

    def test_unknown_network_raises(self):
        config = BNBAgentConfig(network="unknown")
        with pytest.raises(ValueError, match="Unknown network"):
            _ = config.network_config

    def test_repr_with_wallet(self):
        config = BNBAgentConfig(
            private_key="0x" + "ab" * 32,
            wallet_password="test-pw",
        )
        r = repr(config)
        assert "wallet=" in r
        assert "ab" * 32 not in r  # no plaintext key

    def test_repr_without_wallet(self):
        config = BNBAgentConfig()
        r = repr(config)
        assert "wallet=None" in r

    def test_from_env_with_wallet(self, monkeypatch):
        monkeypatch.setenv("PRIVATE_KEY", "0x" + "ab" * 32)
        monkeypatch.setenv("WALLET_PASSWORD", "test-pw")
        config = BNBAgentConfig.from_env()
        assert config.wallet_provider is not None
        assert config.private_key == ""

    def test_from_env_no_key(self, monkeypatch):
        monkeypatch.delenv("PRIVATE_KEY", raising=False)
        monkeypatch.delenv("WALLET_PASSWORD", raising=False)
        config = BNBAgentConfig.from_env()
        assert config.wallet_provider is None


class TestBuiltinModules:
    """Verify that built-in modules have correct metadata."""

    def test_erc8004_module(self):
        from bnbagent.erc8004 import create_module

        mod = create_module()
        info = mod.info()
        assert info.name == "erc8004"
        assert info.dependencies == ()
        assert "registry_contract" in mod.default_config()

    def test_apex_module(self):
        from bnbagent.apex import create_module

        mod = create_module()
        info = mod.info()
        assert info.name == "apex"
        assert "erc8004" in info.dependencies
        config = mod.default_config()
        assert "commerce_contract" in config
        assert "router_contract" in config
        assert "policy_contract" in config
